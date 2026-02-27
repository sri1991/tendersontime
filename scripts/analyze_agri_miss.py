import asyncio
import os
import sys
import pandas as pd
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

TARGET_ID = "124038037"
QUERY_BROAD = "Agricultural machines"
QUERY_SPECIFIC = "Hedge Shear Cutter"

CSV_PATH = "tender_dataset_06082025_6Jan2026.csv"

async def analyze_agri():
    print(f"--- Analyzing ID {TARGET_ID} ---")
    
    # 1. Check Source CSV
    print(f"\n[1] Checking Source CSV: {CSV_PATH}")
    # fast check with grep? or pandas if fast enough. 
    # Let's use grep for speed and line number
    import subprocess
    try:
        result = subprocess.run(
            ["grep", "-n", f'"{TARGET_ID}"', CSV_PATH],
            capture_output=True, text=True
        )
        if result.stdout:
            print(f"✅ Found in CSV: {result.stdout.strip()[:200]}...")
            line_num = int(result.stdout.split(':')[0])
            print(f"   Line Number: {line_num}")
            if line_num < 85000:
                print("   ⚠️ WARNING: In skipped range (< 85000)!")
        else:
            print("❌ NOT FOUND in Source CSV.")
    except Exception as e:
        print(f"Error checking CSV: {e}")

    # 2. Check ChromaDB
    print(f"\n[2] Checking ChromaDB Index...")
    host = os.getenv("CHROMA_HOST", "34.61.156.171")
    port = int(os.getenv("CHROMA_PORT", "8002"))
    try:
        engine = SmartSearchEngine()
        import chromadb
        engine.client = chromadb.HttpClient(host=host, port=port)
        engine.collection = engine.client.get_collection("tenders_v1")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return
    collection = engine.collection
    
    record = collection.get(ids=[TARGET_ID], include=["metadatas", "documents"])
    
    if record['ids']:
        print("✅ Found in ChromaDB.")
        meta = record['metadatas'][0]
        doc = record['documents'][0]
        print(f"   Title: {meta.get('original_title')}")
        print(f"   Domain: {meta.get('core_domain')}")
        print(f"   Desc Length: {len(doc)}")
        print(f"   Content: {doc[:200]}...")
        
        # 3. Check Rank
        print(f"\n[3] Checking Rank for '{QUERY_BROAD}'...")
        results = await engine.search(QUERY_BROAD, k=50)
        found = False
        for i, rid in enumerate(results['ids'][0]):
            if rid == TARGET_ID:
                print(f"✅ Found at Rank {i+1}")
                found = True
                break
        if not found:
            print("❌ NOT Found in Top 50.")
            
        print(f"\n[4] Checking Rank for '{QUERY_SPECIFIC}'...")
        results_spec = await engine.search(QUERY_SPECIFIC, k=50)
        found_spec = False
        for i, rid in enumerate(results_spec['ids'][0]):
            if rid == TARGET_ID:
                print(f"✅ Found at Rank {i+1}")
                found_spec = True
                break
        if not found_spec:
            print("❌ NOT Found in Top 50.")
            
    else:
        print("❌ NOT FOUND in ChromaDB.")

if __name__ == "__main__":
    asyncio.run(analyze_agri())
