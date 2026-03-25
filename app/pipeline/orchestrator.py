"""
Cascade Orchestrator — routes each tender record through Tier 0 → Tier 2.

Flow:
  1. Dedup check (SHA-256) — skip if already processed
  2. CPV mapping (Steps 1+2: trie + embedding)
  3. Tier 0 rule-based — if passes, done
  4. Tier 2 Gemini batch — for records that don't pass Tier 0

Then for all enriched records:
  5. Embed searchable text
  6. Upsert to Qdrant
  7. Save to PostgreSQL
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.cpv.mapper import map_cpv, _calibrate_cosine
from app.indexing.embeddings import embed_texts
from app.indexing.qdrant_client import upsert_tenders
from app.models.tender import TenderCSVRow, TenderEnriched, TenderRecord, TierProcessed
from app.pipeline.tier0 import process_tier0
from app.pipeline.tier2 import enrich_batch_tier2

settings = get_settings()


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _parse_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


async def _is_duplicate(session: AsyncSession, dedup_hash: str) -> bool:
    """Check if this hash already exists in the database."""
    result = await session.execute(
        select(TenderRecord.id).where(TenderRecord.dedup_hash == dedup_hash).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _save_to_db(
    session: AsyncSession,
    row: TenderCSVRow,
    enriched: TenderEnriched,
    dedup_hash: str,
    qdrant_point_id: Optional[str] = None,
) -> TenderRecord:
    """Persist a tender record + enrichment to PostgreSQL."""
    record = TenderRecord(
        tot_id=row.tot_id,
        tender_notice_no=row.tender_notice_no,
        country=row.country,
        state=row.state,
        purchaser_name=row.purchaser_name,
        purchaser_address=row.purchaser_address,
        purchaser_email=row.purchaser_email,
        purchaser_url=row.purchaser_url,
        summary=row.summary,
        description=row.description,
        tender_value=row.tender_value,
        currency=row.currency,
        usd_tender_value=_parse_float(row.usd_tender_value),
        published_date=_parse_date(row.published_date),
        closing_date=_parse_date(row.closing_date),
        competition=row.competition,
        financier_name=row.financier_name,
        cpv_code=enriched.cpv_code,
        cpv_description=enriched.cpv_description,
        cpv_confidence=enriched.cpv_confidence,
        tender_notice_document=row.tender_notice_document,
        procurement_type=enriched.procurement_type,
        tier_processed=enriched.tier_processed,
        dedup_hash=dedup_hash,
        is_indexed=qdrant_point_id is not None,
        qdrant_id=qdrant_point_id,
        extracted_value_usd=enriched.extracted_value_usd,
        location_mentions=json.dumps(enriched.location_mentions) if enriched.location_mentions else None,
    )
    session.add(record)
    await session.flush()
    return record


def _build_qdrant_payload(row: TenderCSVRow, enriched: TenderEnriched) -> dict:
    """Construct payload stored alongside the Qdrant vector."""
    return {
        "country": row.country,
        "purchaser_name": row.purchaser_name,
        "summary": (row.summary or "")[:500],
        "cpv_code": enriched.cpv_code,
        "cpv_description": enriched.cpv_description,
        "procurement_type": enriched.procurement_type.value if enriched.procurement_type else None,
        "usd_value": _parse_float(row.usd_tender_value),
        "published_date": row.published_date,
        "closing_date": row.closing_date,
        "document_url": row.tender_notice_document,
        "tier": enriched.tier_processed.value if enriched.tier_processed else None,
    }


async def process_batch(
    rows: list[TenderCSVRow],
    session: AsyncSession,
    dry_run: bool = False,
) -> dict:
    """
    Process a batch of TenderCSVRow records through the full pipeline.

    Args:
        rows:    Batch of validated CSV rows.
        session: Async SQLAlchemy session.
        dry_run: If True, skip DB and Qdrant writes (for testing).

    Returns:
        Stats dict: {total, dupes, tier0, tier2, indexed, errors}
    """
    stats = {
        "total": len(rows),
        "dupes": 0,
        "tier0": 0,
        "tier2": 0,
        "indexed": 0,
        "errors": 0,
        "skipped_short": 0,
    }

    # --- Dedup pass (DB + within-batch) ---
    unique_rows: list[TenderCSVRow] = []
    hashes: list[str] = []
    seen_hashes_in_batch: set[str] = set()

    for row in rows:
        h = row.compute_dedup_hash()
        # Skip if duplicate within this batch (e.g. identical "Hiring of Third Supervisor" records)
        if h in seen_hashes_in_batch:
            stats["dupes"] += 1
            logger.debug(f"Skipping intra-batch duplicate: {row.tot_id}")
            continue
        # Skip if already in DB
        if not dry_run and await _is_duplicate(session, h):
            stats["dupes"] += 1
            logger.debug(f"Skipping DB duplicate: {row.tot_id}")
            continue
        unique_rows.append(row)
        hashes.append(h)
        seen_hashes_in_batch.add(h)

    if not unique_rows:
        logger.info("All records in batch were duplicates.")
        return stats

    # --- CPV mapping (Steps 1+2) — trie first, batch embed only for misses ---
    from app.indexing.embeddings import embed_texts as _embed_texts
    from app.indexing.qdrant_client import search_cpv as _search_cpv
    from app.cpv.mapper import trie_lookup, CPVMatch

    trie_results: list[CPVMatch] = []
    needs_embed_indices: list[int] = []

    for i, row in enumerate(unique_rows):
        trie_hit = trie_lookup(row.searchable_text())
        trie_results.append(trie_hit)
        if trie_hit.confidence < settings.tier0_cpv_confidence_threshold:
            needs_embed_indices.append(i)

    # Batch embed only texts that didn't get a high-confidence trie hit
    cpv_results: list[CPVMatch] = list(trie_results)
    if needs_embed_indices:
        embed_texts_batch = [unique_rows[i].searchable_text()[:2000] for i in needs_embed_indices]
        try:
            embeddings = await _embed_texts(embed_texts_batch, task_type="RETRIEVAL_QUERY")
            # Fire all Qdrant CPV searches in parallel instead of sequentially
            import asyncio as _asyncio
            all_cpv_hits = await _asyncio.gather(
                *[_search_cpv(emb, top_k=1) for emb in embeddings],
                return_exceptions=True,
            )
            for idx, cpv_hits in zip(needs_embed_indices, all_cpv_hits):
                if isinstance(cpv_hits, Exception) or not cpv_hits:
                    continue
                top = cpv_hits[0]
                conf = _calibrate_cosine(float(top["score"]))
                if conf > trie_results[idx].confidence:
                    cpv_results[idx] = CPVMatch(
                        cpv_code=top["cpv_code"],
                        cpv_description=top["description"],
                        confidence=round(conf, 3),
                        method="embedding",
                    )
        except Exception as e:
            logger.warning(f"Batch CPV embedding lookup failed: {e}")

    # --- Tier 0 pass ---
    tier0_enriched: list[Optional[TenderEnriched]] = []
    tier2_needed_rows: list[TenderCSVRow] = []
    tier2_needed_indices: list[int] = []

    for i, (row, cpv_match) in enumerate(zip(unique_rows, cpv_results)):
        result = process_tier0(
            row,
            cpv_code=cpv_match.cpv_code,
            cpv_description=cpv_match.cpv_description,
            cpv_confidence=cpv_match.confidence,
        )
        if result.passed:
            tier0_enriched.append(result.enriched)
            stats["tier0"] += 1
        elif result.reason.startswith("Text too short"):
            tier0_enriched.append(None)
            stats["skipped_short"] += 1
        else:
            tier0_enriched.append(None)
            tier2_needed_rows.append(row)
            tier2_needed_indices.append(i)

    # --- Tier 2 pass for records that didn't pass Tier 0 ---
    tier2_enriched_map: dict[str, TenderEnriched] = {}
    if tier2_needed_rows:
        logger.info(f"Routing {len(tier2_needed_rows)} records to Tier 2 (Gemini)")
        tier2_results = await enrich_batch_tier2(tier2_needed_rows)
        stats["tier2"] += len(tier2_results)
        for enriched in tier2_results:
            tier2_enriched_map[enriched.tot_id] = enriched

    # --- Fill in Tier 2 results ---
    final_enriched: list[Optional[TenderEnriched]] = list(tier0_enriched)
    for i in tier2_needed_indices:
        row = unique_rows[i]
        final_enriched[i] = tier2_enriched_map.get(row.tot_id)

    # --- Embed all indexable records ---
    indexable_rows: list[TenderCSVRow] = []
    indexable_enriched: list[TenderEnriched] = []
    indexable_hashes: list[str] = []

    for row, enriched, h in zip(unique_rows, final_enriched, hashes):
        if enriched is not None:
            indexable_rows.append(row)
            indexable_enriched.append(enriched)
            indexable_hashes.append(h)

    qdrant_ids: dict[str, str] = {}

    if indexable_rows and not dry_run:
        texts = [row.searchable_text() for row in indexable_rows]
        embeddings = await embed_texts(texts)

        tot_ids = [row.tot_id for row in indexable_rows]
        payloads = [
            _build_qdrant_payload(row, enriched)
            for row, enriched in zip(indexable_rows, indexable_enriched)
        ]

        await upsert_tenders(tot_ids, embeddings, payloads)
        stats["indexed"] += len(indexable_rows)

        import uuid
        for tot_id in tot_ids:
            qdrant_ids[tot_id] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tot_id))

        # Feed BM25 index incrementally
        from app.indexing.bm25 import get_bm25_index
        bm25_docs = [(row.tot_id, row.searchable_text()) for row in indexable_rows]
        get_bm25_index().add_documents(bm25_docs)

    # --- Persist to PostgreSQL (each record in its own savepoint) ---
    if not dry_run:
        for row, enriched, h in zip(unique_rows, final_enriched, hashes):
            if enriched is None:
                continue
            try:
                async with session.begin_nested():  # savepoint — rolls back only this record on error
                    await _save_to_db(
                        session, row, enriched, h,
                        qdrant_point_id=qdrant_ids.get(row.tot_id),
                    )
            except Exception as e:
                logger.error(f"DB save failed for {row.tot_id}: {e}")
                stats["errors"] += 1

        await session.commit()

    logger.info(
        f"Batch done | total={stats['total']} dupes={stats['dupes']} "
        f"tier0={stats['tier0']} tier2={stats['tier2']} "
        f"indexed={stats['indexed']} errors={stats['errors']} "
        f"skipped_short={stats['skipped_short']}"
    )
    return stats
