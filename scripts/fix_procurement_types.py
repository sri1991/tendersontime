
import chromadb
import os
import sys
import json
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

CHROMA_HOST = os.getenv("CHROMA_HOST", "136.114.154.210")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8002))
COLLECTION_NAME = "tenders_v1"
DRY_RUN = False # Set to False to apply changes

def fix_types():
    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    collection = client.get_collection(COLLECTION_NAME)
    
    # 1. Fetch all records (paging would be better for memory, but for 85k IDs it fits in RAM)
    print("Fetching all record IDs...")
    all_data = collection.get(include=["metadatas"])
    ids = all_data['ids']
    metas = all_data['metadatas']
    
    total = len(ids)
    print(f"Total: {total} records.")
    
    updates_ids = []
    updates_metas = []
    
    stats = {"Consultancy->Services": 0, "Unknown->Works": 0, "Unknown->Supply": 0, "Unknown->Services": 0, "Skipped": 0}
    
    for i, meta in enumerate(metas):
        if not meta: continue
        
        ptype = meta.get("procurement_type", "Unknown")
        title = (meta.get("original_title") or "").lower()
        new_type = None
        
        # Rule 1: Consultancy -> Services
        if ptype == "Consultancy":
            new_type = "Services"
            stats["Consultancy->Services"] += 1
            
        # Rule 2: Unknown -> Keyword Guess
        elif ptype in ["Unknown", "Unclassified", "Other"]:
            if any(k in title for k in ["work", "construction", "build", "road", "civil"]):
                new_type = "Works"
                stats["Unknown->Works"] += 1
            elif any(k in title for k in ["supply", "purchase", "delivery", "procurement of", "equipment", "goods"]):
                new_type = "Supply"
                stats["Unknown->Supply"] += 1
            elif any(k in title for k in ["service", "consult", "manpower", "hiring", "amc", "security", "cleaning"]):
                new_type = "Services"
                stats["Unknown->Services"] += 1
            else:
                stats["Skipped"] += 1
                
                
        if new_type:
            # Create updated metadata dict (copy existing)
            updated_meta = meta.copy()
            updated_meta["procurement_type"] = new_type
            
            updates_ids.append(ids[i])
            updates_metas.append(updated_meta)
            
            # Log change
            audit_entry = {
                "id": ids[i],
                "old_type": ptype,
                "new_type": new_type,
                "title": title
            }
            with open("procurement_fix_log.jsonl", "a") as log:
                log.write(json.dumps(audit_entry) + "\n")
            
    print("\n--- Proposed Updates ---")
    print(f"Total to update: {len(updates_ids)}")
    print(f"Changes logged to procurement_fix_log.jsonl")
    print(stats)
    
    if not DRY_RUN and updates_ids:
        print("Applying updates to ChromaDB...")
        batch_size = 500
        for i in range(0, len(updates_ids), batch_size):
            end = i + batch_size
            collection.update(
                ids=updates_ids[i:end],
                metadatas=updates_metas[i:end]
            )
            print(f"Updated batch {i}-{end}")
        print("Done.")
    elif DRY_RUN:
        print("\nDRY RUN: No changes applied. Set DRY_RUN = False to apply.")

if __name__ == "__main__":
    fix_types()
