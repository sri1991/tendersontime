
import chromadb
import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

CHROMA_HOST = os.getenv("CHROMA_HOST", "136.114.154.210")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"

TARGET_IDS = ["123959193", "123954119"]

def analyze_two():
    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    collection = client.get_collection(COLLECTION_NAME)
    
    results = collection.get(ids=TARGET_IDS, include=["metadatas"])
    
    print("\n--- METADATA ---")
    for i, tid in enumerate(results['ids']):
        meta = results['metadatas'][i]
        print(f"ID: {tid}")
        print(f"Title: {meta.get('original_title')}")
        print(f"Domain: {meta.get('core_domain')}")
        print(f"Type: {meta.get('procurement_type')}")
        print(f"Is Corrigendum: {meta.get('is_corrigendum')}")
        print("-" * 30)

if __name__ == "__main__":
    analyze_two()
