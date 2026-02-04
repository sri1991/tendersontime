
import chromadb
import json

# Connect to ChromaDB
client = chromadb.HttpClient(host='136.114.154.210', port=8002)
collection_name = "tenders_v1"

# List of IDs provided by user
target_ids = [
    "124035656", "124031491", "123979272", "123977238", "123971422",
    "124041277", "124041210", "124034710", "124031620", "124031584",
    "124031444", "124029814", "124029011", "124028346", "124024295",
    "124023494", "124020804", "124015419", "124015070", "124014976",
    "124010163", "124009276", "124005237", "124005031", "124001861",
    "124000966", "123993300", "123993145", "123992737", "123990825",
    "123987524", "123982832", "123978014", "123977774", "123976701",
    "123974116", "123973464", "123972731", "123972605", "123972604"
]

try:
    print(f"Connecting to collection '{collection_name}'...")
    collection = client.get_collection(name=collection_name)
    
    print(f"Checking for {len(target_ids)} IDs...")
    
    # Fetch records
    results = collection.get(ids=target_ids, include=["metadatas"])
    
    found_ids = results['ids']
    missing_ids = [tid for tid in target_ids if tid not in found_ids]
    
    print(f"\nFound: {len(found_ids)}")
    print(f"Missing: {len(missing_ids)}")
    
    if missing_ids:
        print(f"\nMissing IDs: {missing_ids}")
        
    print("\n--- Sample Found Metadata ---")
    for i in range(min(5, len(found_ids))):
         print(f"\nID: {found_ids[i]}")
         print(json.dumps(results['metadatas'][i], indent=2))

except Exception as e:
    print(f"Error: {e}")
