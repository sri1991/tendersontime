"""
Multi-index retrieval — runs semantic, BM25, and CPV searches in parallel
then hands results to the RRF fusion layer.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from app.indexing.bm25 import get_bm25_index
from app.indexing.embeddings import embed_query
from app.indexing.qdrant_client import search_semantic, search_cpv
from app.search.fusion import reciprocal_rank_fusion
from app.search.router import RouteDecision, QueryType


async def _run_semantic(
    query_vec: list[float],
    top_k: int,
    qdrant_filters: Optional[dict],
) -> list[dict]:
    try:
        return await search_semantic(query_vec, top_k=top_k, filters=qdrant_filters)
    except Exception as e:
        logger.warning(f"Semantic search failed: {e}")
        return []


async def _run_bm25(query: str, top_k: int) -> list[dict]:
    try:
        index = get_bm25_index()
        if index.size == 0:
            logger.debug("BM25 index empty — skipping")
            return []
        results = index.search(query, top_k=top_k)
        # BM25 returns {tot_id, score, rank} — add empty payload for RRF
        for r in results:
            r.setdefault("payload", {})
        return results
    except Exception as e:
        logger.warning(f"BM25 search failed: {e}")
        return []


async def _run_cpv(
    query_vec: list[float],
    cpv_filter: Optional[str],
    top_k: int,
    qdrant_filters: Optional[dict],
) -> list[dict]:
    """
    CPV-aware retrieval:
      1. Find closest CPV nodes to the query vector.
      2. Filter tender semantic search to those CPV codes.
    """
    try:
        # Step 1: find closest CPV codes for this query
        cpv_hits = await search_cpv(query_vec, top_k=3)
        if not cpv_hits:
            return []

        # Step 2: build CPV filter — prefer explicit filter, else top CPV match
        if cpv_filter:
            # Prefix filter: all codes starting with cpv_filter digits
            target_cpv = cpv_filter
        else:
            target_cpv = cpv_hits[0]["cpv_code"] if cpv_hits else None

        if not target_cpv:
            return []

        # Step 3: semantic search restricted to that CPV code
        cpv_qdrant_filter = {**(qdrant_filters or {}), "cpv_code": target_cpv}
        hits = await search_semantic(query_vec, top_k=top_k, filters=cpv_qdrant_filter)
        return hits
    except Exception as e:
        logger.warning(f"CPV retrieval failed: {e}")
        return []


async def retrieve(
    query: str,
    route: RouteDecision,
    top_k: int = 20,
    extra_filters: Optional[dict[str, Any]] = None,
) -> list[dict]:
    """
    Run all three indexes in parallel and fuse with RRF.

    Args:
        query:         Raw search query.
        route:         RouteDecision from the query router.
        top_k:         Number of final results.
        extra_filters: Additional Qdrant filters (country, date range, etc.).

    Returns:
        Fused + ranked list of {tot_id, rrf_score, sources, payload}.
    """
    # Embed query once, reuse across indexes
    query_vec = await embed_query(route.clean_query)

    qdrant_filters = extra_filters or None

    # Run all three indexes concurrently
    semantic_hits, bm25_hits, cpv_hits = await asyncio.gather(
        _run_semantic(query_vec, top_k=top_k, qdrant_filters=qdrant_filters),
        _run_bm25(route.clean_query, top_k=top_k),
        _run_cpv(query_vec, route.cpv_filter, top_k=top_k, qdrant_filters=qdrant_filters),
    )

    logger.debug(
        f"Retrieval hits — semantic={len(semantic_hits)} "
        f"bm25={len(bm25_hits)} cpv={len(cpv_hits)} "
        f"route={route.query_type} weights={route.weights}"
    )

    ranked_lists = {
        "semantic": semantic_hits,
        "bm25": bm25_hits,
        "cpv": cpv_hits,
    }

    return reciprocal_rank_fusion(ranked_lists, weights=route.weights, top_k=top_k)
