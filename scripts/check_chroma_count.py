
import os
from dotenv import load_dotenv
import chromadb
import logging

load_dotenv()

def count_records():
    collection_name = "tenders_v1"
    persist_directory = "./chroma_db"
    
    chroma_host = "136.114.154.210"
    chroma_port = "8002"
    
    if chroma_host and chroma_port:
        print(f"Connecting to ChromaDB Server at {chroma_host}:{chroma_port}...")
        client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
    else:
        print(f"Connecting to Local ChromaDB at {persist_directory}...")
        client = chromadb.PersistentClient(path=persist_directory)
        
    try:
        collection = client.get_collection(name=collection_name)
        count = collection.count()
        print(f"Total records in '{collection_name}': {count}")
    except Exception as e:
        print(f"Error accessing collection: {e}")

if __name__ == "__main__":
    count_records()
