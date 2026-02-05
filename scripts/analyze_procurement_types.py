
import chromadb
import os
import sys
from collections import Counter
from dotenv import load_dotenv

# Path patch
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

CHROMA_HOST = os.getenv("CHROMA_HOST", "136.114.154.210")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"

def analyze_types():
    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = client.get_collection(COLLECTION_NAME)
        
        # Chroma doesn't allow "select distinct", so we must fetch metadata.
        # For large DBs, we should page. How big is it?
        count = collection.count()
        print(f"Total Records: {count}")
        
        limit = 2000 # Sample size for speed, or loop for all
        print(f"Sampling first {limit} records...")
        
        results = collection.get(
            limit=limit,
            include=["metadatas"]
        )
        
        types = []
        for meta in results['metadatas']:
            p_type = meta.get("procurement_type", "Missing")
            types.append(p_type)
            
        counter = Counter(types)
        
        print("\n--- Procurement Type Distribution (Sample) ---")
        for ptype, count in counter.most_common():
            print(f"{ptype:<25} : {count}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze_types()
