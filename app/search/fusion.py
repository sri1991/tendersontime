"""
RRF Fusion — Reciprocal Rank Fusion with query-type-specific weights.

RRF formula:  score(d) = Σ  weight_i / (k + rank_i(d))

where:
  k         = 60  (standard constant, dampens effect of very high rank gaps)
  rank_i    = position of document d in ranked list i (1-based)
  weight_i  = index-specific weight from RouteDecision

References: Cormack et al., 2009 — "Reciprocal Rank Fusion outperforms Condorcet"
"""
from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[dict]],
    weights: dict[str, float],
    top_k: int = 20,
    k: int = 60,
) -> list[dict]:
    """
    Fuse multiple ranked lists using weighted RRF.

    Args:
        ranked_lists: {"semantic": [{tot_id, score, ...}], "bm25": [...], "cpv": [...]}
        weights:      {"semantic": 0.6, "bm25": 0.2, "cpv": 0.2}
        top_k:        Number of results to return.
        k:            RRF constant (default 60).

    Returns:
        Fused list of {tot_id, rrf_score, sources} sorted by rrf_score desc.
    """
    rrf_scores: dict[str, float] = defaultdict(float)
    hit_sources: dict[str, list[str]] = defaultdict(list)
    payloads: dict[str, dict] = {}

    for index_name, hits in ranked_lists.items():
        w = weights.get(index_name, 1.0)
        for rank, hit in enumerate(hits, start=1):
            tot_id = hit.get("tot_id")
            if not tot_id:
                continue
            rrf_scores[tot_id] += w / (k + rank)
            hit_sources[tot_id].append(index_name)
            # Keep payload from the most detailed source (semantic has most)
            if tot_id not in payloads and "payload" in hit:
                payloads[tot_id] = hit["payload"]

    # Sort by fused score
    sorted_ids = sorted(rrf_scores, key=lambda tid: rrf_scores[tid], reverse=True)[:top_k]

    return [
        {
            "tot_id": tid,
            "rrf_score": round(rrf_scores[tid], 6),
            "sources": hit_sources[tid],       # which indexes found this doc
            "payload": payloads.get(tid, {}),
        }
        for tid in sorted_ids
    ]
