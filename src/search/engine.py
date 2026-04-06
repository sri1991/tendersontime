"""
SmartSearchEngine — pgvector + PostgreSQL full-text search.

Key improvements over v1:
  - pgvector replaces ChromaDB for vector search (SQL-level filtering)
  - PostgreSQL FTS (tsvector/GIN) replaces in-memory BM25 — scales to millions
    of rows, no RAM overhead, no per-process pickle, no rebuild on startup
  - asyncio.gather parallelises intent analysis and query embedding
  - Redis intent cache eliminates LLM call for repeated/similar queries
  - Redis search result cache (10 min TTL) short-circuits identical queries
  - Fully async embedding — no asyncio.to_thread thread overhead
  - Honest multi-signal scoring: vector_sim (60%) + FTS (30%) + freshness (10%)
  - Conditional re-ranking: skip LLM re-rank when top result is already excellent
"""

import os
import json
import logging
import asyncio
from datetime import date, datetime
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv
from sqlalchemy import text

from src.db.schema import engine as db_engine
from src.cache.intent_cache import IntentCache
from src.cache.search_cache import SearchCache

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

INTENT_PROMPT_TEMPLATE = """
You are a Search Intent Analyzer for a Tender Database.
Your job is to interpret the user's search query and extract specific metadata filters to ensure precision.

User Query: "{query}"

## Rules
1. **Industry Domain**:
   - Determine if the query implies a specific BROAD domain.
   - Allowed Domains: [Healthcare, Infrastructure, Energy, Defense, Technology, Transport, Agriculture, Other].
   - "Hospital Construction" -> Domain: "Healthcare" AND "Infrastructure".
   - "Ear Tag" -> Domain: "Agriculture".

2. **Broad/Cross-Cutting Queries**:
   - If the query is about a technology, product, or service applicable to MANY sectors
     (e.g. "Drones", "Computers", "Security Guards", "Vehicles"), set "is_broad_query": true.

## Output Schema
Return JSON:
{{
  "core_domains": ["Healthcare", "Infrastructure"],
  "procurement_types": ["Works", "Supply", "Services"],
  "refined_query": "String",
  "is_broad_query": boolean
}}
"""


def _title_match_score(title: str, query: str) -> float:
    """
    Returns 0.0–1.0 based on the fraction of meaningful query terms
    that appear in the title.  Rewards exact-phrase matches heavily.

    Examples:
      query="animal ear tag", title="Supply of Animal Ear Tags for Cattle" → 1.0
      query="animal ear tag", title="Animal Feed Supply"                   → 0.33
      query="animal ear tag", title="Medical Equipment"                    → 0.0
    """
    if not title or not query:
        return 0.0
    title_lower = title.lower()
    query_lower = query.lower()

    # Full phrase match → maximum score
    if query_lower in title_lower:
        return 1.0

    # Partial: fraction of query words (length > 2) found in title
    words = [w for w in query_lower.split() if len(w) > 2]
    if not words:
        return 0.0
    matches = sum(1 for w in words if w in title_lower)
    return matches / len(words)


def _freshness_score(closing_date_str: Optional[str]) -> float:
    """Converts a closing date string to a freshness score [0.0, 1.0]."""
    if not closing_date_str:
        return 0.4

    try:
        closing = datetime.strptime(closing_date_str, "%Y-%m-%d").date()
        today = date.today()
        days_left = (closing - today).days

        if days_left < 0:
            return 0.1
        if days_left <= 7:
            return 1.0
        if days_left <= 30:
            return 0.8
        if days_left <= 90:
            return 0.6
        return 0.4
    except (ValueError, TypeError):
        return 0.4


def _multi_signal_score(
    vector_distance: float,
    fts_score: float,
    max_fts: float,
    closing_date_str: Optional[str],
    title_match: float = 0.0,
) -> float:
    """
    Combines vector similarity, FTS relevance, title match, and freshness.

    Weights:
      vector_sim  : 0.50
      fts_norm    : 0.25
      title_match : 0.15
      freshness   : 0.10
    """
    vector_sim = max(0.0, 1.0 - vector_distance)
    fts_norm = (fts_score / max_fts) if max_fts > 0 else 0.0
    freshness = _freshness_score(closing_date_str)

    return round(
        0.50 * vector_sim +
        0.25 * fts_norm +
        0.15 * title_match +
        0.10 * freshness,
        4,
    )


class SmartSearchEngine:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY found.")

        self.client_genai = genai.Client(api_key=self.api_key)

        # Redis caches
        self.intent_cache = IntentCache()
        self.search_cache = SearchCache()

        # Determine search backend (postgres or chroma for migration period)
        self.backend = os.getenv("SEARCH_BACKEND", "postgres")
        if self.backend == "chroma":
            self._init_chroma_fallback()

        logger.info(f"SmartSearchEngine initialised (backend={self.backend}).")

    def _init_chroma_fallback(self):
        """Initialise ChromaDB client for migration-period fallback."""
        import chromadb
        chroma_host = os.getenv("CHROMA_HOST")
        chroma_port = os.getenv("CHROMA_PORT")
        if chroma_host and chroma_port:
            self.chroma_client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
        else:
            self.chroma_client = chromadb.PersistentClient(path="./chroma_db")
        self.chroma_collection = self.chroma_client.get_or_create_collection("tenders_v1")

    # ──────────────────────────────────────────────────────────
    # Intent Analysis
    # ──────────────────────────────────────────────────────────

    async def analyze_intent(self, query: str) -> Dict[str, Any]:
        cached = await self.intent_cache.get(query)
        if cached is not None:
            logger.info(f"Intent cache HIT: {query[:50]}")
            return cached

        prompt = INTENT_PROMPT_TEMPLATE.format(query=query)
        try:
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            text_raw = response.text.replace("```json", "").replace("```", "").strip()
            intent = json.loads(text_raw)
            await self.intent_cache.set(query, intent)
            return intent
        except Exception as e:
            logger.error(f"Intent analysis failed: {e}")
            return {}

    # ──────────────────────────────────────────────────────────
    # Embedding  (fully async — no thread overhead)
    # ──────────────────────────────────────────────────────────

    async def get_embedding_async(self, text_input: str) -> List[float]:
        try:
            response = await self.client_genai.aio.models.embed_content(
                model="gemini-embedding-001",
                contents=text_input,
            )
            return response.embeddings[0].values
        except Exception as e:
            logger.error(f"Async embedding failed: {e}")
            raise

    def get_embedding(self, text_input: str) -> List[float]:
        """Synchronous embedding (used by postgres_loader during ingestion)."""
        try:
            response = self.client_genai.models.embed_content(
                model="gemini-embedding-001",
                contents=text_input,
            )
            return response.embeddings[0].values
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise

    # ──────────────────────────────────────────────────────────
    # Vector Search (pgvector)
    # ──────────────────────────────────────────────────────────

    async def _vector_search_postgres(
        self,
        query_vec: List[float],
        domains: List[str],
        is_broad: bool,
        include_corrigendum: bool,
        fetch_k: int,
    ) -> List[Dict[str, Any]]:
        vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

        conditions = ["embedding IS NOT NULL"]
        params: Dict[str, Any] = {"vec": vec_str, "limit": fetch_k}

        if not is_broad and domains:
            conditions.append("core_domain = ANY(:domains)")
            params["domains"] = domains

        if not include_corrigendum:
            conditions.append("is_corrigendum = FALSE")

        where_clause = " AND ".join(conditions)

        sql = text(f"""
            SELECT
                id, tot_id, ref_no, title, description, signal_summary,
                search_keywords, project_tags, core_domain, procurement_type,
                authority_name, location_city, location_state, country,
                closing_date, url, is_corrigendum,
                (embedding <=> CAST(:vec AS halfvec)) AS distance
            FROM tenders
            WHERE {where_clause}
            ORDER BY embedding <=> CAST(:vec AS halfvec)
            LIMIT :limit
        """)

        try:
            async with db_engine.connect() as conn:
                result = await conn.execute(sql, params)
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"pgvector search failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────
    # Full-Text Search (PostgreSQL tsvector/GIN — replaces BM25)
    # ──────────────────────────────────────────────────────────

    async def _fts_search_postgres(
        self,
        query: str,
        fetch_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Runs PostgreSQL full-text search using the pre-built GIN index.

        websearch_to_tsquery handles natural language input (AND by default,
        phrases in quotes, OR/NOT supported).  ts_rank_cd uses cover density
        which is better for short keyword phrases like tender searches.

        Returns full row data including fts_score so no second DB round-trip
        is needed for FTS-only results.
        """
        sql = text("""
            SELECT
                id, tot_id, ref_no, title, description, signal_summary,
                search_keywords, project_tags, core_domain, procurement_type,
                authority_name, location_city, location_state, country,
                closing_date, url, is_corrigendum,
                ts_rank_cd(fts, websearch_to_tsquery('pg_catalog.english'::regconfig, :query)) AS fts_score
            FROM tenders
            WHERE fts @@ websearch_to_tsquery('pg_catalog.english'::regconfig, :query)
            ORDER BY fts_score DESC
            LIMIT :limit
        """)
        try:
            async with db_engine.connect() as conn:
                result = await conn.execute(sql, {"query": query, "limit": fetch_k})
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

    async def _vector_search_chroma(
        self,
        query_vec: List[float],
        where_clause: Optional[Dict],
        fetch_k: int,
    ) -> List[Dict[str, Any]]:
        """ChromaDB fallback for migration period."""
        try:
            results = self.chroma_collection.query(
                query_embeddings=[query_vec],
                n_results=fetch_k,
                where=where_clause,
                include=["metadatas", "documents", "distances"],
            )
            rows = []
            ids = results.get("ids", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            for tid, meta, dist in zip(ids, metas, dists):
                row = dict(meta)
                row["id"] = tid
                row["distance"] = dist
                row["title"] = meta.get("original_title", "")
                rows.append(row)
            return rows
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────
    # RRF Fusion
    # ──────────────────────────────────────────────────────────

    def _reciprocal_rank_fusion(
        self,
        vector_ids: List[str],
        fts_ids: List[str],
        k: int = 60,
    ) -> List[tuple]:
        """
        Combines vector and FTS rankings with Reciprocal Rank Fusion.
        Returns list of (id, rrf_score) sorted descending.
        """
        scores: Dict[str, float] = {}

        for rank, doc_id in enumerate(vector_ids):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

        for rank, doc_id in enumerate(fts_ids):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # ──────────────────────────────────────────────────────────
    # Re-ranking (conditional)
    # ──────────────────────────────────────────────────────────

    async def re_rank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_score: float,
        is_broad: bool,
    ) -> List[Dict[str, Any]]:
        """
        Optionally re-ranks results using gemini-2.0-flash.
        Skipped when the top result is already excellent and query is specific.
        """
        if not results:
            return []

        if not is_broad and top_score >= 0.85:
            logger.info(f"Re-rank skipped (top_score={top_score:.2f}, specific query).")
            return results

        candidates = [
            {
                "index": i,
                "title": r.get("title", ""),
                "summary": str(r.get("description") or r.get("signal_summary", ""))[:200],
            }
            for i, r in enumerate(results)
        ]

        prompt = f"""
You are a Search Re-ranking Assistant for a Tender database.
User Query: "{query}"

Re-rank the candidates below by direct relevance to the query.
Return a JSON array of indices in decreasing order of relevance.
Omit indices that are completely irrelevant.

CANDIDATES:
{json.dumps(candidates, indent=2)}

OUTPUT FORMAT: [index1, index2, ...]
"""
        try:
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            re_ranked_indices = json.loads(response.text.strip())
            if not isinstance(re_ranked_indices, list):
                return results

            final = []
            seen = set()
            for idx in re_ranked_indices:
                i = int(idx)
                if 0 <= i < len(results) and i not in seen:
                    final.append(results[i])
                    seen.add(i)

            # Pad if LLM returned too few
            if len(final) < 5 and len(results) > 5:
                for i in range(len(results)):
                    if i not in seen and len(final) < 20:
                        final.append(results[i])
                        seen.add(i)

            return final
        except Exception as e:
            logger.error(f"Re-ranking failed: {e}")
            return results

    # ──────────────────────────────────────────────────────────
    # Main search entry point
    # ──────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        k: int = 20,
        include_corrigendum: bool = True,
    ) -> Dict[str, Any]:
        logger.info(f"Search: '{query}'")

        # Check search result cache first
        cached = await self.search_cache.get(query, k, include_corrigendum)
        if cached is not None:
            logger.info(f"Search cache HIT: '{query[:50]}'")
            return cached

        fetch_k = k * 3

        # Stage 1: Parallel intent analysis + query embedding (both fully async)
        intent, query_vec = await asyncio.gather(
            self.analyze_intent(query),
            self.get_embedding_async(query),
        )
        logger.info(f"Intent: {intent}")

        domains = intent.get("core_domains", [])
        is_broad = intent.get("is_broad_query", False)
        refined_query = intent.get("refined_query", query)

        # Stage 2: Parallel vector search + FTS search
        if self.backend == "postgres":
            vector_rows, fts_rows = await asyncio.gather(
                self._vector_search_postgres(
                    query_vec, domains, is_broad, include_corrigendum, fetch_k
                ),
                self._fts_search_postgres(refined_query, fetch_k),
            )
        else:
            # ChromaDB fallback — FTS not available in this path
            where_clause = {}
            if not is_broad and domains:
                where_clause = {"core_domain": {"$in": domains}} if len(domains) > 1 else {"core_domain": domains[0]}
            if not include_corrigendum:
                where_clause = {"$and": [where_clause, {"is_corrigendum": {"$ne": True}}]} if where_clause else {"is_corrigendum": {"$ne": True}}
            vector_rows = await self._vector_search_chroma(query_vec, where_clause or None, fetch_k)
            fts_rows = []

        vector_ids = [r["id"] for r in vector_rows]
        distance_map = {r["id"]: r.get("distance", 1.0) for r in vector_rows}
        fts_score_map = {r["id"]: float(r.get("fts_score", 0.0)) for r in fts_rows}
        rows_by_id = {r["id"]: r for r in vector_rows}

        # Merge FTS-only rows into rows_by_id (already have full metadata)
        for r in fts_rows:
            if r["id"] not in rows_by_id:
                rows_by_id[r["id"]] = r
                distance_map[r["id"]] = 1.0  # no vector distance — treat as max distance

        # Stage 3: RRF fusion
        fts_ids = [r["id"] for r in fts_rows]
        fused = self._reciprocal_rank_fusion(vector_ids, fts_ids)
        top_fused_ids = [item[0] for item in fused[:fetch_k]]

        # Stage 4: Assemble ordered result list with multi-signal scores
        max_fts = max(fts_score_map.values()) if fts_score_map else 1.0
        ordered = []
        for tid in top_fused_ids:
            if tid not in rows_by_id:
                continue
            row = rows_by_id[tid]
            if not include_corrigendum and row.get("is_corrigendum"):
                continue

            dist = distance_map.get(tid, 1.0)
            fts_s = fts_score_map.get(tid, 0.0)
            closing = str(row.get("closing_date") or "")
            title_match = _title_match_score(row.get("title", ""), refined_query)

            score = _multi_signal_score(dist, fts_s, max_fts, closing or None, title_match)
            row["_score"] = score
            ordered.append(row)

            if len(ordered) >= fetch_k:
                break

        # Stage 5: Conditional re-ranking
        top_score = ordered[0]["_score"] if ordered else 0.0
        rerank_candidates = ordered[:50]
        remaining = ordered[50:]

        reranked = await self.re_rank(query, rerank_candidates, top_score, is_broad)
        final = (reranked + remaining)[:k]

        result = {
            "ids":       [[r["id"] for r in final]],
            "metadatas": [[{key: v for key, v in r.items() if not key.startswith("_")} for r in final]],
            "distances": [[r["_score"] for r in final]],
            "documents": [[r.get("embedding_text", "") for r in final]],
            "meta": {
                "intent": intent,
                "backend": self.backend,
            },
        }

        # Populate search result cache
        await self.search_cache.set(query, k, include_corrigendum, result)

        return result

    # ──────────────────────────────────────────────────────────
    # Chat with a specific tender
    # ──────────────────────────────────────────────────────────

    async def chat_with_tender(self, tender_id: str, user_query: str) -> str:
        try:
            async with db_engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT * FROM tenders WHERE id = :id"),
                    {"id": tender_id},
                )
                row = result.mappings().fetchone()

            if not row:
                return "Tender not found."

            row = dict(row)
            url = row.get("url", "")
            live_content = ""

            if url and url.startswith("http"):
                try:
                    import httpx, re
                    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", resp.text, flags=re.DOTALL)
                            clean = re.sub(r"<[^>]+>", " ", clean)
                            live_content = re.sub(r"\s+", " ", clean).strip()[:10000]
                except Exception as fe:
                    logger.warning(f"Failed to fetch live URL {url}: {fe}")

            context = f"""
TITLE: {row.get('title', 'Unknown')}
DESCRIPTION: {row.get('description', 'Unknown')}
AUTHORITY: {row.get('authority_name', 'Unknown')}
LOCATION: {row.get('location_city', '')}, {row.get('location_state', '')}, {row.get('country', '')}
CLOSING DATE: {row.get('closing_date', 'Unknown')}
URL: {url}

KEYWORDS: {', '.join(row.get('search_keywords') or [])}
TAGS: {', '.join(row.get('project_tags') or [])}

--- LIVE PAGE CONTENT ---
{live_content or 'Could not fetch live content.'}
"""
            prompt = f"""
You are a procurement assistant. Answer the user's question using ONLY the tender data below.
If information is missing, say "I don't see that detail in the summary."

TENDER DATA:
{context}

USER QUESTION: {user_query}
"""
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            return response.text

        except Exception as e:
            logger.error(f"Chat error: {e}")
            return "Sorry, I encountered an error analysing this tender."


if __name__ == "__main__":
    import sys
    if not os.getenv("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY")
        sys.exit(1)

    engine_obj = SmartSearchEngine()
    if len(sys.argv) > 1:
        asyncio.run(engine_obj.search(sys.argv[1]))
    else:
        print("Usage: python src/search/engine.py 'query'")
