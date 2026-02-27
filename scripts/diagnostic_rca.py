import asyncio
import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
from src.indexing.chroma_loader import ChromaLoader # Needed for embedding generation if not analyzing search engine directly? 
# Actually search engine has embedding model.

load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CSV_PATH = "tender_dataset_06082025_6Jan2026.csv"
LOG_FILE = "missed_ids_low_level_log.json"

def calculate_cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return np.dot(vec_a, vec_b) / (norm_a * norm_b)

async def diagnostic_rca(query: str, missed_id: str):
    logger.info(f"Starting Diagnostic RCA for ID: {missed_id} | Query: '{query}'")
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "missed_id": missed_id,
        "status": "Unknown",
        "category": "Unknown",
        "details": {}
    }

    engine = SmartSearchEngine()
    collection = engine.collection

    # --- Step 1: Check Index Existence ---
    logger.info("Step 1: Checking Index...")
    record = collection.get(ids=[missed_id], include=["metadatas", "documents", "embeddings"])
    
    if not record['ids']:
        logger.warning(f"ID {missed_id} NOT FOUND in ChromaDB.")
        report["status"] = "Not Indexed"
        report["category"] = "Ingestion Failure"
        
        # Check Source CSV if not indexed
        logger.info("Checking Source CSV...")
        try:
            # Grep for speed
            import subprocess
            res = subprocess.run(["grep", "-n", f'"{missed_id}"', CSV_PATH], capture_output=True, text=True)
            if res.stdout:
                line_num = int(res.stdout.split(':')[0])
                report["details"]["source_status"] = f"Found in CSV at line {line_num}"
                if line_num < 85000:
                    report["details"]["source_note"] = "Skipped by Offset (< 85000)"
            else:
                 report["details"]["source_status"] = "Not Found in Source CSV"
        except Exception as e:
            report["details"]["source_error"] = str(e)
            
        return report

    # ID Exists in DB
    report["status"] = "Indexed"
    meta = record['metadatas'][0]
    embedding = record['embeddings'][0]
    document_text = record['documents'][0]
    
    report["details"]["metadata"] = meta
    report["details"]["doc_length"] = len(document_text)

    # --- Step 2: Vector Math (The Retrieval Check) ---
    logger.info("Step 2: Analysis Vector Distance...")
    # Generate query embedding using same model as engine
    # We can access engine.embedding_model or similar logic?
    # SmartSearchEngine uses GoogleGenAIEmbeddings inside Chroma/Langchain wrapper usually, 
    # but here let's assume we can generate it via the same method engine uses.
    # Engine.search() does it internally. let's verify rank first.
    
    # Let's use the engine's internal method if accessible or re-instantiate embedding gen
    # Quickest way: Search and see where it lands.
    
    logger.info("Running live search to find rank...")
    search_results = await engine.search(query, k=500) # Fetch deep to find it
    
    found_at_rank = -1
    found_distance = -1.0
    
    if search_results and search_results['ids']:
        ids_list = search_results['ids'][0]
        dists_list = search_results['distances'][0]
        
        if missed_id in ids_list:
            idx = ids_list.index(missed_id)
            found_at_rank = idx + 1
            found_distance = dists_list[idx]
        else:
            # If not in top 500, we need to manually compute distance to know "how far"
            # Explicitly embed query to compare
            # Using genai directly for this diagnostic
            import google.generativeai as genai
            if not os.getenv("GEMINI_API_KEY"):
                 logger.error("GEMINI_API_KEY not found.")
            
            # Re-embed query manually to compare distance
            # Note: This might differ slightly if engine uses different params, but good for approx
            # Actually, let's use the engine's embedding function if exposed, or just rely on 'Not in 500'
            pass

    report["details"]["rank"] = found_at_rank
    report["details"]["vector_distance_search"] = found_distance

    # --- Step 3: Categorization Logic ---
    
    # Condition A: Retrieval Issue / Close Call
    # Indexed, Rank is "okay" (e.g. 11-50) but missed top 10
    if found_at_rank > 10 and found_at_rank <= 50:
        report["category"] = "Category A: Retrieval Issue (Close Call)"
        report["recommendation"] = "Implement Reranking (Cross-Encoder)"
    
    # Condition B: Semantic Gap
    # Indexed, but Rank is very poor (>50) or Distance is high
    elif found_at_rank > 50 or found_at_rank == -1:
        report["category"] = "Category B: Semantic Gap (Embedding Issue)"
        report["recommendation"] = "Query Expansion / Hybrid Search"
        
        # Check metadata match
        # If domain matches query intent but still low rank -> Definite Semantic Gap
        # If domain MISMATCH -> Metadata Issue (e.g. Hedge Shear Cutter was Unclassified)
        
    # Condition C: Enrichment Erasure (Keyword Loss)
    # Check if query terms are in Original Title/Desc vs Enrichment Summary
    query_terms = [t.lower() for t in query.split() if len(t) > 3] # simple tokenizer
    
    original_text = (meta.get('original_title', '') + " " + meta.get('description', '')).lower()
    enriched_text = (meta.get('signal_summary', '') + " " + meta.get('keywords', '') + " " + meta.get('project_tags', '')).lower()
    
    missing_terms = []
    for term in query_terms:
        if term in original_text and term not in enriched_text:
            missing_terms.append(term)
            
    if missing_terms:
        report["category"] = "Category C: Enrichment Erasure"
        report["details"]["missing_keywords"] = missing_terms
        report["recommendation"] = "Update Enrichment Prompt (Preserve Keywords)"

    # Filter Condition (Special Case)
    # If the user's intent was "Agriculture" but item is "Unclassified"
    # We can simulate intent analysis here or just log the domain
    report["details"]["item_domain"] = meta.get('core_domain')

    # Final Rank check for priority
    if report["category"] == "Unknown":
        if found_at_rank <= 10 and found_at_rank != -1:
             report["category"] = "False Alarm (Ranked in Top 10)"
             report["status"] = "healthy"
        elif found_at_rank == -1:
             report["category"] = "Category B: Semantic Gap (Deep Miss)"

    logger.info(f"Diagnosis Complete: {report['category']}")
    print(json.dumps(report, indent=2))
    
    # Log to file
    try:
        history = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                history = json.load(f)
        history.append(report)
        with open(LOG_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write log: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/diagnostic_rca.py <query> <missed_id>")
        sys.exit(1)
    
    q = sys.argv[1]
    mid = sys.argv[2]
    asyncio.run(diagnostic_rca(q, mid))
