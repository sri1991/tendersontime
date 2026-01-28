import os
from dotenv import load_dotenv
load_dotenv()
import json
import logging
from typing import List, Dict, Any
import chromadb
from chromadb.config import Settings
import google.generativeai as genai # Keep for other files potentially? No, new SDK.
from google import genai
from google.genai import types

# ...

class ChromaLoader:
    def __init__(self, collection_name: str = "tenders_v1", persist_directory: str = "./chroma_db", api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logging.warning("No GEMINI_API_KEY found. Embeddings will fail.")
        
        # Initialize New Client
        self.client_genai = genai.Client(api_key=self.api_key)
        
        # CHROMA CONNECTION LOGIC
        chroma_host = os.getenv("CHROMA_HOST")
        chroma_port = os.getenv("CHROMA_PORT")
        
        if chroma_host and chroma_port:
            logging.info(f"Connecting to ChromaDB Server at {chroma_host}:{chroma_port}...")
            self.client = chromadb.HttpClient(host=chroma_host, port=int(chroma_port))
        else:
            logging.info(f"Connecting to Local ChromaDB at {persist_directory}...")
            self.client = chromadb.PersistentClient(path=persist_directory)
            
        self.collection = self.client.get_or_create_collection(name=collection_name)
        
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generates embeddings using Google GenAI SDK (v2).
        """
        # Batching for API limits
        batch_size = 50 # New SDK might handle larger batches, but sticking to safe limit
        all_embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            try:
                # Use the working model found: gemini-embedding-001
                response = self.client_genai.models.embed_content(
                    model="gemini-embedding-001",
                    contents=batch,
                )
                
                # Response structure is different in new SDK
                # It returns an object with .embeddings attribute which is a list
                if response.embeddings:
                    # Each embedding in the list has .values attribute? Check SDK docs or inspect.
                    # Based on docs: result.embeddings is a list of Embeddings object or list of lists?
                    # "print(result.embeddings)" shows it returns list of Embedding(values=[...]) or similar.
                    # Let's assume standard behavior: extraction needed.
                    
                    # Update: In Python SDK 0.x it was dict. In new SDK it's object.
                    # Let's extract values properly.
                    batch_embeddings = [e.values for e in response.embeddings]
                    all_embeddings.extend(batch_embeddings)
                else:
                    logging.error(f"Empty embeddings response for batch {i}")
                    
            except Exception as e:
                logging.error(f"Error embedding batch {i}-{i+batch_size}: {e}")
                # Crash if real embedding fails, no more mocks
                raise 
                
        return all_embeddings

    def load_from_jsonl(self, jsonl_path: str, batch_size: int = 50):
        """
        Reads enriched JSONL and loads into ChromaDB.
        """
        if not os.path.exists(jsonl_path):
            logging.error(f"File not found: {jsonl_path}")
            return

        with open(jsonl_path, 'r') as f:
            lines = f.readlines()
            
        total = len(lines)
        logging.info(f"Found {total} records to load into ChromaDB.")
        
        # Process in chunks
        for i in range(0, total, batch_size):
            chunk_lines = lines[i:i+batch_size]
            documents = []
            metadatas = []
            ids = []
            
            for line in chunk_lines:
                try:
                    data = json.loads(line)
                    
                    # VALIDATION: Check for failed enrichment
                    if "error" in data:
                        logging.warning(f"Skipping record with error: {data.get('error')}")
                        continue
                        
                    # CRITICAL: Use 'signal_summary' + keywords for embedding
                    signal_text = data.get("signal_summary", "")
                    keywords = ", ".join(data.get("search_keywords", []))
                    tags = ", ".join(data.get("project_tags", []))
                    
                    # Augmented text for better retrieval
                    embedding_text = f"{signal_text}. Tags: {tags}. Keywords: {keywords}"
                    
                    if not signal_text:
                        logging.warning(f"Skipping record with empty signal_summary: {data}")
                        continue
                        
                    # Metadata Guardrails
                    entities = data.get("entities", {})
                    meta = {
                        "core_domain": data.get("core_domain", "Unclassified"),
                        "project_tags": tags,
                        "procurement_type": data.get("procurement_type", "Unknown"),
                        "authority_name": entities.get("authority_name", "Unknown"),
                        "location_city": entities.get("location_city", "Unknown"),
                        "location_state": entities.get("location_state", "Unknown"),
                        "country": data.get("Country", "Unknown"),
                        # Store raw title/desc in metadata for retrieval display
                        "original_title": data.get("Summary", data.get("Title", ""))[:300],
                        "original_title": data.get("Summary", data.get("Title", ""))[:300], 
                        "description": data.get("Description", data.get("signal_summary", ""))[:500],
                        "closing_date": data.get("Closing_Date", "N/A"),
                        "url": data.get("Tender_Notice_Document", "#"),
                        "ref_no": str(data.get("RefNo", hash(signal_text))),
                        "tot_id": str(data.get("TOT_ID", "N/A")),
                        "is_corrigendum": "corrigendum" in (data.get("Summary") or data.get("Title", "")).lower()
                    }
                    
                    # Fix Authority Name if Unknown
                    if meta["authority_name"] in ["Unknown", "N/A"] and data.get("Purchaser_Name"):
                         meta["authority_name"] = data.get("Purchaser_Name")[:100]
                    
                    documents.append(embedding_text)
                    metadatas.append(meta)
                    # Unique ID 
                    ids.append(str(data.get("RefNo", f"hash_{hash(signal_text)}")))
                    
                except json.JSONDecodeError:
                    logging.error(f"Failed to decode JSON line: {line[:50]}...")
            
            if not documents:
                continue
                
            # Generate Embeddings
            logging.info(f"Embedding batch {i} to {i+len(documents)}...")
            try:
                embeddings = self.generate_embeddings(documents)
            except Exception as e:
                logging.error(f"Skipping batch due to embedding error: {e}")
                continue
            
            # Upsert to Chroma
            logging.info(f"Upserting {len(documents)} records to ChromaDB...")
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )
            
        logging.info("ChromaDB loading complete.")
        
if __name__ == "__main__":
    # Mock Test
    import sys
    # Initialize with project ID from env
    if not os.getenv("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY env var.")
        sys.exit(1)
        
    if len(sys.argv) > 1:
        loader = ChromaLoader()
        loader.load_from_jsonl(sys.argv[1])
    else:
        print("Usage: python src/indexing/chroma_loader.py <enriched_data.jsonl>")
