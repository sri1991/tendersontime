
import asyncio
import sys
from src.search.engine import SmartSearchEngine

async def debug_distances(query):
    print(f"--- Debugging Distances for: '{query}' ---")
    engine = SmartSearchEngine()
    
    # Force include_corrigendum=True to see everything for stats
    results = await engine.search(query, k=10, include_corrigendum=True)
    
    ids = results["ids"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]
    
    print(f"\n{'Score':<10} | {'Dist':<10} | {'Title'}")
    print("-" * 80)
    
    for i, dist in enumerate(dists):
        # New Calibrated Formula
        min_dist = 0.5
        max_dist = 1.15
        
        if dist <= min_dist:
            curr_score = 100.0
        elif dist >= max_dist:
            curr_score = 0.0
        else:
            curr_score = ((max_dist - dist) / (max_dist - min_dist)) * 100
        title = metas[i].get("original_title", "N/A")[:60]
        print(f"{curr_score:5.1f}%     | {dist:5.4f}     | {title}")

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "animal ear tag"
    asyncio.run(debug_distances(query))
