import chromadb
import os
from dotenv import load_dotenv

load_dotenv()

def check_count():
    client = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_or_create_collection(name="tenders_v1")
    count = collection.count()
    print(f"Total documents in collection: {count}")

if __name__ == "__main__":
    check_count()
