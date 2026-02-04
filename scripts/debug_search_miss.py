
import asyncio
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
from src.search.engine import SmartSearchEngine

load_dotenv()

async def debug_search():
    engine = SmartSearchEngine()
    query = "drones"
    
    print(f"--- Debugging Search for '{query}' ---")
    
    # 1. Analyze Intent
    intent = await engine.analyze_intent(query)
    print(f"Intent Result: {intent}")
    
    is_broad = intent.get("is_broad_query", False)
    types = intent.get("procurement_types", [])
    
    # 2. Replicate Logic
    conditions = []
    
    # Domain filter skipped if broad (we know this works)
    
    # Procurement Type Filter
    if types:
        if len(types) == 1:
            conditions.append({"procurement_type": types[0]})
        else:
            conditions.append({"procurement_type": {"$in": types}})
            
    print(f"Applied Conditions: {conditions}")
    
    # 3. Check specific ID metadata
    target_id = "124031584"
    record = engine.collection.get(ids=[target_id])
    if not record['ids']:
        print(f"Target ID {target_id} not found in DB.")
        return

    meta = record['metadatas'][0]
    p_type = meta.get("procurement_type", "Unknown")
    print(f"Target Record Procurement Type: '{p_type}'")
    
    # 4. Check if it passes filter
    passes = False
    if not types:
        passes = True
    elif p_type in types:
        passes = True
        
    print(f"Does record pass filter? {passes}")
    
    if not passes:
        print("CONCLUSION: Record is filtered out because its procurement_type is not in the intent list.")

if __name__ == "__main__":
    asyncio.run(debug_search())
