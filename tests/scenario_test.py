import os
import shutil
import asyncio
import json
import logging
import vertexai
from src.enrichment.processor import TenderEnricher
from src.indexing.chroma_loader import ChromaLoader
from src.search.engine import SmartSearchEngine

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Mock Data for Scenarios
TEST_DATA = [
    # Precision Test: Hospital Construction
    {
        "Title": "Construction of road to City Hospital", 
        "Description": "Widening and blacktopping of approach road to the new City Hospital complex.",
        "RefNo": "T001"
    },
    {
        "Title": "Construction of a new Clinic in Brazil",
        "Description": "Civil works for the construction of a 50-bed community health clinic.",
        "RefNo": "T002"
    },
    # Ambiguity Test: Emergency Power
    {
        "Title": "Supply of Backup Generators for Government Buildings",
        "Description": "Procurement of 500kVA Diesel Generators for emergency power backup.",
        "RefNo": "T003"
    },
    {
        "Title": "Purchase of batteries for handheld radios",
        "Description": "Supply of AA and AAA batteries for security staff radios.",
        "RefNo": "T004"
    },
    # Noise/Control
    {
        "Title": "Consultancy for Sugar Plant Efficiency",
        "Description": "Technical study to improve sugar cane processing.",
        "RefNo": "T005"
    }
]

TEMP_JSONL = "test_enriched_data.jsonl"
CHROMA_DIR = "./test_chroma_db"

async def run_scenario_test():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("ERROR: GOOGLE_CLOUD_PROJECT env var not set.")
        return

    print("--- 1. SETUP ---")
    if os.path.exists(CHROMA_DIR):
        shutil.rmtree(CHROMA_DIR)
    if os.path.exists(TEMP_JSONL):
        os.remove(TEMP_JSONL)
        
    vertexai.init(project=project_id, location="us-central1")
        
    print("--- 2. ENRICHMENT (BATCH) ---")
    enricher = TenderEnricher(project_id=project_id)
    enriched_results = await enricher.process_batch(TEST_DATA)
    
    # Save to JSONL
    with open(TEMP_JSONL, 'w') as f:
        for item in enriched_results:
            f.write(json.dumps(item) + "\n")
            
    print("\n[Enrichment Results Audit]")
    for item in enriched_results:
        print(f"ID: {item['RefNo']} | Domain: {item.get('industry_domain')} | Signal: {item.get('signal_summary')}")
        
    print("\n--- 3. INDEXING (CHROMA) ---")
    # Initialize ChromaLoader with custom persist dir
    loader = ChromaLoader(persist_directory=CHROMA_DIR)
    # We call load_from_jsonl but since we have the list, it's easier to just call upsert logic
    # But let's verify the loader class works
    loader.load_from_jsonl(TEMP_JSONL, batch_size=5)
    
    print("\n--- 4. SEARCH VALIDATION ---")
    # Initialize Search Engine
    engine = SmartSearchEngine(project_id=project_id, persist_directory=CHROMA_DIR)
    
    # SCENARIO A: "Hospital Construction"
    # Expectation: T002 (Clinic) FOUND, T001 (Road) BLOCKED/NOT FOUND
    print("\n>>> TEST A: Search for 'Hospital Construction'")
    results_a = await engine.search("Hospital Construction", k=5)
    
    print("\nResults:")
    ids_a = results_a['ids'][0]
    metas_a = results_a['metadatas'][0]
    
    t001_found = "T001" in ids_a
    t002_found = "T002" in ids_a
    
    if t002_found and not t001_found:
        print("✅ SUCCESS: Found Clinic, Blocked Road.")
    elif t001_found:
        print("❌ FAILURE: Found Road to Hospital (Precision Leak!).")
    else:
        print("⚠️ WARNING: Clinic not found?")
        
    # SCENARIO B: "Emergency Power"
    # Expectation: T003 (Generators) FOUND, T004 (Batteries) BLOCKED (or lower ranked, but ideally blocked if domain differs or just semantically far)
    # Batteries might be Supply/Infrastructure or Supply/IT. Generators -> Energy or Infrastructure.
    print("\n>>> TEST B: Search for 'Emergency Power'")
    results_b = await engine.search("Emergency Power", k=5)
    
    ids_b = results_b['ids'][0]
    t003_found = "T003" in ids_b
    t004_found = "T004" in ids_b
    
    if t003_found:
        print("✅ SUCCESS: Found Generators.")
    else:
        print("❌ FAILURE: Generators not found.")
        
    if t004_found:
        print("ℹ️ NOTE: Batteries found. Check ranking/score.")

if __name__ == "__main__":
    asyncio.run(run_scenario_test())
