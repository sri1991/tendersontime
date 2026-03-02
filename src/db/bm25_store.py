"""
Persistent BM25 index manager.

Serializes the BM25 index to disk so it survives API restarts without
rebuilding from 85k+ database records every time.

Usage:
    store = BM25Store()
    store.load()                          # load from disk on startup
    store.update(new_ids, new_docs)       # incremental add after ingestion
    scores = store.get_scores(query)      # returns {id: score}
    store.save()                          # persist to disk
"""

import os
import pickle
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = "data/bm25_index"


class BM25Store:
    def __init__(self, index_dir: str = DEFAULT_INDEX_DIR):
        self.index_dir = index_dir
        self.index_file = os.path.join(index_dir, "index.pkl")
        self.ids_file = os.path.join(index_dir, "ids.pkl")

        self.bm25 = None
        self.ids: List[str] = []
        self._loaded = False

    def load(self) -> bool:
        """
        Loads the persisted BM25 index from disk.
        Returns True if loaded successfully, False if not found (first run).
        """
        if not os.path.exists(self.index_file) or not os.path.exists(self.ids_file):
            logger.info("No persisted BM25 index found. Will build on first ingestion.")
            return False

        try:
            with open(self.index_file, "rb") as f:
                self.bm25 = pickle.load(f)
            with open(self.ids_file, "rb") as f:
                self.ids = pickle.load(f)
            self._loaded = True
            logger.info(f"BM25 index loaded from disk: {len(self.ids)} documents.")
            return True
        except Exception as e:
            logger.error(f"Failed to load BM25 index from disk: {e}")
            self.bm25 = None
            self.ids = []
            return False

    def build(self, ids: List[str], documents: List[str]):
        """
        Builds a fresh BM25 index from scratch.
        Used for initial setup or full rebuild.
        """
        if not ids or not documents:
            logger.warning("BM25 build called with empty data.")
            return

        from rank_bm25 import BM25Okapi
        tokenized = [doc.lower().split() for doc in documents]
        self.bm25 = BM25Okapi(tokenized)
        self.ids = list(ids)
        self._loaded = True
        logger.info(f"BM25 index built with {len(self.ids)} documents.")

    def update(self, new_ids: List[str], new_documents: List[str]):
        """
        Incrementally adds new documents to the existing index.
        Since BM25Okapi doesn't support true incremental updates,
        we rebuild only when new docs arrive (cheap for small batches).
        """
        if not new_ids:
            return

        # Merge new docs with existing
        # We re-tokenize only the new docs and append to corpus
        # For a full rebuild, call build() instead
        if self.bm25 is None:
            self.build(new_ids, new_documents)
            return

        from rank_bm25 import BM25Okapi

        # Get existing corpus tokens (stored in bm25 object)
        existing_corpus = self.bm25.corpus if hasattr(self.bm25, 'corpus') else []
        new_tokenized = [doc.lower().split() for doc in new_documents]

        combined_corpus = existing_corpus + new_tokenized
        self.bm25 = BM25Okapi(combined_corpus)
        self.ids = self.ids + list(new_ids)
        logger.info(f"BM25 index updated: now {len(self.ids)} documents.")

    def save(self):
        """Persists the index to disk."""
        if self.bm25 is None:
            logger.warning("Nothing to save — BM25 index is empty.")
            return

        os.makedirs(self.index_dir, exist_ok=True)
        try:
            with open(self.index_file, "wb") as f:
                pickle.dump(self.bm25, f)
            with open(self.ids_file, "wb") as f:
                pickle.dump(self.ids, f)
            logger.info(f"BM25 index saved to {self.index_dir} ({len(self.ids)} documents).")
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    def get_scores(self, query: str, top_k: int = 60) -> Dict[str, float]:
        """
        Returns a dict of {doc_id: bm25_score} for the top_k results.
        Returns empty dict if index not loaded or query yields no results.
        """
        if self.bm25 is None or not self.ids:
            return {}

        tokenized_query = query.lower().split()
        scores = self.bm25.get_scores(tokenized_query)

        # Pair with IDs, filter zero scores, take top_k
        id_score_pairs = [
            (self.ids[i], float(scores[i]))
            for i in range(len(self.ids))
            if scores[i] > 0
        ]
        id_score_pairs.sort(key=lambda x: x[1], reverse=True)
        return dict(id_score_pairs[:top_k])

    @property
    def doc_count(self) -> int:
        return len(self.ids)

    @property
    def is_ready(self) -> bool:
        return self.bm25 is not None and len(self.ids) > 0
