import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
import chromadb
import google.generativeai as genai
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
   - Determine if the query implies a specific domain (Healthcare, Infrastructure, IT, Defense, Energy).
   - "Hospital Construction" -> Domain: "Healthcare" (Wait! The guardrail says Hospital Construction finds Clinics but BLOCKS Roads. The enriched data likely tagged "Hospital Construction" as Works/Infrastructure if it's purely building, BUT the brief says:
     - Query: "Hospital Construction" -> Should find "Construction of a new Clinic".
     - Enriched logic said: "Construction of road to City Hospital" -> Infrastructure. 
     - "Construction of a new Clinic" -> ?? That might be Healthcare or Infrastructure/Works depending on the strictness. 
     - **RE-READING BRIEF**: 
       > Query: "Hospital Construction"
       > Should find: "Construction of a new Clinic in Brazil."
       > Should block: "Restoration of BT Road near City Hospital."
       > Logic: "If the tender is for a road... the domain MUST be Transport/Infrastructure."
       
     - This implies "Clinic Construction" remains in "Healthcare" (or "Infrastructure-Health" if we had that). 
     - The strict rule was specifically for ROADS/BRIDGES being Infrastructure regardless of location.
     - So, a "Clinic Building" is likely "Healthcare" + "Works".
     
   - So if user searches "Hospital Construction":
     - Domain: **Healthcare** (Primary intent) OR **Infrastructure** (if they strictly mean civil works).
     - But to block "Roads", we must rely on "Roads" being in "Infrastructure" and "Clinic" being in "Healthcare".
     - Thus Intent MUST be "Healthcare".

2. **Procurement Type (Optional)**:
   - "Construction", "Building" -> Works
   - "Supply", "Purchase" -> Supply
   - "Maintenance" -> Services

3. **Refined Query**:
   - Strip domain keywords to leave the semantic core if needed, or keep it. 
   - Example: "Hospital Construction" -> "Construction of Medical Facility" (Signal).

## Output Schema
Return JSON:
{{
  "core_domains": ["Healthcare", "Infrastructure"], # List allowed domains. Usually just one specific one if clear.
  "procurement_types": ["Works", "Supply", "Services"], # List allowed types.
  "refined_query": "String"
}}
"""

class SmartSearchEngine:
    def __init__(self, api_key: str = None, persist_directory: str = "./chroma_db"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logging.warning("No GEMINI_API_KEY found.")
        genai.configure(api_key=self.api_key)
        
        # Init Vector Store
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection("tenders_v1")
        
        # Init Models
        self.intent_model = genai.GenerativeModel("gemini-2.5-flash-lite")
        
    async def analyze_intent(self, query: str) -> Dict[str, Any]:
        """
        Gemini analyzes query to get filters.
        """
        prompt = INTENT_PROMPT_TEMPLATE.format(query=query)
        config = genai.types.GenerationConfig(response_mime_type="application/json", temperature=0.0)
        
        try:
            response = await self.intent_model.generate_content_async(
                prompt,
                generation_config=config
            )
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logging.error(f"Intent analysis failed: {e}")
            return {}

    def get_embedding(self, text: str) -> List[float]:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_query"
        )
        return result['embedding']

    async def search(self, query: str, k: int = 20, include_corrigendum: bool = False):
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
        
        where_clause = {}
        conditions = []
        
        if domains and len(domains) == 1:
            conditions.append({"core_domain": domains[0]})
        elif domains:
            conditions.append({"core_domain": {"$in": domains}})
            
        # Optional: Apply strict Type filter if detected?
        # Maybe safer to stick to Domain for the "Wall" unless query is specific.
        # Let's apply it if present to demonstrate precision.
        if types and len(types) == 1:
             conditions.append({"procurement_type": types[0]})
        elif types:
             conditions.append({"procurement_type": {"$in": types}})
             
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
            where_clause = None # No restrictions
            
        print(f"DEBUG: Vector Filter: {where_clause}")
        
        # 3. Vector Search
        query_vec = self.get_embedding(refined_query)
        
        # Fetch slightly more to account for post-filtering
        fetch_k = k * 2 if not include_corrigendum else k
        
        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=fetch_k,
            where=where_clause,
            include=["metadatas", "documents", "distances"]
        )
        
        # 4. Runtime Guardrail: Filter by Title text if metadata failed
        # Many old records have is_corrigendum=False but title="Corrigendum: ..."
        if not include_corrigendum:
            final_ids = []
            final_metas = []
            final_dists = []
            final_docs = []
            
            # Chroma results are lists of lists [[]]
            r_ids = results["ids"][0]
            r_metas = results["metadatas"][0]
            r_dists = results["distances"][0]
            r_docs = results["documents"][0] if results.get("documents") else []
            
            for i in range(len(r_ids)):
                title = r_metas[i].get("original_title", "").lower()
                summary = r_metas[i].get("description", "").lower() # Check description too?
                
                # STRING CHECK:
                if "corrigendum" in title:
                    # Skip it
                    continue
                    
                final_ids.append(r_ids[i])
                final_metas.append(r_metas[i])
                final_dists.append(r_dists[i])
                if r_docs: final_docs.append(r_docs[i])
                
                if len(final_ids) >= k:
                    break
            
            # Reconstruct result format
            results["ids"] = [final_ids]
            results["metadatas"] = [final_metas]
            results["distances"] = [final_dists]
            if r_docs: results["documents"] = [final_docs]
            
        return results

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
