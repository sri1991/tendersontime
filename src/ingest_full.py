import sys
import os
from src.ingestion.pipeline import IngestionPipeline

# Default Configuration
DEFAULT_CSV = "tender_dataset_06082025_6Jan2026.csv"
TOTAL_RECORDS = 90000
START_OFFSET = 85000

def main():
    import argparse
    import asyncio 

    parser = argparse.ArgumentParser(description="Ingestion Pipeline Trigger")
    parser.add_argument("input_file", nargs="?", default=DEFAULT_CSV, help="Path to input CSV or Excel file")
    parser.add_argument("--api-key", help="Gemini API Key (overrides env var)", default=None)
    parser.add_argument("--offset", type=int, default=START_OFFSET, help="Start offset")
    parser.add_argument("--limit", type=int, default=TOTAL_RECORDS, help="Total records to process")
    
    args = parser.parse_args()
    
    # Check if file exists
    if not os.path.exists(args.input_file):
        print(f"Error: File {args.input_file} not found.")
        sys.exit(1)
        
    print(f"Initializing Ingestion Pipeline for {args.input_file}...")
    
    pipeline = IngestionPipeline(
        input_file=args.input_file,
        api_key=args.api_key,
        total_records=args.limit,
        start_offset=args.offset,
        chunk_size=500
    )
    
    try:
        asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        print("\nPipeline stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nPipeline failed: {e}")
        # Print full traceback for debugging async issues
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
