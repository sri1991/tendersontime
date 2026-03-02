"""
SmartSearchEngine — re-architected for PostgreSQL + pgvector.

Key improvements over v1:
  - pgvector replaces ChromaDB for vector search (enables SQL-level filtering by date, domain, etc.)
  - asyncio.gather parallelises intent analysis and query embedding (~400ms saved per query)
  - Redis intent cache eliminates LLM call for repeated/similar queries
  - BM25 index loaded from disk (not rebuilt from DB on every startup)
  - Honest multi-signal scoring: vector_sim (60%) + BM25 (30%) + freshness (10%)
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
from src.db.bm25_store import BM25Store
from src.cache.intent_cache import IntentCache

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


def _freshness_score(closing_date_str: Optional[str]) -> float:
    """
    Converts a closing date string to a freshness score [0.0, 1.0].
    Urgent tenders (closing soon) score higher.
    """
    if not closing_date_str:
        return 0.4  # unknown date — neutral

    try:
        closing = datetime.strptime(closing_date_str, "%Y-%m-%d").date()
        today = date.today()
        days_left = (closing - today).days

        if days_left < 0:
            return 0.1   # already closed — still show, but low boost
        if days_left <= 7:
            return 1.0   # urgent
        if days_left <= 30:
            return 0.8
        if days_left <= 90:
            return 0.6
        return 0.4       # far future
    except (ValueError, TypeError):
        return 0.4


def _multi_signal_score(
    vector_distance: float,
    bm25_score: float,
    max_bm25: float,
    closing_date_str: Optional[str],
) -> float:
    """
    Combines vector similarity, BM25 relevance, and freshness into a single score [0.0, 1.0].

    Weights:
      vector_sim : 0.6
      bm25_norm  : 0.3
      freshness  : 0.1
    """
    vector_sim = max(0.0, 1.0 - vector_distance)
    bm25_norm = (bm25_score / max_bm25) if max_bm25 > 0 else 0.0
    freshness = _freshness_score(closing_date_str)

    return round(0.6 * vector_sim + 0.3 * bm25_norm + 0.1 * freshness, 4)


class SmartSearchEngine:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY found.")

        self.client_genai = genai.Client(api_key=self.api_key)

        # BM25 from disk — fast startup, no DB rebuild
        self.bm25_store = BM25Store()
        loaded = self.bm25_store.load()
        if not loaded:
            logger.warning("BM25 index not found on disk. Keyword search will be unavailable until first ingestion.")

        # Redis intent cache
        self.intent_cache = IntentCache()

        # Determine search backend (postgres or chroma for migration period)
        self.backend = os.getenv("SEARCH_BACKEND", "postgres")
        if self.backend == "chroma":
            self._init_chroma_fallback()

        logger.info(f"SmartSearchEngine initialised (backend={self.backend}, BM25={'ready' if self.bm25_store.is_ready else 'empty'}).")

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
        # 1. Check Redis cache first
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

            # Store in cache
            await self.intent_cache.set(query, intent)
            return intent
        except Exception as e:
            logger.error(f"Intent analysis failed: {e}")
            return {}

    # ──────────────────────────────────────────────────────────
    # Embedding
    # ──────────────────────────────────────────────────────────

    def get_embedding(self, text_input: str) -> List[float]:
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
        """
        Runs pgvector cosine similarity search with SQL-level filters.
        Returns list of dicts with all tender fields + distance.
        """
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
                rows = result.mappings().all()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"pgvector search failed: {e}")
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
        bm25_scores: Dict[str, float],
        k: int = 60,
    ) -> List[tuple]:
        """
        Combines vector and BM25 rankings with RRF.
        Returns list of (id, rrf_score) sorted descending.
        """
        scores: Dict[str, float] = {}

        for rank, doc_id in enumerate(vector_ids):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

        bm25_ranked = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (doc_id, _) in enumerate(bm25_ranked):
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

        # Conditional skip: clear winner on a specific query
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
        fetch_k = k * 3

        # Stage 1: Parallel intent analysis + query embedding
        intent, query_vec = await asyncio.gather(
            self.analyze_intent(query),
            asyncio.to_thread(self.get_embedding, query),
        )
        logger.info(f"Intent: {intent}")

        domains = intent.get("core_domains", [])
        is_broad = intent.get("is_broad_query", False)
        refined_query = intent.get("refined_query", query)

        # Stage 2: Vector search
        if self.backend == "postgres":
            vector_rows = await self._vector_search_postgres(
                query_vec, domains, is_broad, include_corrigendum, fetch_k
            )
        else:
            # ChromaDB fallback
            where_clause = {}
            if not is_broad and domains:
                where_clause = {"core_domain": {"$in": domains}} if len(domains) > 1 else {"core_domain": domains[0]}
            if not include_corrigendum:
                where_clause = {"$and": [where_clause, {"is_corrigendum": {"$ne": True}}]} if where_clause else {"is_corrigendum": {"$ne": True}}
            vector_rows = await self._vector_search_chroma(query_vec, where_clause or None, fetch_k)

        vector_ids = [r["id"] for r in vector_rows]
        distance_map = {r["id"]: r.get("distance", 1.0) for r in vector_rows}
        rows_by_id = {r["id"]: r for r in vector_rows}

        # Stage 3: BM25 keyword search
        bm25_scores = self.bm25_store.get_scores(refined_query, top_k=fetch_k) if self.bm25_store.is_ready else {}

        # Stage 4: RRF fusion
        fused = self._reciprocal_rank_fusion(vector_ids, bm25_scores)
        top_fused_ids = [item[0] for item in fused[:fetch_k]]

        # Stage 5: Fetch BM25-only rows not in vector results (metadata + actual distance)
        bm25_only_ids = [tid for tid in top_fused_ids if tid not in rows_by_id]
        if bm25_only_ids and self.backend == "postgres":
            try:
                vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
                async with db_engine.connect() as conn:
                    result = await conn.execute(
                        text("SELECT id, tot_id, ref_no, title, description, signal_summary, "
                             "search_keywords, project_tags, core_domain, procurement_type, "
                             "authority_name, location_city, location_state, country, "
                             "closing_date, url, is_corrigendum, "
                             "(embedding <=> CAST(:vec AS halfvec)) AS distance "
                             "FROM tenders WHERE id = ANY(:ids)"),
                        {"ids": bm25_only_ids, "vec": vec_str},
                    )
                    for row in result.mappings().all():
                        d = dict(row)
                        distance_map[d["id"]] = d.get("distance", 1.0)
                        rows_by_id[d["id"]] = d
            except Exception as e:
                logger.error(f"BM25-only metadata fetch failed: {e}")

        # Stage 6: Assemble ordered result list
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
        ordered = []
        for tid in top_fused_ids:
            if tid not in rows_by_id:
                continue
            row = rows_by_id[tid]
            if not include_corrigendum and row.get("is_corrigendum"):
                continue

            dist = distance_map.get(tid, 1.0)
            bm25_s = bm25_scores.get(tid, 0.0)
            closing = str(row.get("closing_date") or "")

            score = _multi_signal_score(dist, bm25_s, max_bm25, closing or None)
            row["_score"] = score
            row["_bm25_only"] = row.get("bm25_only", False)
            ordered.append(row)

            if len(ordered) >= fetch_k:
                break

        # Stage 7: Conditional re-ranking
        top_score = ordered[0]["_score"] if ordered else 0.0
        rerank_candidates = ordered[:50]
        remaining = ordered[50:]

        reranked = await self.re_rank(query, rerank_candidates, top_score, is_broad)
        final = (reranked + remaining)[:k]

        return {
            "ids":       [[r["id"] for r in final]],
            "metadatas": [[{k: v for k, v in r.items() if not k.startswith("_")} for r in final]],
            "distances": [[r["_score"] for r in final]],
            "documents": [[r.get("embedding_text", "") for r in final]],
            "meta": {
                "intent": intent,
                "bm25_available": self.bm25_store.is_ready,
                "backend": self.backend,
            },
        }

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
