import asyncio
import os
import json
import logging
import pandas as pd
from dotenv import load_dotenv
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search.engine import SmartSearchEngine
load_dotenv()

# Config
CSV_PATH = "tender_dataset_06082025_6Jan2026.csv"
NEW_MISSING_IDS = [
    "123965020", "123964002", "123963807", "123961662", "123961180",
    "123961009", "123960747", "123959968", "123959518", "123956687",
    "123954340", "124041422"
]

async def categorize_issues():
    engine = SmartSearchEngine()
    collection = engine.collection
    
    print(f"--- Analyzing {len(NEW_MISSING_IDS)} IDs ---")
    
    # 1. Check ChromaDB Existence & Content
    existing = collection.get(ids=NEW_MISSING_IDS, include=["metadatas", "documents"])
    found_map = {id: {"meta": m, "doc": d} for id, m, d in zip(existing['ids'], existing['metadatas'], existing['documents'])}
    
    # 2. Check Source CSV Existence
    # We'll stream the CSV to find line numbers if possible, or just load relevant cols
    # Since it's large, let's just use grep via subprocess for exact matches to get line numbers?
    # Or just pandas chunked.
    
    print("Checking Source CSV...")
    source_map = {}
    # Optimization: Read CSV once, checking for our IDs
    # But for 12 IDs, grep is faster.
    
    issues = []
    
    for tid in NEW_MISSING_IDS:
        issue = {"id": tid, "category": "Unknown", "details": ""}
        
        # Check Source
        # We can simulate checking if it's in the first 85000 lines vs later
        # But let's check DB first.
        
        if tid in found_map:
            # It IS in DB. Check Quality.
            doc_content = found_map[tid]['doc']
            meta = found_map[tid]['meta']
            
            # Heuristic for "Poor Indexing":
            # If doc content is short (< 300 chars) AND metadata exists
            # Or if keywords like "PTZ", "Biometric" are in Title but NOT in Doc?
            
            title = meta.get('original_title', '').lower()
            doc_lower = doc_content.lower()
            
            # Check for "Rich Description" presence (simple heuristic)
            # If doc is largely just the title + tags, it's poor.
            
            if len(doc_content) < len(title) + 50: # Arbitrary threshold
                issue["category"] = "STILL Low Quality (Fix Failed?)"
                issue["details"] = f"Doc length: {len(doc_content)}. Content: {doc_content[:100]}..."
            else:
                issue["category"] = "VERIFIED: Fixed (Full Description Present)"
                issue["details"] = f"Doc length: {len(doc_content)}. Content starts: {doc_content[:50]}..."
                
        else:
            # Not in DB. Why?
            # Check source CSV
            # Using grep to find line number
            import subprocess
            try:
                # grep -n "12345" file.csv
                result = subprocess.run(
                    ["grep", "-n", f'"{tid}"', CSV_PATH],
                    capture_output=True, text=True
                )
                if result.stdout:
                    line_str = result.stdout.strip().split(':')[0]
                    line_num = int(line_str)
                    
                    if line_num < 85000:
                        issue["category"] = "Ingestion Skip (Config)"
                        issue["details"] = f"Found at line {line_num}. Skipped by START_OFFSET=85000."
                    else:
                        issue["category"] = "Ingestion Unknown Failure"
                        issue["details"] = f"Found at line {line_num} (Should have been processed?)"
                else:
                    issue["category"] = "Source Data Missing"
                    issue["details"] = "ID not found in source CSV 'tender_dataset_06082025_6Jan2026.csv'"
            except Exception as e:
                issue["category"] = "Error Checking Source"
                issue["details"] = str(e)
                
        issues.append(issue)

    # Group by Category
    categories = {}
    for i in issues:
        cat = i["category"]
        if cat not in categories: categories[cat] = []
        categories[cat].append(i)
        
    print("\n=== VERIFICATION RESULTS (Phase 3) ===")
    for cat, items in categories.items():
        print(f"\nCategory: {cat} ({len(items)} items)")
        for item in items:
            print(f"  - {item['id']}: {item['details']}")
            
if __name__ == "__main__":
    asyncio.run(categorize_issues())
