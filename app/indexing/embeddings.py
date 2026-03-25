"""
Embedding client using Google text-embedding-004 via the Gemini SDK.
Handles batching and rate-limit retries.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

import google.generativeai as genai
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

settings = get_settings()

# Configure Gemini SDK once at module load
genai.configure(api_key=settings.gemini_api_key)

# Google embedding API allows up to 100 texts per batch request
_EMBED_BATCH_SIZE = 100


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
def _embed_batch_sync(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embed a batch of texts synchronously (runs in thread pool for async callers)."""
    result = genai.embed_content(
        model=settings.embedding_model,
        content=texts,
        task_type=task_type,
    )
    return result["embedding"]


async def embed_texts(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """
    Embed a list of texts using Google text-embedding-004.
    Splits into batches of 100 and runs in a thread pool to avoid blocking.

    Args:
        texts:     List of strings to embed.
        task_type: "RETRIEVAL_DOCUMENT" for indexing, "RETRIEVAL_QUERY" for search.

    Returns:
        List of embedding vectors (each is a list of 768 floats).
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    # Split into batches
    batches = [texts[i:i + _EMBED_BATCH_SIZE] for i in range(0, len(texts), _EMBED_BATCH_SIZE)]

    for i, batch in enumerate(batches):
        logger.debug(f"Embedding batch {i+1}/{len(batches)} ({len(batch)} texts)")
        embeddings = await asyncio.get_event_loop().run_in_executor(
            None, lambda b=batch: _embed_batch_sync(b, task_type)
        )
        all_embeddings.extend(embeddings)

    return all_embeddings


async def embed_query(query: str) -> list[float]:
    """Embed a single search query."""
    results = await embed_texts([query], task_type="RETRIEVAL_QUERY")
    return results[0]
