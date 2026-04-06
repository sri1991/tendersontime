import asyncio
import logging
from src.search.engine import SmartSearchEngine
import os

logging.basicConfig(level=logging.WARNING)

async def debug_missing(ids: list, query: str):
    engine = SmartSearchEngine()
    
    print(f"\n=== Checking {len(ids)} IDs in ChromaDB ===\n")
    
    # 1. Check if IDs exist in chroma using ref_no metadata
    found = []
    not_found = []
    
    for tid in ids:
        results = engine.collection.get(
            where={"ref_no": str(tid)},
            include=["metadatas"]
        )
        if results["ids"]:
            meta = results["metadatas"][0]
            found.append(tid)
            print(f"✅ FOUND: {tid} | Title: {meta.get('original_title')} | Domain: {meta.get('core_domain')} | ChromaID: {results['ids'][0]}")
        else:
            not_found.append(tid)
            print(f"❌ NOT INDEXED: {tid}")
    
    print(f"\n=== Summary: {len(found)} found, {len(not_found)} not indexed ===")
    
    if found:
        print(f"\n=== Running search for '{query}' and checking if any found IDs appear ===\n")
        search_results = await engine.search(query, k=20)
        retrieved_ids = search_results.get("ids", [[]])[0]
        
        # Get ref_nos for the retrieved ChromaDB IDs
        retrieved_refs = []
        if retrieved_ids:
            meta_results = engine.collection.get(ids=retrieved_ids, include=["metadatas"])
            for meta in meta_results.get("metadatas", []):
                retrieved_refs.append(meta.get("ref_no", ""))
        
        print(f"Retrieved ref_nos in top 20: {retrieved_refs}")
        
        for tid in found:
            if str(tid) in retrieved_refs:
                print(f"✅ {tid} IS in top 20 results")
            else:
                print(f"⚠️  {tid} is indexed but NOT in top 20 results")

if __name__ == "__main__":
    missing_ids = [
        "123956687", "123955092", "123954671",
        "123954340", "123953637", "124041423", "124041422"
    ]
    asyncio.run(debug_missing(missing_ids, "cctv, RFID"))
