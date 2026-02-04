
import chromadb
import sys
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO)

try:
    print("Attempting to connect to ChromaDB...")
    # Try creating client with explicit settings if needed, or just default
    # If the server is older, the path might be different, but the python client handles versioning usually.
    # The 404 on heartbeat suggests the server might be running but the endpoint is different or it's not a Chroma server?
    # Or maybe we need to use the V1 client explicitly if the server is old? 
    # But let's stick to standard HttpClient first.
    
    # Use the IP that was working previously if possible, or the one in the code.
    # The user replaced 'your-vm-ip' with '136.114.154.210' before.
    client = chromadb.HttpClient(host='136.114.154.210', port=8002)
    
    print(f"Client created: {client}")
    
    # Try a simple list collections call instead of heartbeat to test connectivity
    print("Listing collections...")
    collections = client.list_collections()
    print(f"Collections found: {[c.name for c in collections]}")
    
    collection_name = "tenders_v1"
    collection = client.get_collection(name=collection_name)
    print(f"Accessed collection: {collection_name}")
    
    # If successful, check for IDs
    target_ids = [
        "124035656", "124031491", "123979272", "123977238", "123971422"
    ]
    
    results = collection.get(ids=target_ids)
    print(f"Found {len(results['ids'])} out of {len(target_ids)} tested IDs.")
    
except Exception as e:
    print(f"Connection failed: {e}")
    # Print more debug info if possible
    import traceback
    traceback.print_exc()
