"""
Search API — multi-index semantic search with query routing and RRF fusion.

POST /api/v1/search
GET  /api/v1/search?q=...
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.models.tender import TenderSearchResult
from app.search.router import route_query
from app.search.retrieval import retrieve

router = APIRouter(prefix="/api/v1/search", tags=["Search"])


class SearchRequest(BaseModel):
    query: str                          = Field(..., min_length=1, max_length=500)
    top_k: int                          = Field(10, ge=1, le=100)
    country: Optional[str]              = None
    cpv_code: Optional[str]             = None
    procurement_type: Optional[str]     = None


class SearchResponse(BaseModel):
    query: str
    query_type: str
    weights: dict[str, float]
    total_hits: int
    results: list[TenderSearchResult]


@router.post("", response_model=SearchResponse)
async def search_tenders(req: SearchRequest):
    """
    Multi-index tender search.

    Query is classified by the router → semantic / keyword / CPV / mixed.
    All three indexes run in parallel, results fused with weighted RRF.
    """
    route = route_query(req.query)

    extra_filters: dict[str, Any] = {}
    if req.country:
        extra_filters["country"] = req.country
    if req.cpv_code:
        extra_filters["cpv_code"] = req.cpv_code
    if req.procurement_type:
        extra_filters["procurement_type"] = req.procurement_type

    fused = await retrieve(
        query=req.query,
        route=route,
        top_k=req.top_k,
        extra_filters=extra_filters or None,
    )

    results = []
    for hit in fused:
        p = hit.get("payload", {})
        results.append(
            TenderSearchResult(
                tot_id=hit["tot_id"],
                score=hit["rrf_score"],
                summary=p.get("summary"),
                description=None,
                country=p.get("country"),
                purchaser_name=p.get("purchaser_name"),
                cpv_code=p.get("cpv_code"),
                cpv_description=p.get("cpv_description"),
                procurement_type=p.get("procurement_type"),
                usd_tender_value=p.get("usd_value"),
                published_date=p.get("published_date"),
                closing_date=p.get("closing_date"),
                tender_notice_document=p.get("document_url"),
            )
        )

    return SearchResponse(
        query=req.query,
        query_type=route.query_type.value,
        weights=route.weights,
        total_hits=len(results),
        results=results,
    )


@router.get("", response_model=SearchResponse)
async def search_tenders_get(
    q: str                          = Query(..., description="Search query"),
    top_k: int                      = Query(10, ge=1, le=100),
    country: Optional[str]          = Query(None),
    cpv_code: Optional[str]         = Query(None),
    procurement_type: Optional[str] = Query(None),
):
    """GET version of search for quick browser/curl testing."""
    return await search_tenders(SearchRequest(
        query=q,
        top_k=top_k,
        country=country,
        cpv_code=cpv_code,
        procurement_type=procurement_type,
    ))
