import os
import sys
import chromadb
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(".", "src")))
sys.path.append(os.path.abspath("."))
load_dotenv()

host = os.getenv("CHROMA_HOST", "34.61.156.171")
port = int(os.getenv("CHROMA_PORT", "8002"))

try:
    client = chromadb.HttpClient(host=host, port=port)
    collection = client.get_or_create_collection("tenders_v1")
except Exception as e:
    print(f"Failed to connect to ChromaDB at {host}:{port}: {e}")
    sys.exit(1)

ids = [
    "124040748", "124038885", "124038037", "124037999", 
    "124037397", "124037268", "124036384", "124036343", 
    "124035040", "124035038", "124034457"
]

records = collection.get(ids=ids, include=["metadatas"])
found_ids = set(records["ids"])

print("--- Missing from Chroma DB ---")
missing = [i for i in ids if i not in found_ids]
for m in missing:
    print(m)

print("\n--- Found in Chroma DB (Checking core_domain) ---")
if records["metadatas"]:
    for id_val, meta in zip(records["ids"], records["metadatas"]):
        domain = meta.get("core_domain", "N/A")
        print(f"{id_val}: {domain}")
