import chromadb
import sys
import json
import os
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("CHROMA_HOST", "34.61.156.171")
port = int(os.getenv("CHROMA_PORT", "8002"))

client = chromadb.HttpClient(host=host, port=port)
collection = client.get_or_create_collection("tenders_v1")

ids = [
    "124032274", "124031661", "124031569", "124029737",
    "124029484", "124029458", "124029359", "124029077"
]

results = collection.get(ids=ids, include=["metadatas"])

for i, id_val in enumerate(results['ids']):
    meta = results['metadatas'][i]
    print(f"--- ID: {id_val} ---")
    print(f"Domain: {meta.get('core_domain')}")
    print(f"Original Title: {meta.get('original_title')}")
