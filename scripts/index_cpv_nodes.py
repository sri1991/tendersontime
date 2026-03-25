"""
Index all CPV taxonomy nodes into the Qdrant cpv_taxonomy collection.

Run once before starting the pipeline (or after adding new CPV codes):
    python scripts/index_cpv_nodes.py

This enables Step 2 of the CPV mapper (embedding similarity lookup).
Without this, Step 2 is skipped and more records fall through to Tier 2 (LLM).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from loguru import logger

from app.cpv.taxonomy import load_taxonomy
from app.indexing.embeddings import embed_texts
from app.indexing.qdrant_client import ensure_collections, upsert_cpv_nodes


async def index_cpv_nodes():
    taxonomy = load_taxonomy()
    logger.info(f"Indexing {len(taxonomy)} CPV nodes into Qdrant...")

    await ensure_collections()

    codes = [item["code"] for item in taxonomy]
    descriptions = [item["description"] for item in taxonomy]

    # Embed all descriptions (batched 100 at a time internally)
    logger.info("Embedding CPV descriptions with text-embedding-004...")
    embeddings = await embed_texts(descriptions, task_type="RETRIEVAL_DOCUMENT")

    # Upsert into cpv_taxonomy collection
    await upsert_cpv_nodes(codes, embeddings, descriptions)

    logger.info(f"Done — {len(codes)} CPV nodes indexed in Qdrant.")
    logger.info("Step 2 of CPV mapper (embedding lookup) is now active.")


if __name__ == "__main__":
    asyncio.run(index_cpv_nodes())
