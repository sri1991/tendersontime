"""
One-time migration script: ChromaDB → PostgreSQL + pgvector.

Reads all records from the ChromaDB 'tenders_v1' collection and upserts
them into the PostgreSQL 'tenders' table with their existing embeddings.
The BM25 index is also rebuilt and saved to disk at the end.

Usage:
    # Ensure postgres + chroma are running (docker-compose up)
    GEMINI_API_KEY=... DATABASE_URL=... python scripts/migrate_chroma_to_postgres.py

    # Dry run (no DB writes):
    python scripts/migrate_chroma_to_postgres.py --dry-run

    # Resume from a specific ChromaDB offset:
    python scripts/migrate_chroma_to_postgres.py --offset 10000
"""

import os
import sys
import json
import asyncio
import argparse
import logging
from typing import List, Dict, Any

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 500
PROGRESS_FILE = "data/migration_progress.json"


def load_progress() -> int:
    """Returns the last successfully migrated offset."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f).get("last_offset", 0)
        except Exception:
            pass
    return 0


def save_progress(offset: int):
    os.makedirs("data", exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"last_offset": offset}, f)


def connect_chroma():
    import chromadb
    chroma_host = os.getenv("CHROMA_HOST")
    chroma_port = os.getenv("CHROMA_PORT")
    if chroma_host and chroma_port:
        client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
    else:
        client = chromadb.PersistentClient(path="./chroma_db")
    return client.get_or_create_collection("tenders_v1")


async def migrate_batch(
    records: List[Dict[str, Any]],
    dry_run: bool,
):
    """Upserts a batch of ChromaDB records into PostgreSQL."""
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from src.db.schema import engine as db_engine, Tender

    rows = []
    for rec in records:
        meta = rec.get("metadata", {})
        embedding = rec.get("embedding")  # List[float] already from chroma

        # Parse closing_date
        closing_raw = meta.get("closing_date", "")
        closing_date = None
        if closing_raw and closing_raw not in ("N/A", "nan", "None", ""):
            try:
                from src.cleaning.cleaner import DateStandardizer
                parsed = DateStandardizer.to_iso(str(closing_raw))
                closing_date = parsed if parsed != str(closing_raw) else None
            except Exception:
                pass

        rows.append({
            "id":               str(rec["id"]),
            "tot_id":           str(meta.get("tot_id", "N/A")),
            "ref_no":           str(meta.get("ref_no", "N/A")),
            "title":            str(meta.get("original_title", ""))[:500],
            "description":      str(meta.get("description", ""))[:2000],
            "signal_summary":   str(rec.get("document", ""))[:1000],
            "search_keywords":  [],  # not stored in chroma metadata
            "project_tags":     [t.strip() for t in str(meta.get("project_tags", "")).split(",") if t.strip()],
            "core_domain":      meta.get("core_domain", "Other"),
            "procurement_type": meta.get("procurement_type", "Unknown"),
            "authority_name":   meta.get("authority_name", "Unknown"),
            "location_city":    meta.get("location_city", "Unknown"),
            "location_state":   meta.get("location_state", "Unknown"),
            "country":          meta.get("country", "Unknown"),
            "closing_date":     closing_date,
            "url":              meta.get("url", "#"),
            "is_corrigendum":   bool(meta.get("is_corrigendum", False)),
            "embedding_text":   str(rec.get("document", "")),
            "embedding":        embedding,
        })

    if dry_run:
        logger.info(f"[DRY RUN] Would upsert {len(rows)} records.")
        return len(rows)

    try:
        async with db_engine.begin() as conn:
            stmt = pg_insert(Tender).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "title":            stmt.excluded.title,
                    "description":      stmt.excluded.description,
                    "signal_summary":   stmt.excluded.signal_summary,
                    "project_tags":     stmt.excluded.project_tags,
                    "core_domain":      stmt.excluded.core_domain,
                    "procurement_type": stmt.excluded.procurement_type,
                    "authority_name":   stmt.excluded.authority_name,
                    "location_city":    stmt.excluded.location_city,
                    "location_state":   stmt.excluded.location_state,
                    "country":          stmt.excluded.country,
                    "closing_date":     stmt.excluded.closing_date,
                    "url":              stmt.excluded.url,
                    "is_corrigendum":   stmt.excluded.is_corrigendum,
                    "embedding_text":   stmt.excluded.embedding_text,
                    "embedding":        stmt.excluded.embedding,
                    "updated_at":       text("NOW()"),
                },
            )
            await conn.execute(stmt)
        return len(rows)
    except Exception as e:
        logger.error(f"Batch upsert failed: {e}")
        return 0


async def run_migration(start_offset: int, dry_run: bool):
    logger.info(f"Connecting to ChromaDB...")
    collection = connect_chroma()
    total = collection.count()
    logger.info(f"ChromaDB collection has {total} records.")

    if total == 0:
        logger.warning("ChromaDB collection is empty. Nothing to migrate.")
        return

    migrated = 0
    offset = start_offset

    while offset < total:
        logger.info(f"Fetching batch offset={offset} (batch_size={BATCH_SIZE})...")

        try:
            result = collection.get(
                limit=BATCH_SIZE,
                offset=offset,
                include=["metadatas", "documents", "embeddings"],
            )
        except Exception as e:
            logger.error(f"ChromaDB fetch failed at offset {offset}: {e}")
            break

        ids = result.get("ids", [])
        if not ids:
            logger.info("No more records. Migration complete.")
            break

        metadatas = result.get("metadatas", [])
        documents = result.get("documents", [])
        embeddings = result.get("embeddings", [])

        records = [
            {
                "id":       ids[i],
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "document": documents[i] if i < len(documents) else "",
                "embedding": list(embeddings[i]) if embeddings is not None and len(embeddings) > 0 and i < len(embeddings) else None,
            }
            for i in range(len(ids))
        ]

        # Skip records without embeddings — can't migrate them
        records_with_emb = [r for r in records if r["embedding"] is not None]
        skipped = len(records) - len(records_with_emb)
        if skipped:
            logger.warning(f"Skipping {skipped} records without embeddings.")

        count = await migrate_batch(records_with_emb, dry_run)
        migrated += count
        offset += BATCH_SIZE

        if not dry_run:
            save_progress(offset)

        logger.info(f"Progress: {offset}/{total} ({round(offset/total*100, 1)}%) — migrated {migrated} records so far.")

    # Rebuild and persist BM25 index
    if not dry_run:
        logger.info("Rebuilding BM25 index from PostgreSQL...")
        await rebuild_bm25()

    logger.info(f"Migration complete. Total records migrated: {migrated}")
    if not dry_run and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


async def rebuild_bm25():
    """Loads all embedding_text from PostgreSQL and builds the BM25 index."""
    from sqlalchemy import text
    from src.db.schema import engine as db_engine
    from src.db.bm25_store import BM25Store

    store = BM25Store()
    ids, documents = [], []

    logger.info("Loading documents for BM25 rebuild...")
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT id, embedding_text FROM tenders WHERE embedding_text IS NOT NULL"))
        for row in result.mappings():
            ids.append(row["id"])
            documents.append(row["embedding_text"] or "")

    logger.info(f"Building BM25 index from {len(ids)} documents...")
    store.build(ids, documents)
    store.save()
    logger.info("BM25 index saved to disk.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate ChromaDB → PostgreSQL+pgvector")
    parser.add_argument("--offset", type=int, default=None, help="Start offset (default: resume from last progress)")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to DB")
    args = parser.parse_args()

    start_offset = args.offset if args.offset is not None else load_progress()
    if start_offset > 0:
        logger.info(f"Resuming migration from offset {start_offset}")

    asyncio.run(run_migration(start_offset, args.dry_run))
