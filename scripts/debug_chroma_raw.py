import asyncio
import os
import json
import logging
from dotenv import load_dotenv
import sys
import chromadb

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

PROBLEM_IDS = ["124038569", "124035609", "124031591", "124030904", "124029979"]
QUERY = "cctv, RFID, Access control, Surveillance and security systems"

async def debug_chroma():
    engine = SmartSearchEngine()
    print("--- Debugging Chroma Retrieval ---")
    
    # 1. Get Query Embedding
    query_vec = engine.get_embedding(QUERY) # Raw query, or refined?
    # Engine uses refined. Let's use refined.
    intent = await engine.analyze_intent(QUERY)
    refined_query = intent.get("refined_query", QUERY)
    print(f"Refined Query: {refined_query}")
    query_vec = engine.get_embedding(refined_query)
    
    # 2. Raw Chroma Query
    # We want to ask Chroma: "Give me the closest 500 items"
    # And check if PROBLEM_IDS are in there.
    
    print("\nQuerying Chroma (k=500)...")
    results = engine.collection.query(
        query_embeddings=[query_vec],
        n_results=500,
        include=["metadatas", "distances"]
    )
    
    found_ids = results['ids'][0]
    found_dists = results['distances'][0]
    
    print(f"Got {len(found_ids)} results.")
    print(f"Top 1 Distance: {found_dists[0]}")
    print(f"Rank 500 Distance: {found_dists[-1]}")
    
    for pid in PROBLEM_IDS:
        if pid in found_ids:
            rank = found_ids.index(pid)
            dist = found_dists[rank]
            print(f"✅ ID {pid} found at Rank {rank+1}. Distance: {dist}")
        else:
            print(f"❌ ID {pid} NOT found in Top 500.")
            
    # 3. Targeted Query
    # Query ONLY these IDs to see what Chroma thinks their distance is
    print("\nTargeted Query (Filter by IDs)...")
    target_results = engine.collection.get(
        ids=PROBLEM_IDS,
        include=["embeddings"]
    )
    
    if target_results['embeddings']:
        # We have to manually calc distance again?
        pass 
        
if __name__ == "__main__":
    asyncio.run(debug_chroma())
