import subprocess
import os
import time
import sys

# Configuration
# Configuration
TOTAL_RECORDS = 20000
START_OFFSET = 0
CHUNK_SIZE = 500
INPUT_CSV = "tender_dataset_06082025_6Jan2026.csv"
PYTHON_EXEC = "venv/bin/python"

def run_command(cmd):
    print(f"--- Executing: {cmd} ---")
    subprocess.check_call(cmd, shell=True)

def main():
    print(f"Starting Full Ingestion from offset {START_OFFSET} to {TOTAL_RECORDS}...")
    
    for offset in range(START_OFFSET, TOTAL_RECORDS, CHUNK_SIZE):
        limit = CHUNK_SIZE
        print(f"\n==================================================")
        print(f"Processing Chunk: Offset {offset}, Limit {limit}")
        print(f"==================================================")
        
        output_file = f"batch_{offset}_{offset+limit}.jsonl"
        
        # Clean up previous run residue
        if os.path.exists(output_file):
            os.remove(output_file)
        
        # 1. Enrichment
        # python -m src.enrichment.processor <csv> <jsonl> --limit <limit> --offset <offset>
        enrich_cmd = f"{PYTHON_EXEC} -m src.enrichment.processor {INPUT_CSV} {output_file} --limit {limit} --offset {offset}"
        
        try:
            start_t = time.time()
            run_command(enrich_cmd)
            duration = time.time() - start_t
            print(f"Enrichment finished in {duration:.2f} seconds.")
        except subprocess.CalledProcessError as e:
            print(f"!!! Enrichment failed for offset {offset}. Error: {e}")
            # We stop here to avoid gaps or issues
            sys.exit(1)
            
        # 2. Indexing
        if os.path.exists(output_file):
            index_cmd = f"{PYTHON_EXEC} -m src.indexing.chroma_loader {output_file}"
            try:
                start_t = time.time()
                run_command(index_cmd)
                duration = time.time() - start_t
                print(f"Indexing finished in {duration:.2f} seconds.")
                
                # 3. Cleanup
                print(f"Removing temp file {output_file}...")
                os.remove(output_file)
                
            except subprocess.CalledProcessError as e:
                print(f"!!! Indexing failed for offset {offset}. Error: {e}")
                sys.exit(1)
        else:
            print(f"!!! Output file {output_file} not found. Skipping indexing.")
            sys.exit(1)
            
    print("\n\n################################################")
    print("       FULL DATASET INGESTION COMPLETE!       ")
    print("################################################")

if __name__ == "__main__":
    main()
