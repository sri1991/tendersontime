"""
3-step CPV mapping pipeline:
  Step 1: Keyword trie lookup (ahocorasick) — ~60% hit rate, zero LLM cost
  Step 2: Embedding similarity vs. CPV taxonomy — ~30% of remainder
  Step 3: Falls through to Tier 2 LLM for ambiguous ~10%

This module handles Steps 1 and 2.
Step 3 is handled by tier2.py.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import json

import ahocorasick
from loguru import logger

from app.cpv.taxonomy import get_code_map, load_taxonomy
from app.config import get_settings

settings = get_settings()

_KEYWORDS_PATH = (
    __import__("pathlib").Path(__file__).parent.parent.parent / "data" / "cpv_keywords.json"
)


@dataclass
class CPVMatch:
    cpv_code: Optional[str]
    cpv_description: Optional[str]
    confidence: float
    method: str  # "trie", "embedding", "none"


# ---------------------------------------------------------------------------
# Step 1: Keyword trie
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_curated_keywords() -> dict[str, tuple[str, float]]:
    """Load curated keyword → (cpv_code, confidence) map."""
    if not _KEYWORDS_PATH.exists():
        return {}
    with open(_KEYWORDS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    kw = data.get("keywords", {})
    # Normalise: value can be [code, conf] or {"code": ..., "confidence": ...}
    result = {}
    for phrase, val in kw.items():
        if isinstance(val, list) and len(val) == 2:
            result[phrase.lower()] = (str(val[0]), float(val[1]))
    return result


@lru_cache(maxsize=1)
def _build_trie() -> ahocorasick.Automaton:
    """
    Build an Aho-Corasick trie from:
      1. Curated keyword dictionary (high confidence, domain-specific)
      2. CPV taxonomy descriptions (medium confidence fallback)
    """
    code_map = get_code_map()  # {code: description}
    curated = _load_curated_keywords()
    automaton = ahocorasick.Automaton()

    # --- Layer 1: curated keywords (high confidence) ---
    for phrase, (code, conf) in curated.items():
        desc = code_map.get(code, phrase)
        automaton.add_word(phrase, (code, desc, conf))

    # --- Layer 2: CPV taxonomy descriptions (medium confidence fallback) ---
    taxonomy = load_taxonomy()
    _STOP = {"and", "for", "the", "of", "in", "to", "a", "an", "or", "with", "by", "related"}
    for item in taxonomy:
        code = item["code"]
        description = item["description"].lower()

        # Full description phrase → 0.90
        if description not in automaton:
            automaton.add_word(description, (code, item["description"], 0.90))

        # Individual words → 0.65 (only if not already a curated keyword)
        words = [w for w in description.split() if len(w) >= 5 and w not in _STOP]
        for word in words:
            if word not in automaton:
                automaton.add_word(word, (code, item["description"], 0.65))

    automaton.make_automaton()
    logger.info(
        f"CPV trie built: {len(curated)} curated keywords + {len(taxonomy)} taxonomy entries"
    )
    return automaton


def trie_lookup(text: str) -> CPVMatch:
    """
    Step 1: Keyword trie lookup.
    Returns best match (highest confidence) found in text.
    """
    if not text:
        return CPVMatch(None, None, 0.0, "none")

    automaton = _build_trie()
    text_lower = text.lower()

    best_code = None
    best_desc = None
    best_conf = 0.0

    for _, (code, desc, conf) in automaton.iter(text_lower):
        if conf > best_conf:
            best_code = code
            best_desc = desc
            best_conf = conf

    if best_code:
        return CPVMatch(best_code, best_desc, best_conf, "trie")
    return CPVMatch(None, None, 0.0, "none")


# ---------------------------------------------------------------------------
# Step 2: Embedding similarity
# ---------------------------------------------------------------------------

# Cosine similarity calibration constants for gemini-embedding-001.
# Raw cosine is NOT a probability — scores below the floor are noise.
# Linear rescaling: [_COSINE_FLOOR, 1.0] → [0.0, 1.0]
# e.g. raw=0.60 → 0.00, raw=0.80 → 0.50, raw=0.94 → 0.85, raw=1.00 → 1.00
_COSINE_FLOOR = 0.60
_COSINE_SCALE = 1.0 - _COSINE_FLOOR


def _calibrate_cosine(raw_score: float) -> float:
    """Map raw cosine similarity to a calibrated [0, 1] confidence."""
    if raw_score < _COSINE_FLOOR:
        return 0.0
    return round((raw_score - _COSINE_FLOOR) / _COSINE_SCALE, 3)


async def embedding_lookup(text: str, top_k: int = 1) -> CPVMatch:
    """
    Step 2: Embed the tender text and find closest CPV node in Qdrant.
    Only called if trie lookup fails or returns low confidence.
    """
    from app.indexing.embeddings import embed_texts
    from app.indexing.qdrant_client import search_cpv, COLLECTION_CPV, get_qdrant

    # Check if CPV collection has any points first
    client = get_qdrant()
    try:
        info = await client.get_collection(COLLECTION_CPV)
        if info.points_count == 0:
            logger.warning("CPV collection is empty — skipping embedding lookup")
            return CPVMatch(None, None, 0.0, "none")
    except Exception:
        return CPVMatch(None, None, 0.0, "none")

    embeddings = await embed_texts([text[:2000]], task_type="RETRIEVAL_QUERY")
    results = await search_cpv(embeddings[0], top_k=top_k)

    if not results:
        return CPVMatch(None, None, 0.0, "none")

    top = results[0]
    confidence = _calibrate_cosine(float(top["score"]))

    return CPVMatch(
        cpv_code=top["cpv_code"],
        cpv_description=top["description"],
        confidence=confidence,
        method="embedding",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def map_cpv(text: str) -> CPVMatch:
    """
    Run Steps 1 and 2 of the CPV mapping pipeline.

    Returns a CPVMatch with the best code found.
    If confidence < tier0 threshold, the caller should route to Tier 2.
    """
    if not text or len(text.strip()) < 10:
        return CPVMatch(None, None, 0.0, "none")

    # Step 1: Trie
    trie_result = trie_lookup(text)
    if trie_result.confidence >= settings.tier0_cpv_confidence_threshold:
        return trie_result

    # Step 2: Embedding (only if trie didn't get a high-confidence hit)
    emb_result = await embedding_lookup(text)
    if emb_result.confidence > trie_result.confidence:
        return emb_result

    # Return best of what we have (even if low confidence)
    if trie_result.confidence > 0:
        return trie_result

    return emb_result
