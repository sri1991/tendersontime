import asyncio
import os
import json
import logging
from typing import List
from dotenv import load_dotenv
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine

load_dotenv()

# List of IDs provided by the user (extracted from the message)
MISSING_IDS = [
    "124041424", "124038569", "124035609", "124030904", "124029979",
    "124027036", "124026699", "124024823", "124024587", "124024386",
    "124023327", "124021450", "124020530", "124020406", "124020398",
    "124020356", "124019998", "124019371", "124017991", "124017183",
    "124016840", "124016273", "124015693", "124015684", "124012453",
    "124009381", "124009368", "124009363", "124007693", "124005124",
    "124000476", "123997519", "123997224", "123995557", "123995142",
    "123995128", "123995036", "123994569", "123994424", "123993990",
    "123993132", "123993077", "123993043", "123993039", "123993033",
    "123993026", "123993018", "123993014", "123991808", "123990596",
    "123987689", "123984805", "123984717", "123981612", "123981596",
    "123980503", "123980229", "123977721", "123977128", "123977092",
    "123976406", "123975199", "123974527", "123974375", "123972950",
    "123972760", "123972056", "123971374", "123970893", "123969917",
    "123969804", "123967962", "123967961", "123966605", "123965486",
    "123965178", "123965020", "123964002", "123963807", "123961662",
    "123961180", "123961009", "123960747", "123959968", "123959518",
    "123958150", "123956687", "123954340", "124041422"
]

QUERY = "cctv, RFID, Access control, Surveillance and security systems"

async def debug_search():
    engine = SmartSearchEngine()
    
    # 1. Analyze Intent
    print(f"\n--- 1. Intent Analysis ---")
    intent = await engine.analyze_intent(QUERY)
    print(json.dumps(intent, indent=2))
    
    # 2. Check Existence of IDs
    print(f"\n--- 2. Checking Existence of {len(MISSING_IDS)} IDs ---")
    collection = engine.collection
    existing = collection.get(ids=MISSING_IDS, include=["metadatas"])
    found_ids = set(existing['ids'])
    missing_from_db = [mid for mid in MISSING_IDS if mid not in found_ids]
    
    print(f"Found in DB: {len(found_ids)}")
    print(f"Missing from DB: {len(missing_from_db)}")
    if missing_from_db:
        print(f"Missing IDs: {missing_from_db}")

    if not found_ids:
        print("No IDs found in DB. Aborting search debug.")
        return

    # 3. Simulate Search and Rank
    print(f"\n--- 3. Running Search and Checking Ranks ---")
    
    # Get query embedding
    query_vec = engine.get_embedding(intent.get("refined_query", QUERY))
    
    # We want to see:
    # A) Does the filter exclude them?
    # B) What is their distance?
    
    # Construct the filter just like engine.py does
    is_broad = intent.get("is_broad_query", False)
    domains = intent.get("core_domains", [])
    
    where_clause = {}
    conditions = []
    
    if not is_broad and domains:
        if len(domains) == 1:
            conditions.append({"core_domain": domains[0]})
        else:
            conditions.append({"core_domain": {"$in": domains}})
    
    # Note: engine.py applies `include_corrigendum` logic.
    # The user request set "include_corrigendum": true, so we do NOT add {"is_corrigendum": {"$ne": True}}
    
    if len(conditions) > 1:
        where_clause = {"$and": conditions}
    elif len(conditions) == 1:
        where_clause = conditions[0]
    else:
        where_clause = None
        
    print(f"Generated Filter: {where_clause}")
    
    # Fetch specific records with embedding to calculate distance manually?
    # Or just query the DB for THESE IDs with the filter to see if they match the filter?
    
    # Check if they match the filter
    if where_clause:
        print("\n--- Checking Filter Compliance ---")
        filtered_results = collection.get(
            ids=list(found_ids),
            where=where_clause,
            include=["metadatas"]
        )
        passed_filter_ids = set(filtered_results['ids'])
        blocked_by_filter = [fid for fid in found_ids if fid not in passed_filter_ids]
        print(f"Passed Filter: {len(passed_filter_ids)}")
        print(f"Blocked by Filter: {len(blocked_by_filter)}")
        if blocked_by_filter:
            print("Examples blocked:", blocked_by_filter[:5])
            # Show metadata of blocked
            blocked_meta = [m for i, m in enumerate(existing['metadatas']) if existing['ids'][i] in blocked_by_filter[:1]]
            print("Blocked Metadata Sample:", blocked_meta)
    else:
        print("No filter applied.")
        
    # Check Distance/Rank
    print("\n--- Checking Vector Distances ---")
    # We query for these specific IDs to get their distance to the query
    # Chroma doesn't support "get distance for specific IDs relative to query" directly easily without fetching.
    # We can fetch embeddings and calculate cosine distance, or query with filter IDs.
    
    target_results = collection.query(
        query_embeddings=[query_vec],
        n_results=len(found_ids),
        where={"id": {"$in": list(found_ids)}} if len(found_ids) < 2000 else None, # Chroma might not support $in for input IDs in get? Use get for embeddings.
        include=["distances", "metadatas", "documents"]
    )
    
    # Actually, let's just do a search for top 500 and see if they appear.
    print(f"\n--- Performing Top 500 Search ---")
    search_results = await engine.search(QUERY, k=500, include_corrigendum=True)
    search_ids = search_results['ids'][0]
    search_distances = search_results['distances'][0]
    
    found_in_top_500 = [fid for fid in MISSING_IDS if fid in search_ids]
    print(f"Found in Top 500: {len(found_in_top_500)}")
    
    if len(search_distances) > 0:
        print(f"Min Distance (Rank 1): {min(search_distances)}")
        print(f"Max Distance (Rank {len(search_distances)}): {max(search_distances)}")
    
    # Inspect outliers
    not_in_top_500 = [fid for fid in found_ids if fid not in search_ids]
    print(f"Not in Top 500: {len(not_in_top_500)}")
    
    if not_in_top_500:
        print("\n--- Analyzing Outliers (in DB, not in results) ---")
        # Get their embeddings/distances manually
        # Recalculate distance
        outlier_data = collection.get(ids=not_in_top_500[:5], include=["embeddings", "metadatas", "documents"])
        # print sample
        for i in range(len(outlier_data['ids'])):
            print(f"ID: {outlier_data['ids'][i]}")
            print(f"Title: {outlier_data['metadatas'][i].get('original_title')}")
            print(f"Domain: {outlier_data['metadatas'][i].get('core_domain')}")
            print(f"Doc (Full): {outlier_data['documents'][i]}")
            print("-" * 20)
            
    # 4. Check specific problem IDs Deep Dive
    problem_ids = ["124024823", "124017991"]
    print("\n--- Deep Dive on Problem IDs ---")
    deep_dive_res = collection.get(ids=problem_ids, include=["embeddings", "metadatas", "documents"])
    for i in range(len(deep_dive_res['ids'])):
        print(f"ID: {deep_dive_res['ids'][i]}")
        print(f"Full Doc: {deep_dive_res['documents'][i]}")
        # Calculate distance to query if possible?
        # Manually: cosine distance
        # But we don't have numpy here easily without importing it.
        # Just printing doc is enough to see if description is missing.
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(debug_search())
