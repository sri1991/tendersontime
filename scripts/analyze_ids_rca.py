
import chromadb
import json
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Config
CHROMA_HOST = os.getenv("CHROMA_HOST", "34.61.156.171")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"

TARGET_IDS = [
    "124041424", "124040164", "124038569", "124035609", "124030904",
    "124029979", "124027036", "124026699", "124024823", "124024587",
    "124024386", "124023327", "124021450", "124020530", "124020406",
    "124020398", "124020356", "124019998", "124019371", "124017991",
    "124017183", "124016840", "124016273", "124015693", "124015684",
    "124015440", "124012453", "124011948", "124009381", "124009368",
    "124009363", "124007693", "124005124", "124000476"
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
