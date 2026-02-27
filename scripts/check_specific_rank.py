import asyncio
import os
import sys
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

TEST_CASES = [
    ("124035609", "PTZ Camera"),
    ("124031591", "IP PTZ Camera"),
    ("124038569", "Full Body Scanner"),
    ("124030904", "Solar Electric Fence"),
    ("124029979", "Fingerprint Canvas")
]

async def check_ranks():
    engine = SmartSearchEngine()
    print("--- Checking Specific Ranks ---")
    
    for pid, query in TEST_CASES:
        print(f"\nTarget ID: {pid}")
        print(f"Query: '{query}'")
        
        results = await engine.search(query, k=10)
        found_ids = results['ids'][0]
        
        if pid in found_ids:
            rank = found_ids.index(pid) + 1
            print(f"✅ Found at Rank {rank}")
            print(f"   Dist: {results['distances'][0][rank-1]:.4f}")
            print(f"   Title: {results['metadatas'][0][rank-1].get('original_title')}")
        else:
            print(f"❌ NOT Found in Top 10.")
            # Check where it is? (Simulate)
            # engine.collection.get(...)
            
            # Print top 1 to see what beat it
            if results['ids'][0]:
                print(f"   Top 1: {results['metadatas'][0][0].get('original_title')} (Dist: {results['distances'][0][0]:.4f})")
            else:
                print("   Top 1: No results")

if __name__ == "__main__":
    asyncio.run(check_ranks())
