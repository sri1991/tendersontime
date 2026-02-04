import asyncio
import os
from dotenv import load_dotenv
from src.search.engine import SmartSearchEngine
import json

# Load env vars
load_dotenv()

async def evaluate_query(query: str):
    print(f"\n--- Evaluating Query: '{query}' ---\n")
    
    try:
        engine = SmartSearchEngine()
        results = await engine.search(query, k=5)
        
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        
        print(f"Found {len(ids)} results:\n")
        
        for i, tender_id in enumerate(ids):
            meta = metadatas[i]
            dist = distances[i]
            
            print(f"Rank {i+1}:")
            print(f"  Title: {meta.get('original_title')}")
            print(f"  Score (Distance): {dist:.4f}")
            print(f"  Core Domain: {meta.get('core_domain')}")
            print(f"  Sub Domain: {meta.get('sub_domain')}")
            print(f"  Summary: {meta.get('description')}")
            print("-" * 40)
            
    except Exception as e:
        print(f"Error during evaluation: {e}")

if __name__ == "__main__":
    asyncio.run(evaluate_query("anti cancer drug"))
