import os
import time
import logging
import pandas as pd
from tqdm import tqdm
from src.enrichment.processor import TenderEnricher
from src.indexing.chroma_loader import ChromaLoader

# Configure logging to show up in standard output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

class IngestionPipeline:
    def __init__(self, input_file: str, api_key: str = None, 
                 start_offset: int = 0, total_records: int = None, chunk_size: int = 500):
        """
        Initializes the ingestion pipeline.
        
        Args:
            input_file: Path to the input Excel or CSV file.
            api_key: Gemini API Key.
            start_offset: Record index to start processing from.
            total_records: Total records to process (if None, will try to count).
            chunk_size: Number of records to process in each batch.
        """
        self.original_input_file = input_file
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.start_offset = start_offset
        self.chunk_size = chunk_size
        self.total_records = total_records
        
        # Helper: Convert Excel to CSV if needed
        self.working_csv_file = self._prepare_input_file(input_file)
        
        # Initialize components
        self.enricher = TenderEnricher(api_key=self.api_key)
        self.loader = ChromaLoader(api_key=self.api_key)
        
        # Determine total records if not provided
        if self.total_records is None:
            self._count_total_records()

    def _prepare_input_file(self, file_path: str) -> str:
        """
        Ensures we have a CSV file to work with. 
        If input is Excel, converts to temp CSV.
        """
        if file_path.lower().endswith('.csv'):
            return file_path
            
        if file_path.lower().endswith(('.xls', '.xlsx')):
            logging.info(f"Converting Excel file {file_path} to temporary CSV...")
            try:
                # Load Excel
                df = pd.read_excel(file_path)
                # Create temp csv path
                temp_csv = file_path.rsplit('.', 1)[0] + "_temp_converted.csv"
                df.to_csv(temp_csv, index=False)
                logging.info(f"Converted to {temp_csv}")
                return temp_csv
            except Exception as e:
                raise ValueError(f"Failed to convert Excel to CSV: {e}")
        
        raise ValueError(f"Unsupported file format: {file_path}. Please use .csv, .xls, or .xlsx")

    def _count_total_records(self):
        try:
            # Efficiently count lines for CSV
            if self.working_csv_file.endswith('.csv'):
                with open(self.working_csv_file, 'r', encoding='utf-8', errors='ignore') as f:
                     # Subtract 1 for header
                     self.total_records = sum(1 for _ in f) - 1
            else:
                 # Should not happen given _prepare_input_file
                 pass
            
            logging.info(f"Detected {self.total_records} records in {self.working_csv_file}")
            
        except Exception as e:
            logging.warning(f"Could not automatically count records: {e}. Defaulting to process until end.")
            self.total_records = 999999 # Safe fallback if we can't count? Or just rely on loop break.

    def run(self):
        """
        Executes the full pipeline: Enrichment -> Indexing.
        """
        if not os.path.exists(self.working_csv_file):
            raise FileNotFoundError(f"Working CSV file not found: {self.working_csv_file}")

        logging.info(f"Starting Ingestion Pipeline for {self.original_input_file}")
        
        # If total_records was auto-detected, ensure start_offset is valid
        if self.total_records and self.start_offset >= self.total_records:
            logging.warning(f"Start offset {self.start_offset} is >= total records {self.total_records}. Nothing to do.")
            return

        # Create a progress bar
        # We assume total_records is accurate.
        
        # Safe upper bound for range
        upper_bound = self.total_records if self.total_records else self.start_offset + 1000000 
        
        with tqdm(total=self.total_records, initial=self.start_offset, unit="rec", desc="Ingestion Progress") as pbar:
            for offset in range(self.start_offset, upper_bound, self.chunk_size):
                
                # Check bounds if total_records is hard set? 
                # range takes care of it.
                
                limit = self.chunk_size
                
                chunk_start_time = time.time()
                
                # Temp file for this chunk
                # We use a unique name to avoid collisions
                output_file = f"batch_{offset}_{offset+limit}.jsonl"
                
                # 1. Enrichment
                if os.path.exists(output_file):
                    os.remove(output_file)
                    
                try:
                    # process_csv_to_jsonl(input_csv, output_jsonl, batch_size=50, limit=None, offset=0)
                    # Note: process_csv_to_jsonl uses 'skiprows=range(1, offset+1)' and 'nrows=limit'
                    # So passing offset=offset and limit=chunk_size is correct mapping.
                    
                    logging.info(f"Processing chunk offset={offset}, limit={limit}")
                    
                    # We need to capture the fact if NO records were read.
                    # process_csv_to_jsonl doesn't return count.
                    # But it writes to output_file.
                    
                    self.enricher.process_csv_to_jsonl(
                        input_csv=self.working_csv_file,
                        output_jsonl=output_file,
                        batch_size=50, 
                        limit=limit,
                        offset=offset
                    )
                    
                    # Check if file exists and has content
                    if not os.path.exists(output_file) or os.stat(output_file).st_size == 0:
                        logging.info("No more records processed. Stopping pipeline.")
                        if os.path.exists(output_file): os.remove(output_file)
                        break
                    
                    # 2. Indexing
                    self.loader.load_from_jsonl(output_file)
                    
                    # Cleanup
                    os.remove(output_file)

                except Exception as e:
                    logging.error(f"Pipeline failed at chunk {offset}: {e}")
                    # In production, we might want to store failed chunks in a DLQ
                    continue
                
                chunk_duration = time.time() - chunk_start_time
                logging.info(f"Chunk finished in {chunk_duration:.2f}s")
                
                # Update progress bar
                pbar.update(limit)
                
        logging.info("Ingestion Pipeline Completed Successfully.")
        
        # Cleanup temp turned csv if it was converted
        if self.working_csv_file != self.original_input_file and os.path.exists(self.working_csv_file):
            logging.info("Cleaning up temporary CSV file...")
            os.remove(self.working_csv_file)
