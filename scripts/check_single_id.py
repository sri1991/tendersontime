
import chromadb
import json
import os
from dotenv import load_dotenv

load_dotenv()

# Config
CHROMA_HOST = os.getenv("CHROMA_HOST", "136.114.154.210")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"

def check_id(tender_id):
    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = client.get_collection(COLLECTION_NAME)
        
        print(f"Querying for ID: {tender_id}")
        result = collection.get(
            ids=[tender_id],
            include=["metadatas", "documents"]
        )
        
        if result['ids']:
            print(f"\n--- Found Tender {tender_id} ---")
            meta = result['metadatas'][0]
            doc = result['documents'][0]
            print(json.dumps(meta, indent=2))
            print(f"\nDocument Content (Embed Text):\n{doc}")
        else:
            print(f"\nTender {tender_id} NOT FOUND in collection '{COLLECTION_NAME}'.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_id("124031584")
