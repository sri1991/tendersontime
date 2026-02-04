
import pandas as pd
import chromadb
import os
import asyncio
import json
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.enrichment.processor import TenderEnricher
from src.indexing.chroma_loader import ChromaLoader
from dotenv import load_dotenv

load_dotenv()

# Configuration
CSV_PATH = "tender_dataset_06082025_6Jan2026.csv"
CHROMA_HOST = "136.114.154.210"
CHROMA_PORT = 8002
COLLECTION_NAME = "tenders_v1"

# Target IDs to force ingest
TARGET_IDS = [
    "124035656", "124031491", "123979272", "123977238", "123971422",
    "124041277", "124041210", "124034710", "124031584", "124031444",
    "124029814", "124029011", "124024295", "124023494", "124020804",
    "124015419", "124015070", "124014976", "124010163", "124009276",
    "124005237", "124005031", "124001861", "124000966", "123993145",
    "123992737", "123990825", "123987524", "123982832", "123978014",
    "123977774", "123976701", "123974116", "123973464", "123972731",
    "123972605", "123972604", "123972596", "123972548", "123972525",
    "123972253", "123972244", "123972243", "123971838", "123967982",
    "123966361", "123966357", "123966352", "123966272", "123964677",
    "123964558", "123960101", "123959193", "123957155", "123956095",
    "123956006", "123955893", "123954774", "123954119", "123953735",
    "123953632", "123953627", "124031620", "123993300", "124028346"
]

async def ingest_missing():
    print(f"Loading dataset from {CSV_PATH}...")
    df = pd.read_csv(CSV_PATH)
    
    # Identify ID column (assuming 'TOT_ID' based on inspection logic)
    # The previous run didn't print columns so I'll be safe
    id_col = None
    for col in df.columns:
        if col.lower() in ['tot_id', 'id', 'tender id', 'refno']:
            id_col = col
            break
    
    if not id_col: id_col = df.columns[0]
    df[id_col] = df[id_col].astype(str)
    
    subset = df[df[id_col].isin(TARGET_IDS)]
    print(f"Found {len(subset)} records matching target IDs.")
    
    if len(subset) == 0: return

    # Initialize
    print("Initializing Enricher...")
    enricher = TenderEnricher()
    
    # Initialize Loader with monkeypatched env
    os.environ["CHROMA_HOST"] = CHROMA_HOST
    os.environ["CHROMA_PORT"] = str(CHROMA_PORT)
    loader = ChromaLoader(collection_name=COLLECTION_NAME)
    
    records = subset.to_dict('records')
    documents = []
    metadatas = []
    ids = []
    
    print("Processing records...")
    for record in records:
        try:
            # Map title/desc for enricher
            title = record.get("Summary") or record.get("Title") or ""
            desc = record.get("Description") or ""
            
            # Enrich
            enrichment_result = await enricher.enrich_tender(title, desc)
            
            # Merge
            data = record.copy()
            data.update(enrichment_result)
            
            # Prepare for Chroma
            signal_text = data.get("signal_summary", "")
            keywords = ", ".join(data.get("search_keywords", []))
            tags = ", ".join(data.get("project_tags", []))
            
            embedding_text = f"{signal_text}. Tags: {tags}. Keywords: {keywords}"
            
            if not signal_text: continue
            
            entities = data.get("entities", {})
            meta = {
                "core_domain": data.get("core_domain", "Unclassified"),
                "project_tags": tags,
                "procurement_type": data.get("procurement_type", "Unknown"),
                "authority_name": entities.get("authority_name", "Unknown"),
                "location_city": entities.get("location_city", "Unknown"),
                "location_state": entities.get("location_state", "Unknown"),
                "country": data.get("Country", "Unknown"),
                "original_title": (data.get("Summary") or data.get("Title") or "")[:300],
                "description": str(data.get("Description") or data.get("signal_summary") or "")[:500],
                "closing_date": data.get("Closing_Date", "N/A"),
                "url": data.get("Tender_Notice_Document", "#"),
                "ref_no": str(data.get("RefNo", hash(signal_text))),
                "tot_id": str(data.get("TOT_ID", "N/A")),
                "is_corrigendum": "corrigendum" in (data.get("Summary") or data.get("Title", "")).lower()
            }
            
            id_val = str(data.get("TOT_ID", data.get("RefNo")))
            
            documents.append(embedding_text)
            metadatas.append(meta)
            ids.append(id_val)
            print(f"Processed {id_val}")
            
        except Exception as e:
            print(f"Error processing record: {e}")

    if ids:
        print(f"Upserting {len(ids)} records...")
        embeddings = loader.generate_embeddings(documents)
        loader.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        print("Done.")

if __name__ == "__main__":
    asyncio.run(ingest_missing())
