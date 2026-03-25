"""
Full dataset ingestion — Phase 1C.

Streams the entire CSV through the enrichment pipeline with:
  - Progress bar (tqdm)
  - Checkpoint file for resumable ingestion
  - Per-chunk stats printed to stdout
  - Final summary

Usage:
    # Full run
    python scripts/ingest_full.py

    # Resume from a checkpoint (skip first N rows already processed)
    python scripts/ingest_full.py --resume

    # Custom slice (e.g. test on 500 records)
    python scripts/ingest_full.py --limit 500 --skip 0

    # Larger batches for faster throughput
    python scripts/ingest_full.py --batch-size 200

    # Dry run (no DB/Qdrant writes)
    python scripts/ingest_full.py --dry --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from loguru import logger

CSV_PATH = Path(__file__).parent.parent / "tender_dataset_06082025_6Jan2026.csv"
CHECKPOINT_FILE = Path(__file__).parent.parent / ".ingest_checkpoint.json"

# Estimated total rows (used for tqdm)
TOTAL_ROWS = 88_398


def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"rows_processed": 0, "cumulative": {
        "total": 0, "dupes": 0, "tier0": 0,
        "tier2": 0, "indexed": 0, "errors": 0, "skipped_short": 0,
    }}


def _save_checkpoint(rows_processed: int, cumulative: dict) -> None:
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"rows_processed": rows_processed, "cumulative": cumulative}, f, indent=2)


def _print_chunk_stats(chunk_num: int, stats: dict, elapsed: float, rows_so_far: int) -> None:
    rate = stats["total"] / elapsed if elapsed > 0 else 0
    eta_s = (TOTAL_ROWS - rows_so_far) / rate if rate > 0 else 0
    eta_min = eta_s / 60

    processed = stats["total"] - stats["dupes"] - stats["skipped_short"]
    t0_pct = stats["tier0"] / max(processed, 1) * 100
    t2_pct = stats["tier2"] / max(processed, 1) * 100

    print(
        f"\n  Chunk {chunk_num:4d} | "
        f"rows={stats['total']:3d}  "
        f"dupes={stats['dupes']:3d}  "
        f"t0={stats['tier0']:3d}({t0_pct:.0f}%)  "
        f"t2={stats['tier2']:3d}({t2_pct:.0f}%)  "
        f"idx={stats['indexed']:3d}  "
        f"err={stats['errors']}  "
        f"{rate:.1f}rec/s  "
        f"ETA={eta_min:.0f}m  "
        f"[{rows_so_far}/{TOTAL_ROWS}]"
    )


async def run_ingestion(
    limit: int | None,
    skip: int,
    batch_size: int,
    dry_run: bool,
    resume: bool,
) -> None:
    from app.database import init_db, AsyncSessionLocal
    from app.indexing.qdrant_client import ensure_collections
    from app.indexing.bm25 import get_bm25_index
    from app.pipeline.ingestion import iter_csv_rows
    from app.pipeline.orchestrator import process_batch

    # ------------------------------------------------------------------ #
    # Load checkpoint
    # ------------------------------------------------------------------ #
    checkpoint = _load_checkpoint()
    if resume and checkpoint["rows_processed"] > 0:
        skip = checkpoint["rows_processed"]
        cumulative = checkpoint["cumulative"]
        print(f"\n  Resuming from row {skip:,} (checkpoint found)")
    else:
        cumulative = {
            "total": 0, "dupes": 0, "tier0": 0,
            "tier2": 0, "indexed": 0, "errors": 0, "skipped_short": 0,
        }
        if resume:
            print("  No checkpoint found — starting from beginning")

    # ------------------------------------------------------------------ #
    # Infrastructure
    # ------------------------------------------------------------------ #
    print("\n── Initialising infrastructure ──")
    if not dry_run:
        await init_db()
        await ensure_collections()
        print("  PostgreSQL ✓  Qdrant ✓")
    else:
        print("  Dry run — DB/Qdrant writes skipped")

    # ------------------------------------------------------------------ #
    # Stream + batch
    # ------------------------------------------------------------------ #
    print(f"\n── Ingesting {CSV_PATH.name} ──")
    print(f"   skip={skip:,}  limit={limit or 'all'}  batch_size={batch_size}")

    chunk_num = 0
    batch: list = []
    rows_processed = skip  # rows already handled (for checkpoint)
    t_start = time.perf_counter()

    try:
        import tqdm
        pbar = tqdm.tqdm(
            total=(limit or TOTAL_ROWS) - skip,
            unit="rec",
            desc="Ingesting",
            dynamic_ncols=True,
        )
        use_pbar = True
    except ImportError:
        pbar = None
        use_pbar = False

    async with AsyncSessionLocal() as session:
        for row in iter_csv_rows(CSV_PATH, limit=limit, skip=skip):
            batch.append(row)

            if len(batch) < batch_size:
                continue

            # Process batch
            chunk_num += 1
            t_chunk = time.perf_counter()
            stats = await process_batch(batch, session, dry_run=dry_run)
            elapsed_chunk = time.perf_counter() - t_chunk

            for k in cumulative:
                cumulative[k] += stats.get(k, 0)
            rows_processed += len(batch)

            if use_pbar:
                pbar.update(len(batch))
            _print_chunk_stats(chunk_num, stats, elapsed_chunk, rows_processed)

            # Save checkpoint every 5 chunks
            if not dry_run and chunk_num % 5 == 0:
                _save_checkpoint(rows_processed, cumulative)

            batch.clear()

        # Final partial batch
        if batch:
            chunk_num += 1
            t_chunk = time.perf_counter()
            stats = await process_batch(batch, session, dry_run=dry_run)
            elapsed_chunk = time.perf_counter() - t_chunk

            for k in cumulative:
                cumulative[k] += stats.get(k, 0)
            rows_processed += len(batch)

            if use_pbar:
                pbar.update(len(batch))
            _print_chunk_stats(chunk_num, stats, elapsed_chunk, rows_processed)
            batch.clear()

    if use_pbar:
        pbar.close()

    if not dry_run:
        _save_checkpoint(rows_processed, cumulative)

    # ------------------------------------------------------------------ #
    # Final report
    # ------------------------------------------------------------------ #
    elapsed_total = time.perf_counter() - t_start
    elapsed_min = elapsed_total / 60

    processed = cumulative["total"] - cumulative["dupes"] - cumulative["skipped_short"]
    t0_pct = cumulative["tier0"] / max(processed, 1) * 100
    t2_pct = cumulative["tier2"] / max(processed, 1) * 100

    bm25_size = 0
    if not dry_run:
        from app.indexing.bm25 import get_bm25_index
        bm25_size = get_bm25_index().size

    print("\n" + "=" * 70)
    print("FULL INGESTION COMPLETE")
    print("=" * 70)
    print(f"  Records loaded      : {cumulative['total']:>8,}")
    print(f"  Duplicates skipped  : {cumulative['dupes']:>8,}")
    print(f"  Too short (skipped) : {cumulative['skipped_short']:>8,}")
    print(f"  Tier 0 (free rules) : {cumulative['tier0']:>8,}   {t0_pct:5.1f}%")
    print(f"  Tier 2 (Gemini LLM) : {cumulative['tier2']:>8,}   {t2_pct:5.1f}%")
    print(f"  Indexed in Qdrant   : {cumulative['indexed']:>8,}")
    print(f"  BM25 documents      : {bm25_size:>8,}")
    print(f"  Errors              : {cumulative['errors']:>8,}")
    print(f"  Elapsed             : {elapsed_min:>8.1f}m  ({elapsed_total:.0f}s)")
    print(f"  Throughput          : {cumulative['total'] / elapsed_total:.1f} rec/s")
    print("=" * 70)

    if cumulative["errors"] == 0:
        print("  ✓  Ingestion PASSED — 0 errors")
    else:
        print(f"  ✗  {cumulative['errors']:,} errors — check logs")
    print("=" * 70 + "\n")

    # Clean up checkpoint on success
    if not dry_run and cumulative["errors"] == 0 and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("  Checkpoint file removed (clean run).")


def main():
    parser = argparse.ArgumentParser(description="TenderScout Phase 1C — full ingestion")
    parser.add_argument("--limit",      type=int, default=None, help="Rows to process (default: all)")
    parser.add_argument("--skip",       type=int, default=0,    help="Rows to skip from start")
    parser.add_argument("--batch-size", type=int, default=100,  help="Records per orchestrator batch (default: 100)")
    parser.add_argument("--dry",        action="store_true",    help="Skip DB/Qdrant writes")
    parser.add_argument("--resume",     action="store_true",    help="Resume from checkpoint file")
    args = parser.parse_args()

    asyncio.run(run_ingestion(
        limit=args.limit,
        skip=args.skip,
        batch_size=args.batch_size,
        dry_run=args.dry,
        resume=args.resume,
    ))


if __name__ == "__main__":
    main()
