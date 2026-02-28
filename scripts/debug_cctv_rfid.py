import asyncio
import logging
from src.search.engine import SmartSearchEngine
import os
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def debug_search(query: str):
    engine = SmartSearchEngine()
    print(f"\nSearching for: '{query}'")
    
    # 1. Intent Analysis
    intent = await engine.analyze_intent(query)
    print(f"\n--- Intent Analysis ---\n{json.dumps(intent, indent=2)}")
    
    # 2. Perform search (bypass re-ranking for initial check if needed, or see final)
    # Actually, let's see what re_rank adds.
    
    results = await engine.search(query, k=10)
    
    ids = results.get("ids", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    
    print(f"\n--- Final Results (k=10) ---")
    for i, (tid, meta) in enumerate(zip(ids, metas)):
        print(f"[{i+1}] ID: {tid} | Title: {meta.get('original_title')} | Domain: {meta.get('core_domain')}")

if __name__ == "__main__":
    import sys
    query = "cctv, RFID"
    if len(sys.argv) > 1:
        query = sys.argv[1]
    asyncio.run(debug_search(query))
