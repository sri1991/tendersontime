import chromadb
from src.search.engine import SmartSearchEngine
import asyncio

async def check_record():
    engine = SmartSearchEngine()
    collection = engine.collection
    
    # ID from the previous grep result
    target_id = "124001018" 
    
    result = collection.get(ids=[target_id])
    print(f"--- Metadata for {target_id} ---")
    if result['metadatas']:
        print(result['metadatas'][0])
    else:
        print("Record not found.")

if __name__ == "__main__":
    asyncio.run(check_record())
