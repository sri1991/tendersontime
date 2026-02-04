
import chromadb
import json

client = chromadb.HttpClient(host='136.114.154.210', port=8002)
collection = client.get_collection("tenders_v1")

# IDs of "Unmanned Aerial Vehicle" tenders mentioned
uav_ids = ["123979272", "123977238", "124031491"]

print("--- UAV Tender Domains ---")
results = collection.get(ids=uav_ids, include=["metadatas"])

for i, meta in enumerate(results['metadatas']):
    if meta:
        print(f"ID: {results['ids'][i]}")
        print(f"Title: {meta.get('original_title')}")
        print(f"Domain: {meta.get('core_domain')}")
        print("---")
