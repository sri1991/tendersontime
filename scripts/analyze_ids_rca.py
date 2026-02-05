
import chromadb
import json
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Config
CHROMA_HOST = os.getenv("CHROMA_HOST", "136.114.154.210")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"

TARGET_IDS = [
    "123998691", "124015419", "124015070", "123993145", "123992377",
    "123987524", "123987286", "123982832", "123978014", "123977391",
    "123973464", "123960101", "123959193", "123954119"
]

def analyze_ids():
    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = client.get_collection(COLLECTION_NAME)
        
        print(f"Analyzing {len(TARGET_IDS)} IDs...")
        results = collection.get(
            ids=TARGET_IDS,
            include=["metadatas"]
        )
        
        found_ids = set(results['ids'])
        
        print("\n--- METADATA ANALYSIS ---")
        print(f"{'ID':<12} | {'Domain':<15} | {'Type':<10} | {'Corrigendum':<5} | {'Title'}")
        print("-" * 100)
        
        for i, tid in enumerate(results['ids']):
            meta = results['metadatas'][i]
            title = meta.get('original_title', 'N/A')[:40]
            domain = meta.get('core_domain', 'N/A')
            ptype = meta.get('procurement_type', 'N/A')
            is_corr = meta.get('is_corrigendum', False)
            
            print(f"{tid:<12} | {domain:<15} | {ptype:<10} | {str(is_corr):<5} | {title}...")

        print("\n--- MISSING IDs ---")
        for tid in TARGET_IDS:
            if tid not in found_ids:
                print(tid)
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze_ids()
