import asyncio
import os
import json
import shutil
from unittest.mock import MagicMock, AsyncMock, patch

# Import our modules
from src.cleaning.cleaner import CurrencyNormalizer, DateStandardizer, Deduplicator
from src.enrichment.processor import TenderEnricher
from src.indexing.chroma_loader import ChromaLoader

# Mock Data
RAW_DATA = [
    {"Title": "Construction of 50-bed Maternity Ward", "Location": "Delhi", "Amount": "5 Cr", "Date": "12-01-2024"},
    {"Title": "Supply of MRI Machines", "Location": "Mumbai", "Amount": "20 Lakhs", "Date": "15/01/2024"},
    {"Title": "Repair of Highway near Hospital", "Location": "Pune", "Amount": "10 K", "Date": "2024-02-01"}
]

async def run_pipeline():
    print("--- Starting Pipeline Verification ---")
    
    # 1. Cleaning Phase
    print("\n[Phase 1] Cleaning Data...")
    cleaned_data = []
    for row in RAW_DATA:
        clean_row = row.copy()
        clean_row["AmountInt"] = CurrencyNormalizer.normalize(row["Amount"])
        clean_row["DateISO"] = DateStandardizer.to_iso(row["Date"])
        clean_row["DedupHash"] = Deduplicator.generate_hash(row["Title"], row["Location"])
        clean_row["RefNo"] = clean_row["DedupHash"] # Use hash as ID
        cleaned_data.append(clean_row)
    
    print(f"Cleaned {len(cleaned_data)} records.")
    print(f"Sample: {cleaned_data[0]['AmountInt']} (Expected 50000000)")

    # 2. Enrichment Phase (Mocked)
    print("\n[Phase 2] Enriching Data (Mocked)...")
    
    # Mock genai
    with patch("src.enrichment.processor.genai") as mock_genai:
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.configure = MagicMock()
        
        # Define mock responses
        responses = [
            """{
                "core_domain": "Infrastructure", 
                "procurement_type": "Works", 
                "search_keywords": ["Clinic", "Healthcare", "Building"], 
                "entities": {"authority_name": "PWD"},
                "signal_summary": "Construction of Maternity Ward"
            }""",
            """{
                "core_domain": "Healthcare", 
                "procurement_type": "Supply", 
                "search_keywords": ["Medical Equipment", "Scan"], 
                "entities": {"authority_name": "AIIMS"},
                "signal_summary": "Supply of MRI Machine"
            }""",
            """{
                "core_domain": "Infrastructure", 
                "procurement_type": "Works", 
                "search_keywords": ["Road", "Tarmac"], 
                "entities": {"authority_name": "NHAI"},
                "signal_summary": "Highway Repair"
            }"""
        ]
        
        # Async mock for generate_content_async
        async_mock = AsyncMock()
        async_mock.side_effect = [MagicMock(text=r) for r in responses]
        mock_model.generate_content_async = async_mock

        # Init with dummy key
        enricher = TenderEnricher(api_key="dummy")
        enriched_data = await enricher.process_batch(cleaned_data)
        
        print(f"Enriched {len(enriched_data)} records.")
        print(f"Sample Domain: {enriched_data[0]['core_domain']}")
    
    # Save to temp jsonl for loader
    with open("temp_enriched.jsonl", "w") as f:
        for item in enriched_data:
            f.write(json.dumps(item) + "\n")

    # 3. Indexing Phase (Mocked Embeddings)
    print("\n[Phase 3] Indexing Data (Mocked)...")
    
    # Mock Chroma Loader dependencies
    with patch("src.indexing.chroma_loader.genai") as mock_genai_loader:
        
        # Mock embedding return
        def mock_embed_content(model, content, task_type):
            # Return dict with 'embedding' key
            return {'embedding': [[0.1]*768 for _ in range(len(content))]}
            
        mock_genai_loader.embed_content.side_effect = mock_embed_content
        mock_genai_loader.configure = MagicMock()
        
        # Use a real (local) Chroma client with a temp dir
        if os.path.exists("temp_chroma"):
            shutil.rmtree("temp_chroma")
            
        loader = ChromaLoader(persist_directory="temp_chroma", api_key="dummy")
        loader.load_from_jsonl("temp_enriched.jsonl", batch_size=2)
        
        count = loader.collection.count()
        print(f"Chroma Collection Count: {count}")
        
    print("\n--- Pipeline Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
