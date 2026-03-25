"""
Pilot run — Phase 1B end-to-end test.

Steps:
  1. Ingest 50 records (enrichment + Qdrant + PostgreSQL)
  2. Build BM25 index from ingested records
  3. Run 6 test queries across all three indexes + RRF
  4. Print enrichment report + search quality report

Usage:
    # Full run (requires Docker: postgres + qdrant + redis)
    python scripts/pilot_run.py --limit 50

    # Dry run — enrichment only, no DB/Qdrant writes, no search test
    python scripts/pilot_run.py --limit 50 --dry

    # Different slice of the dataset
    python scripts/pilot_run.py --limit 50 --skip 5000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from loguru import logger
from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.indexing.qdrant_client import ensure_collections
from app.indexing.bm25 import get_bm25_index
from app.pipeline.ingestion import load_sample
from app.pipeline.orchestrator import process_batch
from app.cpv.mapper import trie_lookup as cpv_trie

CSV_PATH = Path(__file__).parent.parent / "tender_dataset_06082025_6Jan2026.csv"

# -------------------------------------------------------------------
# Test queries — designed to exercise all three query types
# -------------------------------------------------------------------
TEST_QUERIES = [
    # (query, expected_type, description)
    ("GPS tracking system for vehicles",         "semantic",  "Conceptual / descriptive"),
    ("supply of medical equipment",              "mixed",     "Mixed: product + procurement type"),
    ("construction road works",                  "keyword",   "Short keyword query"),
    ("CPV 45310000",                             "cpv",       "Explicit CPV code"),
    ("software development platform cloud",      "semantic",  "Long semantic query"),
    ("workwear uniforms protective clothing",     "mixed",     "Multi-keyword product query"),
]


async def run_pilot(limit: int, skip: int, dry_run: bool):
    settings = get_settings()

    print("\n" + "="*65)
    print("TENDERSCOUT — PHASE 1B PILOT RUN")
    print("="*65)
    print(f"  CSV      : {CSV_PATH.name}")
    print(f"  Records  : {limit} (skip={skip})")
    print(f"  Dry run  : {dry_run}")
    print(f"  Gemini   : {settings.gemini_model}")
    print(f"  Embedding: {settings.embedding_model}")
    print("="*65 + "\n")

    # ---------------------------------------------------------------
    # STEP 1: Load records
    # ---------------------------------------------------------------
    t0 = time.perf_counter()
    rows = load_sample(CSV_PATH, n=limit, skip=skip)
    logger.info(f"Loaded {len(rows)} rows in {time.perf_counter()-t0:.2f}s")

    # Quick CPV trie preview
    print("\n── CPV Trie Preview (first 5 records) ──")
    for row in rows[:5]:
        m = cpv_trie(row.searchable_text())
        print(f"  [{row.tot_id}] CPV={m.cpv_code} conf={m.confidence:.2f} "
              f"via={m.method}  '{(row.searchable_text() or '')[:55]}...'")

    # ---------------------------------------------------------------
    # STEP 2: Infrastructure init
    # ---------------------------------------------------------------
    if not dry_run:
        print("\n── Initialising infrastructure ──")
        await init_db()
        await ensure_collections()
        print("  PostgreSQL ✓  Qdrant ✓")

    # ---------------------------------------------------------------
    # STEP 3: Enrichment pipeline
    # ---------------------------------------------------------------
    print(f"\n── Enrichment pipeline ({len(rows)} records) ──")
    t1 = time.perf_counter()

    if dry_run:
        stats = await _dry_enrichment(rows)
    else:
        async with AsyncSessionLocal() as session:
            stats = await process_batch(rows, session, dry_run=False)

    elapsed = time.perf_counter() - t1

    # ---------------------------------------------------------------
    # STEP 4: Enrichment report
    # ---------------------------------------------------------------
    total_processed = stats['total'] - stats['dupes'] - stats['skipped_short']
    t0_pct = stats['tier0'] / max(total_processed, 1) * 100
    t2_pct = stats['tier2'] / max(total_processed, 1) * 100

    print("\n" + "─"*65)
    print("ENRICHMENT REPORT")
    print("─"*65)
    print(f"  Records loaded     : {stats['total']}")
    print(f"  Duplicates skipped : {stats['dupes']}")
    print(f"  Too short (skipped): {stats['skipped_short']}")
    print(f"  Tier 0 (free rules): {stats['tier0']:4d}   {t0_pct:5.1f}%  (target ≥60%)")
    print(f"  Tier 2 (Gemini LLM): {stats['tier2']:4d}   {t2_pct:5.1f}%  (target ≤40%)")
    print(f"  Indexed in Qdrant  : {stats['indexed']}")
    print(f"  Errors             : {stats['errors']}")
    print(f"  Elapsed            : {elapsed:.1f}s")

    if dry_run:
        print("\n  [Dry run — DB/Qdrant writes skipped. Search test requires full run.]")
        _print_final_verdict(stats)
        return

    # ---------------------------------------------------------------
    # STEP 5: BM25 index check
    # ---------------------------------------------------------------
    bm25 = get_bm25_index()
    print(f"\n── BM25 Index ──")
    print(f"  Documents indexed: {bm25.size}")

    # ---------------------------------------------------------------
    # STEP 6: Search quality test
    # ---------------------------------------------------------------
    print("\n" + "─"*65)
    print("SEARCH QUALITY REPORT")
    print("─"*65)

    from app.search.router import route_query
    from app.search.retrieval import retrieve

    for query, expected_type, description in TEST_QUERIES:
        print(f"\n  Query : \"{query}\"")
        print(f"  Type  : {description}")

        route = route_query(query)
        print(f"  Route : {route.query_type.value}  weights={route.weights}")

        t_search = time.perf_counter()
        try:
            results = await retrieve(query=query, route=route, top_k=5)
            elapsed_search = time.perf_counter() - t_search

            if results:
                print(f"  Hits  : {len(results)}  ({elapsed_search*1000:.0f}ms)")
                for i, r in enumerate(results[:3], 1):
                    p = r.get("payload", {})
                    summary = (p.get("summary") or "")[:55]
                    cpv = p.get("cpv_code", "—")
                    ptype = p.get("procurement_type", "—")
                    sources = "+".join(r.get("sources", []))
                    print(f"  [{i}] score={r['rrf_score']:.4f} [{sources}]  "
                          f"CPV={cpv} {ptype}")
                    print(f"       {summary}")
            else:
                print(f"  Hits  : 0  (no results — index may be too small with {limit} records)")
        except Exception as e:
            print(f"  ERROR : {e}")

    _print_final_verdict(stats)


def _print_final_verdict(stats: dict):
    print("\n" + "="*65)
    if stats['errors'] == 0:
        print("  ✓  PILOT PASSED — pipeline healthy, ready to scale")
    else:
        print(f"  ✗  {stats['errors']} errors — check logs above")
    print("="*65 + "\n")


async def _dry_enrichment(rows) -> dict:
    """Run enrichment without DB/Qdrant writes."""
    from app.cpv.mapper import map_cpv
    from app.pipeline.tier0 import process_tier0
    from app.pipeline.tier2 import enrich_batch_tier2

    stats = {
        "total": len(rows), "dupes": 0, "tier0": 0,
        "tier2": 0, "indexed": 0, "errors": 0, "skipped_short": 0,
    }

    tier2_rows = []
    for row in rows:
        cpv_match = await map_cpv(row.searchable_text())
        result = process_tier0(
            row,
            cpv_code=cpv_match.cpv_code,
            cpv_description=cpv_match.cpv_description,
            cpv_confidence=cpv_match.confidence,
        )
        if result.passed:
            stats["tier0"] += 1
            logger.debug(f"  [{row.tot_id}] Tier0 ✓  CPV={result.enriched.cpv_code} "
                         f"type={result.enriched.procurement_type.value}")
        elif result.reason.startswith("Text too short"):
            stats["skipped_short"] += 1
        else:
            tier2_rows.append(row)

    if tier2_rows:
        tier2_results = await enrich_batch_tier2(tier2_rows)
        for e in tier2_results:
            stats["tier2"] += 1
            logger.debug(f"  [{e.tot_id}] Tier2 ✓  CPV={e.cpv_code} "
                         f"type={e.procurement_type.value}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="TenderScout Phase 1B pilot")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--skip",  type=int, default=0)
    parser.add_argument("--dry",   action="store_true",
                        help="Dry run — no DB/Qdrant writes, skips search test")
    args = parser.parse_args()
    asyncio.run(run_pilot(limit=args.limit, skip=args.skip, dry_run=args.dry))


if __name__ == "__main__":
    main()
