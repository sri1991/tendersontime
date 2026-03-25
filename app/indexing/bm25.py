"""
BM25 in-memory index over tender searchable text.

Built from PostgreSQL records on startup and after each ingestion batch.
Uses rank-bm25 (BM25Okapi variant).

Design:
  - Stores (tot_id, tokenized_text) in memory
  - Rebuilt incrementally as new records arrive
  - Serialized to Redis for persistence across restarts
"""
from __future__ import annotations

import json
import re
import threading
from typing import Optional

from loguru import logger
from rank_bm25 import BM25Okapi

# ---------------------------------------------------------------------------
# Tokeniser — simple but effective for procurement text
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "in", "of", "to", "by",
    "with", "at", "on", "is", "are", "was", "were", "be", "been",
    "from", "as", "this", "that", "its", "it", "not", "no",
}


def tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation, remove stop words, return word tokens."""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens if len(t) >= 2 and t not in _STOP_WORDS]


# ---------------------------------------------------------------------------
# BM25 Index Manager (singleton)
# ---------------------------------------------------------------------------

class BM25Index:
    """
    Thread-safe BM25 index over tender records.

    Usage:
        index = BM25Index()
        index.add_documents([(tot_id, text), ...])
        results = index.search("GPS tracking warehouse", top_k=20)
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._tot_ids: list[str] = []
        self._corpus: list[list[str]] = []
        self._bm25: Optional[BM25Okapi] = None
        self._dirty: bool = False

    def add_documents(self, documents: list[tuple[str, str]]) -> None:
        """
        Add (tot_id, searchable_text) pairs to the index.
        Marks index dirty — rebuild deferred until next search() call.
        """
        with self._lock:
            for tot_id, text in documents:
                if not text:
                    continue
                self._tot_ids.append(tot_id)
                self._corpus.append(tokenise(text))
            self._dirty = True

    def rebuild_from_records(self, records: list[dict]) -> None:
        """
        Full rebuild from a list of {tot_id, text} dicts.
        Called on startup when loading from DB.
        """
        with self._lock:
            self._tot_ids = []
            self._corpus = []
            for r in records:
                text = r.get("text") or ""
                if text:
                    self._tot_ids.append(r["tot_id"])
                    self._corpus.append(tokenise(text))
            self._rebuild()
            logger.info(f"BM25 index rebuilt with {len(self._tot_ids)} documents")

    def _rebuild(self) -> None:
        if self._corpus:
            self._bm25 = BM25Okapi(self._corpus)
            self._dirty = False

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """
        Search the BM25 index.

        Returns list of {tot_id, score} dicts sorted by score descending.
        Score is normalised to [0, 1] relative to best match in result.
        """
        with self._lock:
            if self._dirty:
                self._rebuild()
            if not self._bm25 or not self._tot_ids:
                return []

            query_tokens = tokenise(query)
            if not query_tokens:
                return []

            raw_scores = self._bm25.get_scores(query_tokens)

            # Get top-k indices
            top_indices = sorted(
                range(len(raw_scores)),
                key=lambda i: raw_scores[i],
                reverse=True,
            )[:top_k]

            max_score = raw_scores[top_indices[0]] if top_indices else 1.0
            if max_score == 0:
                return []

            return [
                {
                    "tot_id": self._tot_ids[i],
                    "score": float(raw_scores[i]) / max_score,  # normalise to [0,1]
                    "rank": rank + 1,
                }
                for rank, i in enumerate(top_indices)
                if raw_scores[i] > 0
            ]

    @property
    def size(self) -> int:
        return len(self._tot_ids)


# Module-level singleton
_bm25_index = BM25Index()


def get_bm25_index() -> BM25Index:
    return _bm25_index


async def load_bm25_from_db() -> None:
    """
    Load all indexed tender records from PostgreSQL into the BM25 index.
    Called on API startup.
    """
    from sqlalchemy import select, text as sa_text
    from app.database import AsyncSessionLocal
    from app.models.tender import TenderRecord

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TenderRecord.tot_id, TenderRecord.summary, TenderRecord.description)
            .where(TenderRecord.is_indexed == True)
        )
        rows = result.fetchall()

    records = [
        {
            "tot_id": row.tot_id,
            "text": " ".join(filter(None, [row.summary, row.description])),
        }
        for row in rows
    ]

    _bm25_index.rebuild_from_records(records)
    logger.info(f"BM25 index loaded: {_bm25_index.size} documents from DB")
