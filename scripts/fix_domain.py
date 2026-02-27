import asyncio
import os
import sys
import chromadb
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

TARGET_ID = "124038037"
NEW_DOMAIN = "Agriculture"

async def fix_domain():
    print(f"--- Fixing Domain for ID {TARGET_ID} ---")
    
    engine = SmartSearchEngine()
    collection = engine.collection
    
    # 1. Get existing record
    record = collection.get(ids=[TARGET_ID], include=["metadatas", "documents", "embeddings"])
    
    if not record['ids']:
        print("❌ ID not found in ChromaDB.")
        return
        
    meta = record['metadatas'][0]
    print(f"Current Domain: {meta.get('core_domain')}")
    
    # 2. Update Metadata
    meta['core_domain'] = NEW_DOMAIN
    print(f"New Domain: {meta.get('core_domain')}")
    
    # 3. Upsert (Chroma requires re-inserting everything)
    collection.upsert(
        ids=[TARGET_ID],
        embeddings=record['embeddings'],
        metadatas=[meta],
        documents=record['documents']
    )
    print("✅ updated successfully.")

if __name__ == "__main__":
    asyncio.run(fix_domain())
