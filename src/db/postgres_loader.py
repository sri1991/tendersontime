"""
PostgresLoader — replaces chroma_loader.py for new indexing.

Writes enriched tender records to PostgreSQL+pgvector and maintains
the persisted BM25 index on disk.
"""

import os
import json
import logging
from typing import List, Dict, Any

from google import genai
from google.genai import types
from dotenv import load_dotenv

from src.db.schema import engine, Tender, IngestionDLQ
from src.db.bm25_store import BM25Store
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

load_dotenv()
logger = logging.getLogger(__name__)


class PostgresLoader:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY found. Embeddings will fail.")

        self.client_genai = genai.Client(api_key=self.api_key)
        self.bm25_store = BM25Store()
        self.bm25_store.load()

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generates 3072-dim embeddings in batches of 50."""
        batch_size = 50
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                response = self.client_genai.models.embed_content(
                    model="gemini-embedding-001",
                    contents=batch,
                )
                all_embeddings.extend([e.values for e in response.embeddings])
            except Exception as e:
                logger.error(f"Embedding failed for batch {i}: {e}")
                raise

        return all_embeddings

    async def write_to_dlq(self, tender_id: str, raw_data: dict, error: str):
        """Writes a failed record to the dead letter queue table."""
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ingestion_dlq (tender_id, raw_data, error) "
                    "VALUES (:tender_id, :raw_data::jsonb, :error)"
                ),
                {"tender_id": tender_id, "raw_data": json.dumps(raw_data), "error": error}
            )

    def _parse_closing_date(self, raw: Any) -> str | None:
        """Attempts to normalise closing date to YYYY-MM-DD. Returns None if unparseable."""
        if not raw or str(raw).strip() in ("N/A", "nan", "None", ""):
            return None
        from src.cleaning.cleaner import DateStandardizer
        result = DateStandardizer.to_iso(str(raw))
        return result if result != str(raw) else None

    async def load_from_jsonl(self, jsonl_path: str, batch_size: int = 50):
        """
        Reads enriched JSONL and upserts records into PostgreSQL.
        Updates the BM25 index incrementally after each batch.
        """
        if not os.path.exists(jsonl_path):
            logger.error(f"File not found: {jsonl_path}")
            return

        with open(jsonl_path, "r") as f:
            lines = f.readlines()

        total = len(lines)
        logger.info(f"Loading {total} records into PostgreSQL...")

        for i in range(0, total, batch_size):
            chunk_lines = lines[i:i + batch_size]
            records = []
            embedding_texts = []
            failed_raw = []

            for line in chunk_lines:
                try:
                    data = json.loads(line)

                    if "error" in data and not data.get("signal_summary"):
                        logger.warning(f"Skipping record with error: {data.get('error')}")
                        continue

                    signal_text = data.get("signal_summary", "")
                    if not signal_text:
                        continue

                    keywords = ", ".join(data.get("search_keywords") or [])
                    tags = ", ".join(data.get("project_tags") or [])
                    title_text = data.get("Title") or data.get("Summary") or ""
                    desc_text = data.get("Description") or ""

                    embedding_text = (
                        f"{title_text}. {signal_text}. {desc_text[:1000]}. "
                        f"Tags: {tags}. Keywords: {keywords}"
                    )

                    entities = data.get("entities") or {}
                    authority = entities.get("authority_name", "Unknown")
                    if authority in ("Unknown", "N/A") and data.get("Purchaser_Name"):
                        authority = str(data["Purchaser_Name"])[:100]

                    record_id = str(data.get("RefNo") or data.get("TOT_ID") or hash(signal_text))

                    records.append({
                        "id":               record_id,
                        "tot_id":           str(data.get("TOT_ID", "N/A")),
                        "ref_no":           str(data.get("RefNo", "N/A")),
                        "title":            title_text[:500],
                        "description":      desc_text[:2000],
                        "signal_summary":   signal_text,
                        "search_keywords":  data.get("search_keywords") or [],
                        "project_tags":     data.get("project_tags") or [],
                        "core_domain":      data.get("core_domain", "Other"),
                        "procurement_type": data.get("procurement_type", "Unknown"),
                        "authority_name":   authority,
                        "location_city":    entities.get("location_city", "Unknown"),
                        "location_state":   entities.get("location_state", "Unknown"),
                        "country":          data.get("Country", "Unknown"),
                        "closing_date":     self._parse_closing_date(data.get("Closing_Date")),
                        "url":              data.get("Tender_Notice_Document", "#"),
                        "is_corrigendum":   "corrigendum" in title_text.lower(),
                        "embedding_text":   embedding_text,
                    })
                    embedding_texts.append(embedding_text)

                except Exception as e:
                    logger.error(f"Failed to parse JSONL line: {e}")
                    failed_raw.append((None, {}, str(e)))

            if not records:
                continue

            # Generate embeddings
            try:
                embeddings = self.generate_embeddings(embedding_texts)
            except Exception as e:
                logger.error(f"Embedding batch {i} failed entirely: {e}")
                # Write all to DLQ
                for rec in records:
                    await self.write_to_dlq(rec["id"], rec, f"Embedding failed: {e}")
                continue

            # Attach embeddings to records
            for rec, emb in zip(records, embeddings):
                rec["embedding"] = emb

            # Upsert to PostgreSQL
            try:
                async with engine.begin() as conn:
                    stmt = pg_insert(Tender).values(records)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "signal_summary":   stmt.excluded.signal_summary,
                            "search_keywords":  stmt.excluded.search_keywords,
                            "project_tags":     stmt.excluded.project_tags,
                            "core_domain":      stmt.excluded.core_domain,
                            "procurement_type": stmt.excluded.procurement_type,
                            "authority_name":   stmt.excluded.authority_name,
                            "embedding_text":   stmt.excluded.embedding_text,
                            "embedding":        stmt.excluded.embedding,
                            "updated_at":       text("NOW()"),
                        }
                    )
                    await conn.execute(stmt)
                logger.info(f"Upserted batch {i}–{i + len(records)} to PostgreSQL.")
            except Exception as e:
                logger.error(f"PostgreSQL upsert failed for batch {i}: {e}")
                for rec in records:
                    await self.write_to_dlq(rec["id"], rec, f"DB upsert failed: {e}")
                continue

            # Update BM25 index incrementally
            new_ids = [r["id"] for r in records]
            self.bm25_store.update(new_ids, embedding_texts)

        # Persist BM25 to disk after all batches
        self.bm25_store.save()
        logger.info("PostgreSQL loading complete. BM25 index saved.")
