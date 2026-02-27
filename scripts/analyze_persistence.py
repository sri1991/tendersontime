import asyncio
import os
import json
import logging
from dotenv import load_dotenv
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

# IDs reported by user as "Still not extracted"
# 124038569: X-Ray Scanner (Security)
# 124035609: Ptz Camera (Security)
# 124031591: Ip Ptz Camera (Security) - NEW?
# 124030904: Solar Electric Fence (Infrastructure/Security)
# 124029979: Fingerprint Canvas (Security)

PROBLEM_IDS = [
    "124038569", "124035609", "124031591", "124030904", "124029979"
]

QUERY = "cctv, RFID, Access control, Surveillance and security systems"

async def analyze_persistence():
    engine = SmartSearchEngine()
    collection = engine.collection
    
    print(f"--- Analyzing {len(PROBLEM_IDS)} Persistent IDs ---")
    print(f"Query: {QUERY}")
    
    # 1. Check Existence & Content
    print("\n[1] Checking Content in ChromaDB...")
    existing = collection.get(ids=PROBLEM_IDS, include=["metadatas", "documents", "embeddings"])
    found_map = {id: {"meta": m, "doc": d, "emb": e} for id, m, d, e in zip(existing['ids'], existing['metadatas'], existing['documents'], existing['embeddings'])}
    
    for pid in PROBLEM_IDS:
        if pid in found_map:
            doc = found_map[pid]['doc']
            meta = found_map[pid]['meta']
            print(f"✅ ID {pid} Found.")
            print(f"   Title: {meta.get('original_title')}")
            print(f"   Domain: {meta.get('core_domain')}")
            print(f"   ProcType: {meta.get('procurement_type')}")
            print(f"   Doc Length: {len(doc)}")
            print(f"   Sample: {doc[:100]}...")
        else:
            print(f"❌ ID {pid} NOT FOUND in ChromaDB.")

    # 2. Analyze Filters
    print("\n[2] Analyzing Search Filters...")
    intent = await engine.analyze_intent(QUERY)
    print(f"Intent: {json.dumps(intent, indent=2)}")
    
    # Reconstruct filter logic from engine.py
    is_broad = intent.get("is_broad_query", False)
    domains = intent.get("core_domains", [])
    
    conditions = []
    if not is_broad and domains:
        if len(domains) == 1:
            conditions.append({"core_domain": domains[0]})
        else:
            conditions.append({"core_domain": {"$in": domains}})
            
    # Simulate Filter Check
    passed_filter = []
    failed_filter = []
    
    if conditions:
        print(f"Active Filters: {conditions}")
        # We can't easily run the exact Chroma filter locally without potentially complex logic if it's nested
        # But let's check the metadata manually against the intent.
        
        for pid in PROBLEM_IDS:
            if pid not in found_map: continue
            meta = found_map[pid]['meta']
            domain = meta.get('core_domain')
            
            # Simple check: Is domain in allowed domains?
            # Note: Engine logic is `if not is_broad`. Here `is_broad` might be True/False.
            if not is_broad:
                if domain in domains:
                    passed_filter.append(pid)
                else:
                    failed_filter.append((pid, domain))
            else:
                passed_filter.append(pid) # Broad query = no domain filter
    else:
        print("No Filters Active (Broad Query).")
        passed_filter = [pid for pid in PROBLEM_IDS if pid in found_map]

    print(f"Passed Filter: {len(passed_filter)}")
    if failed_filter:
        print(f"Blocked by Filter: {failed_filter}")

    # 3. Check Rank / Distance
    print("\n[3] Checking Vector Ranks...")
    # Get Query Embedding
    query_vec = engine.get_embedding(intent.get("refined_query", QUERY))
    
    # Calculate Distances for Found IDs
    # Manual Cosine Distance: 1 - cosine_similarity
    # Chroma returns distance? We have embeddings.
    
    import math
    def dot_product(v1, v2):
        return sum(x*y for x,y in zip(v1, v2))
    def magnitude(v):
        return math.sqrt(sum(x*x for x in v))
    def cosine_distance(v1, v2):
        return 1 - (dot_product(v1, v2) / (magnitude(v1) * magnitude(v2)))

    id_distances = []
    for pid in PROBLEM_IDS:
        if pid in found_map:
            emb = found_map[pid]['emb']
            mag = magnitude(emb)
            dist = cosine_distance(query_vec, emb)
            id_distances.append((pid, dist, mag))
            
    id_distances.sort(key=lambda x: x[1])
    
    print("\nCalculated Distances (Lower is Better):")
    for pid, dist, mag in id_distances:
        print(f"ID {pid}: Dist={dist:.4f}, Mag={mag:.4f}")
        
    # Compare against Top 10 result distances
    print("\nTop 5 Search Results Distances:")
    results = await engine.search(QUERY, k=5)
    for i, dist in enumerate(results['distances'][0]):
        print(f"Rank {i+1}: {dist:.4f} (ID: {results['ids'][0][i]})")

if __name__ == "__main__":
    asyncio.run(analyze_persistence())
