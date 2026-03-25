"""
Query Router — classifies each incoming query and returns retrieval weights.

Query types:
  - SEMANTIC   : conceptual / descriptive ("AI-powered fleet management")
  - KEYWORD    : specific product/code ("30213100", "hydraulic pump 50bar")
  - CPV        : explicit CPV lookup ("CPV 45310000", "electrical works")
  - MIXED      : hybrid — use all indexes with balanced weights

Weights returned:
  {semantic: float, bm25: float, cpv: float}
  — used by the RRF fusion layer to blend ranked lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class QueryType(str, Enum):
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    CPV = "cpv"
    MIXED = "mixed"


@dataclass
class RouteDecision:
    query_type: QueryType
    weights: dict[str, float]   # {semantic, bm25, cpv}
    cpv_filter: str | None       # e.g. "45" → filter by CPV division
    clean_query: str             # query with CPV prefix stripped


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

# Explicit CPV mentions: "CPV 45310000", "cpv:45310000", "45310000-6"
_CPV_PREFIX_RE = re.compile(r"\bcpv[:\s]*(\d{2,8})\b", re.I)
_CPV_RAW_RE    = re.compile(r"\b(\d{8})(?:-\d)?\b")             # 8-digit code
_CPV_DIV_RE    = re.compile(r"\b(\d{2})\b")                     # 2-digit division

# Short queries (≤ 3 meaningful words) → lean keyword
_SHORT_QUERY_THRESHOLD = 3

# Long / descriptive queries → lean semantic
_LONG_QUERY_THRESHOLD = 8

# Procurement-domain conceptual signal words
_SEMANTIC_SIGNALS = re.compile(
    r"\b(solution|platform|system|service|management|monitoring|integration|"
    r"development|consultancy|advisory|training|support|implementation|"
    r"procurement|framework|strategy|assessment|evaluation)\b",
    re.I,
)

# Specific-product / code signal words
_KEYWORD_SIGNALS = re.compile(
    r"\b([A-Z]{2,}-\d+|v\d+\.\d+|\d+\s*(?:kw|hp|mhz|ghz|mm|cm|kg|ton|l|ml|bar|psi|rpm))\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def route_query(query: str) -> RouteDecision:
    """
    Classify a query and return retrieval weights + optional CPV filter.

    This is rule-based (zero cost). For Phase 3 we can replace with a
    small fine-tuned classifier.
    """
    query = query.strip()
    cpv_filter: str | None = None
    clean_query = query

    # --- Check for explicit CPV mention ---
    cpv_prefix_match = _CPV_PREFIX_RE.search(query)
    cpv_raw_match    = _CPV_RAW_RE.search(query)

    if cpv_prefix_match:
        cpv_filter = cpv_prefix_match.group(1)
        clean_query = _CPV_PREFIX_RE.sub("", query).strip()
        return RouteDecision(
            query_type=QueryType.CPV,
            weights={"semantic": 0.3, "bm25": 0.2, "cpv": 0.5},
            cpv_filter=cpv_filter,
            clean_query=clean_query or query,
        )

    if cpv_raw_match:
        cpv_filter = cpv_raw_match.group(1)
        clean_query = _CPV_RAW_RE.sub("", query).strip()
        return RouteDecision(
            query_type=QueryType.CPV,
            weights={"semantic": 0.2, "bm25": 0.2, "cpv": 0.6},
            cpv_filter=cpv_filter,
            clean_query=clean_query or query,
        )

    # --- Count meaningful words (non-stop) ---
    from app.indexing.bm25 import tokenise
    tokens = tokenise(query)
    word_count = len(tokens)

    has_semantic_signal = bool(_SEMANTIC_SIGNALS.search(query))
    has_keyword_signal  = bool(_KEYWORD_SIGNALS.search(query))

    # --- Short + specific → keyword-heavy ---
    if word_count <= _SHORT_QUERY_THRESHOLD and not has_semantic_signal:
        return RouteDecision(
            query_type=QueryType.KEYWORD,
            weights={"semantic": 0.2, "bm25": 0.6, "cpv": 0.2},
            cpv_filter=None,
            clean_query=query,
        )

    # --- Long + descriptive → semantic-heavy ---
    if word_count >= _LONG_QUERY_THRESHOLD or has_semantic_signal:
        return RouteDecision(
            query_type=QueryType.SEMANTIC,
            weights={"semantic": 0.6, "bm25": 0.2, "cpv": 0.2},
            cpv_filter=None,
            clean_query=query,
        )

    # --- Default: balanced mix ---
    return RouteDecision(
        query_type=QueryType.MIXED,
        weights={"semantic": 0.4, "bm25": 0.4, "cpv": 0.2},
        cpv_filter=None,
        clean_query=query,
    )
