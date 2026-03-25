"""
Qdrant vector store client.

Collections:
  - tenders_semantic  : full-text embedding for semantic search
  - tenders_cpv       : CPV code descriptions (pre-indexed taxonomy)

Payload stored per tender point:
  tot_id, country, summary, cpv_code, cpv_description,
  procurement_type, usd_value, published_date, closing_date,
  purchaser_name, document_url
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config import get_settings

settings = get_settings()

# Collection names
COLLECTION_SEMANTIC = "tenders_semantic"
COLLECTION_CPV = "cpv_taxonomy"

_client: Optional[AsyncQdrantClient] = None


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.qdrant_url)
    return _client


async def ensure_collections() -> None:
    """Create collections if they don't already exist."""
    client = get_qdrant()
    response = await client.get_collections()
    existing = {c.name for c in response.collections}

    if COLLECTION_SEMANTIC not in existing:
        await client.create_collection(
            collection_name=COLLECTION_SEMANTIC,
            vectors_config=VectorParams(
                size=settings.embedding_dimension,
                distance=Distance.COSINE,
            ),
        )
        logger.info(f"Created collection: {COLLECTION_SEMANTIC}")

    if COLLECTION_CPV not in existing:
        await client.create_collection(
            collection_name=COLLECTION_CPV,
            vectors_config=VectorParams(
                size=settings.embedding_dimension,
                distance=Distance.COSINE,
            ),
        )
        logger.info(f"Created collection: {COLLECTION_CPV}")


async def upsert_tenders(
    tot_ids: list[str],
    embeddings: list[list[float]],
    payloads: list[dict[str, Any]],
) -> None:
    """Upsert tender points into the semantic collection."""
    client = get_qdrant()
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, tot_id)),
            vector=embedding,
            payload={**payload, "tot_id": tot_id},
        )
        for tot_id, embedding, payload in zip(tot_ids, embeddings, payloads)
    ]
    await client.upsert(collection_name=COLLECTION_SEMANTIC, points=points)
    logger.info(f"Upserted {len(points)} points to {COLLECTION_SEMANTIC}")


async def upsert_cpv_nodes(
    cpv_codes: list[str],
    embeddings: list[list[float]],
    descriptions: list[str],
) -> None:
    """Upsert CPV taxonomy nodes into the CPV collection."""
    client = get_qdrant()
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"cpv:{code}")),
            vector=embedding,
            payload={"cpv_code": code, "description": desc},
        )
        for code, embedding, desc in zip(cpv_codes, embeddings, descriptions)
    ]
    await client.upsert(collection_name=COLLECTION_CPV, points=points)
    logger.info(f"Upserted {len(points)} CPV nodes to {COLLECTION_CPV}")


async def search_semantic(
    query_vector: list[float],
    top_k: int = 20,
    filters: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """
    Search the semantic collection.

    Returns list of {tot_id, score, payload} dicts.
    """
    client = get_qdrant()
    qdrant_filter = None
    if filters:
        conditions = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in filters.items()
        ]
        qdrant_filter = Filter(must=conditions)

    results = await client.search(
        collection_name=COLLECTION_SEMANTIC,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
        query_filter=qdrant_filter,
    )

    return [
        {"tot_id": r.payload.get("tot_id"), "score": r.score, "payload": r.payload}
        for r in results
    ]


async def search_cpv(
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Find the closest CPV codes to an embedding."""
    client = get_qdrant()
    results = await client.search(
        collection_name=COLLECTION_CPV,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "cpv_code": r.payload.get("cpv_code"),
            "description": r.payload.get("description"),
            "score": r.score,
        }
        for r in results
    ]
