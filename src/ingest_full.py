import sys
import os
from src.ingestion.pipeline import IngestionPipeline

# Default Configuration
DEFAULT_CSV = "tender_dataset_06082025_6Jan2026.csv"
TOTAL_RECORDS = 90000
START_OFFSET = 85000

def main():
    # Allow overriding file via CLI arg
    input_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    
    # Check if file exists
    if not os.path.exists(input_file):
        print(f"Error: File {input_file} not found.")
        sys.exit(1)
        
    print(f"Initializing Ingestion Pipeline for {input_file}...")
    
    pipeline = IngestionPipeline(
        input_file=input_file,
        total_records=TOTAL_RECORDS,
        start_offset=START_OFFSET,
        chunk_size=500
    )
    
    try:
        pipeline.run()
    except KeyboardInterrupt:
        print("\nPipeline stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nPipeline failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
