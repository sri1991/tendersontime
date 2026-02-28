import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
import chromadb
# import google.generativeai as genai # REMOVE OLD SDK
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

INTENT_PROMPT_TEMPLATE = """
You are a Search Intent Analyzer for a Tender Database.
Your job is to interpret the user's search query and extract specific metadata filters to ensure precision.

User Query: "{query}"

## Rules
1. **Industry Domain**:
   - Determine if the query implies a specific **BROAD** domain.
   - Allowed Domains: [Healthcare, Infrastructure, Energy, Defense, Technology, Transport, Agriculture, Other].
   - "Hospital Construction" -> Domain: "Healthcare" (Primary) AND "Infrastructure" (Secondary).
   - "Ear Tag" -> Domain: "Agriculture".
   
4. **Broad/Cross-Cutting Queries**:
   - If the query is about a technology, product, or service applicable to MANY sectors (e.g., "Drones", "Computers", "Security Guards", "Vehicles"), marking it as "Technology" or "Transport" might exclude relevant results in "Agriculture" or "Defense".
   - In such cases, set "is_broad_query": true.

## Output Schema
Return JSON:
{{
  "core_domains": ["Healthcare", "Infrastructure"], # List allowed broad domains.
  "procurement_types": ["Works", "Supply", "Services"],
  "refined_query": "String",
  "is_broad_query": boolean # True if query is generic/cross-cutting. False if specific.
}}
"""

class SmartSearchEngine:
    def __init__(self, api_key: str = None, persist_directory: str = "./chroma_db"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logging.warning("No GEMINI_API_KEY found.")
        
        # Init New Client
        self.client_genai = genai.Client(api_key=self.api_key)
        
        # CHROMA CONNECTION LOGIC
        chroma_host = os.getenv("CHROMA_HOST")
        chroma_port = os.getenv("CHROMA_PORT")
        
        if chroma_host and chroma_port:
            logging.info(f"Connecting to ChromaDB Server at {chroma_host}:{chroma_port}...")
            self.client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
        else:
            logging.info(f"Connecting to Local ChromaDB at {persist_directory}...")
            self.client = chromadb.PersistentClient(path=persist_directory)

        self.collection = self.client.get_or_create_collection("tenders_v1")
        self.bm25 = None
        self.bm25_ids = []
        self._initialize_bm25()

    def _initialize_bm25(self):
        """
        Loads all documents from ChromaDB and creates a BM25 index.
        Note: For very large collections, this should be done incrementally or via a persistent BM25 store.
        """
        try:
            count = self.collection.count()
            if count == 0:
                logging.info("ChromaDB is empty. BM25 index not initialized.")
                return

            # Fetch all documents (id and text)
            # Fetching in one go if count is reasonable, else should batch.
            results = self.collection.get(include=["documents"])
            self.bm25_ids = results["ids"]
            documents = results["documents"]

            if not documents:
                logging.warning("No documents found in ChromaDB to index for BM25.")
                return

            from rank_bm25 import BM25Okapi
            tokenized_corpus = [doc.lower().split() for doc in documents]
            self.bm25 = BM25Okapi(tokenized_corpus)
            logging.info(f"BM25 Index initialized with {len(documents)} documents.")
        except Exception as e:
            logging.error(f"Failed to initialize BM25: {e}")

    def _reciprocal_rank_fusion(self, vector_results: List[str], bm25_results: List[str], k=60) -> List[tuple]:
        """
        Combines results from vector search and BM25 using RRF.
        Returns list of (id, score) sorted by score descending.
        """
        scores = {}
        for rank, doc_id in enumerate(vector_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
        for rank, doc_id in enumerate(bm25_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
        
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    async def re_rank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Re-ranks top results using Gemini-2.0-flash.
        """
        if not results:
            return []

        # Prepare candidates for re-ranking
        candidates = []
        for i, res in enumerate(results):
            candidates.append({
                "index": i,
                "title": res.get("original_title", ""),
                "summary": res.get("description", "")[:200] # Limit summary length
            })

        re_rank_prompt = f"""
        You are a highly-critical Search Re-ranking Assistant for a Tender Search Engine.
        User Query: "{query}"

        Below are the top {len(candidates)} candidates fetched by the vector engine.
        Your job is to re-rank them based on their direct relevance to the user's intent.
        
        CRITICAL RULES:
        1. Direct matches to query keywords (e.g. "CCTV", "RFID", "Video Surveillance") must be at the very top.
        2. Related but distinct services (e.g. "Locksmith", "Construction", "Plumbing") should be ranked much lower, even if they share a broad 'Security' or 'Infrastructure' context.
        3. If multiple items are equally relevant, prioritize by specificity of title.
        4. Return a JSON array of the indices of the most relevant items in decreasing order of relevance.
        5. IGNORE candidates that are completely irrelevant.

        CANDIDATES:
        {json.dumps(candidates, indent=2)}

        OUTPUT FORMAT:
        [index1, index2, index3, ...]
        """

        try:
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=re_rank_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            
            re_ranked_indices = json.loads(response.text.strip())
            if not isinstance(re_ranked_indices, list):
                return results

            # Reorder results - only include what LLM thinks is relevant
            final_results = []
            seen_indices = set()
            for idx in re_ranked_indices:
                try:
                    i = int(idx)
                    if 0 <= i < len(results) and i not in seen_indices:
                        final_results.append(results[i])
                        seen_indices.add(i)
                except (ValueError, TypeError):
                    continue
            
            # If the LLM returned too few results, or if we want to be safe, 
            # we can append the rest but maybe we should just trust the LLM 
            # if it returned at least a few.
            if len(final_results) < 5 and len(results) > 5:
                # Add some back to fill the list if it's too empty
                for i in range(len(results)):
                    if i not in seen_indices and len(final_results) < 20:
                        final_results.append(results[i])
                        seen_indices.add(i)

            return final_results
        except Exception as e:
            logging.error(f"Re-ranking failed: {e}")
            return results
        
    async def analyze_intent(self, query: str) -> Dict[str, Any]:
        """
        Gemini analyzes query to get filters.
        Using new SDK model.generate_content
        """
        prompt = INTENT_PROMPT_TEMPLATE.format(query=query)
        
        try:
            # New SDK usage
            # config = types.GenerateContentConfig(...)
            # response = self.client_genai.models.generate_content(model=..., contents=...)
            
            # Using 'gemini-2.0-flash' or 'gemini-2.5-flash-lite' as configured?
            # Sticking to the lite model requested: gemini-2.5-flash-lite
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logging.error(f"Intent analysis failed: {e}")
            return {}

    def get_embedding(self, text: str) -> List[float]:
        try:
            response = self.client_genai.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
            )
            return response.embeddings[0].values
        except Exception as e:
            logging.error(f"Embedding failed: {e}")
            raise

    async def search(self, query: str, k: int = 20, include_corrigendum: bool = True):
        print(f"\n--- Searching for: '{query}' (Corrigendum: {include_corrigendum}) ---")
        
        # 1. Intent Analysis
        intent = await self.analyze_intent(query)
        print(f"DEBUG: Intent Analysis: {intent}")
        
        domains = intent.get("core_domains", [])
        types = intent.get("procurement_types", [])
        refined_query = intent.get("refined_query", query)
        
        # 2. Build ChromaDB Filter
        # ChromaDB 'where' clause construction
        # Simple case: if multiple domains, we might need $or, but Chroma's filter syntax is specific.
        # Start simple: Direct match if 1 domain, or $in if supported (Chroma > 0.4.x supports $in).
        
        is_broad = intent.get("is_broad_query", False)
        
        where_clause = {}
        conditions = []
        
        # Domain Filter: Only apply if NOT broad
        if not is_broad and domains:
            if len(domains) == 1:
                conditions.append({"core_domain": domains[0]})
            else:
                conditions.append({"core_domain": {"$in": domains}})
            
        # Optional: Apply strict Type filter if detected?
        # Maybe safer to stick to Domain for the "Wall" unless query is specific.
        # Let's apply it if present to demonstrate precision.
        # FIX: Also disable for broad queries to include "Unknown" types
        # USER FEEDBACK (Step 622): "we should not restrict basis the procurement at this point"
        # Disabling globally due to "Unknown" data quality issues.
        # if not is_broad and types:
        #    if len(types) == 1:
        #         conditions.append({"procurement_type": types[0]})
        #    else:
        #         conditions.append({"procurement_type": {"$in": types}})
             
        # Corrigendum Filter
        # If include_corrigendum is False, we EXCLUDE them (is_corrigendum != True)
        if not include_corrigendum:
            conditions.append({"is_corrigendum": {"$ne": True}})

        # Combine conditions
        if len(conditions) > 1:
            where_clause = {"$and": conditions}
        elif len(conditions) == 1:
            where_clause = conditions[0]
        else:
            where_clause = None
            
        print(f"DEBUG: Active Where Clause: {json.dumps(where_clause, indent=2)}")
        
        print(f"DEBUG: Vector Filter: {where_clause}")
        
        # 3. Vector Search
        query_vec = self.get_embedding(refined_query)
        
        # Fetch slightly more for hybrid fusion
        fetch_k = k * 3 
        
        vector_results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=fetch_k,
            where=where_clause,
            include=["metadatas", "documents", "distances"]
        )
        
        # 4. Keyword Search (BM25)
        bm25_top_ids = []
        if self.bm25:
            tokenized_query = refined_query.lower().split()
            # Get scores for all docs
            doc_scores = self.bm25.get_scores(tokenized_query)
            # Pair scores with IDs and sort
            id_scores = sorted(zip(self.bm25_ids, doc_scores), key=lambda x: x[1], reverse=True)
            # Filter by fetch_k
            bm25_top_ids = [item[0] for item in id_scores[:fetch_k] if item[1] > 0]

        # 5. Hybrid Fusion (RRF)
        vector_ids = vector_results["ids"][0]
        combined_results = self._reciprocal_rank_fusion(vector_ids, bm25_top_ids)
        
        # Take top k
        top_k_ids = [res[0] for res in combined_results[:k]]
        
        # 6. Fetch full metadata for top_k_ids
        if not top_k_ids:
             return {"ids": [[]], "metadatas": [[]], "distances": [[]], "documents": [[]]}

        # Since we need to maintain order from top_k_ids, we fetch and then re-sort
        final_records = self.collection.get(
            ids=top_k_ids,
            include=["metadatas", "documents"]
        )
        
        # Map for quick lookup
        records_map = {id: (meta, doc) for id, meta, doc in zip(final_records["ids"], final_records["metadatas"], final_records["documents"])}
        
        final_ids = []
        final_metas = []
        final_docs = []
        final_dists = [] # Distances might be lost or need mapping, for now using dummy or RRF score
        
        # 7. Runtime Guardrail: Corrigendum Filter
        for tid in top_k_ids:
            if tid not in records_map: continue
            meta, doc = records_map[tid]
            
            if not include_corrigendum:
                title = meta.get("original_title", "").lower()
                if "corrigendum" in title:
                    continue
            
            final_ids.append(tid)
            final_metas.append(meta)
            final_docs.append(doc)
            # We don't have a single "distance" anymore, could put RRF score here if needed
            final_dists.append(0.0) 
            
            if len(final_ids) >= fetch_k: # Fetching more for re-ranking
                break
        
        # 8. Two-Stage Re-ranking
        # Convert to list of dicts for re-ranker
        results_to_rerank = []
        for i in range(len(final_ids)):
            results_to_rerank.append({
                "id": final_ids[i],
                "original_title": final_metas[i].get("original_title"),
                "description": final_metas[i].get("description"),
                "metadata": final_metas[i],
                "document": final_docs[i],
                "score": final_dists[i] # This is either RRF score or 1.0 (if vector-only)
            })
        
        # Only re-rank top 50
        candidates_to_rerank = results_to_rerank[:50]
        remaining_results = results_to_rerank[50:]
        
        re_ranked_top = await self.re_rank(query, candidates_to_rerank)
        final_ordered_results = re_ranked_top + remaining_results

        # Final take k
        final_take_k = final_ordered_results[:k]
            
        # Generate pseudo-distances based on rank (to avoid all 100% scores)
        # We start at 0.5 (100%) and increase slightly per rank (decaying score)
        # Score calculation in api.py: if d <= 0.5: return 1.0; 0.5 -> 0.7 mapping uses linear decay.
        take_k_dists = [0.50 + (i * 0.02) for i in range(len(final_take_k))]

        return {
            "ids": [[res["id"] for res in final_take_k]],
            "metadatas": [[res["metadata"] for res in final_take_k]],
            "distances": [take_k_dists],
            "documents": [[res["document"] for res in final_take_k]]
        }

    async def chat_with_tender(self, tender_id: str, query: str) -> str:
        """
        Chat with a specific tender context.
        """
        # 1. Fetch Tender Context
        try:
            record = self.collection.get(
                ids=[tender_id],
                include=["metadatas", "documents"]
            )
            
            if not record["ids"]:
                return "Tender not found."
                
            meta = record["metadatas"][0]
            # Use 'documents' (signal text + keywords) as primary context, plus metadata
            context_text = record["documents"][0] if record["documents"] else ""
            
            # 1.1 Fetch Live Content (New Feature)
            # Try to fetch the URL content to give Gemini more details
            url = meta.get('url')
            live_content = ""
            if url and url.startswith("http"):
                try:
                    import httpx
                    import re
                    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            # Simple HTML to Text
                            raw_html = resp.text
                            # Remove script/style
                            clean_text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', raw_html, flags=re.DOTALL)
                            # Remove tags
                            clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
                            # Collapse whitespace
                            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                            live_content = clean_text[:10000] # Limit context window to 10k chars
                except Exception as fetch_err:
                    logging.warning(f"Failed to fetch live URL {url}: {fetch_err}")

            # Construct Context
            context = f"""
            TITLE: {meta.get('original_title', 'Unknown')}
            DESCRIPTION: {meta.get('description', 'Unknown')}
            AUTHORITY: {meta.get('authority_name', 'Unknown')}
            LOCATION: {meta.get('location_city', '')}, {meta.get('location_state', '')}, {meta.get('country', '')}
            CLOSING DATE: {meta.get('closing_date', 'Unknown')}
            URL: {url}
            
            EXTRA DETAILS (Keywords): {context_text}
            
            --- LIVE PAGE CONTENT (FETCHED FROM URL) ---
            {live_content if live_content else "Could not fetch live content."}
            --------------------------------------------
            """
            
            # 2. Generate Answer
            prompt = f"""
            You are a procurement assistant helping a user understand this specific tender. 
            Answer their question using ONLY the provided metadata. 
            If the info is not in the text, say "I don't see that detail in the summary."
            
            TENDER DATA:
            {context}
            
            USER QUESTION: {query}
            """
            
            # response = await self.intent_model.generate_content_async(prompt)
            # New SDK (sync for now unless using async client? strictly client.models is sync? 
            # The new SDK has an async client `genai.Client(http_options={'api_version':...})`? 
            # Actually, standard client operations are synchronous. 
            # If we need async, we must use `genai.Client(..., http_options=...)` or check docs.
            # Docs say: `client.aio.models.generate_content` for async.
            
            response = await self.client_genai.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            return response.text
            
        except Exception as e:
            logging.error(f"Chat Error: {e}")
            return "Sorry, I encountered an error analyzing this tender."

if __name__ == "__main__":
    # Test Stub
    import sys
    if not os.getenv("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY")
        sys.exit(1)
        
    engine = SmartSearchEngine()
    
    if len(sys.argv) > 1:
        asyncio.run(engine.search(sys.argv[1]))
    else:
        print("Usage: python src/search/engine.py 'query'")
