import os
import time
import logging
import pandas as pd
import hashlib
import json
from tqdm import tqdm
from src.enrichment.processor import TenderEnricher
from src.indexing.chroma_loader import ChromaLoader
from src.db.postgres_loader import PostgresLoader

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
                 start_offset: int = 0, total_records: int = None, chunk_size: int = 500,
                 progress_callback=None):
        """
        Initializes the ingestion pipeline.
        
        Args:
            input_file: Path to the input Excel or CSV file.
            api_key: Gemini API Key.
            start_offset: Record index to start processing from.
            total_records: Total records to process (if None, will try to count).
            chunk_size: Number of records to process in each batch.
            progress_callback: Optional async function(progress_data) to call with updates.
        """
        self.original_input_file = input_file
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.chunk_size = chunk_size
        self.total_records = total_records
        self.progress_callback = progress_callback
        
        # Helper: Convert Excel to CSV if needed
        self.working_csv_file = self._prepare_input_file(input_file)
        
        # Checkpoint Logic
        self.checkpoint_dir = "checkpoints"
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self.file_signature = self._get_file_signature(self.working_csv_file)
        self.checkpoint_file = os.path.join(self.checkpoint_dir, f"{self.file_signature}.json")
        
        # Determine Start Offset (Resume vs New)
        self.start_offset = self._load_checkpoint(start_offset)

        # Initialize components
        self.enricher = TenderEnricher(api_key=self.api_key)

        # Use postgres_loader as primary; chroma_loader kept for backward compat
        search_backend = os.getenv("SEARCH_BACKEND", "postgres")
        if search_backend == "postgres":
            self.loader = PostgresLoader(api_key=self.api_key)
        else:
            self.loader = ChromaLoader(api_key=self.api_key)
        
        # Determine total records if not provided
        if self.total_records is None:
            self._count_total_records()

    def _get_file_signature(self, file_path: str) -> str:
        """
        Generates a unique signature for the file based on size and first 4KB.
        """
        try:
            stats = os.stat(file_path)
            file_size = stats.st_size
            
            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                buf = f.read(4096)
                hasher.update(buf)
                
            # Combine size and header hash
            signature_base = f"{file_size}_{hasher.hexdigest()}"
            return hashlib.md5(signature_base.encode()).hexdigest()
        except Exception as e:
            logging.warning(f"Failed to generate file signature: {e}")
            return "unknown_signature"

    def _load_checkpoint(self, requested_offset: int) -> int:
        """
        Checks for existing checkpoint. Returns the offset to start from.
        """
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, 'r') as f:
                    data = json.load(f)
                    last_offset = data.get("last_offset", 0)
                    if last_offset > requested_offset:
                        logging.info(f"RESUMING ingestion from checkpoint offset: {last_offset}")
                        return last_offset
            except Exception as e:
                logging.warning(f"Failed to load checkpoint: {e}")
        
        return requested_offset

    async def _record_chunk_failure(self, offset: int, limit: int, error: str):
        """
        Records a failed ingestion chunk to the dead letter queue.
        Falls back to a local JSONL file if the DB is unavailable.
        """
        entry = {
            "chunk_offset": offset,
            "chunk_limit": limit,
            "error": error,
            "file": self.working_csv_file,
            "timestamp": __import__("time").time(),
        }
        try:
            from sqlalchemy import text
            from src.db.schema import engine as db_engine
            async with db_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO ingestion_dlq (tender_id, raw_data, error) "
                        "VALUES (:tender_id, :raw_data::jsonb, :error)"
                    ),
                    {
                        "tender_id": f"chunk_{offset}_{offset+limit}",
                        "raw_data": __import__("json").dumps(entry),
                        "error": error,
                    },
                )
            logging.info(f"Chunk failure recorded in DLQ: offset={offset}")
        except Exception as db_err:
            logging.warning(f"DLQ DB write failed ({db_err}). Falling back to file DLQ.")
            try:
                os.makedirs("data", exist_ok=True)
                with open("data/ingestion_dlq.jsonl", "a") as f:
                    f.write(__import__("json").dumps(entry) + "\n")
            except Exception as fe:
                logging.error(f"File DLQ write also failed: {fe}")

    def _save_checkpoint(self, offset: int):
        """
        Saves current progress to checkpoint file.
        """
        try:
            with open(self.checkpoint_file, 'w') as f:
                json.dump({"last_offset": offset, "updated_at": time.time()}, f)
        except Exception as e:
            logging.warning(f"Failed to save checkpoint: {e}")

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

    async def run(self):
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
                    # DELTA-SYNC: We check if the chunk needs processing.
                    # Since we are batching, we will let the enricher handle hashing/caching.
                    # But we can also check if the loader already has these IDs.
                    
                    logging.info(f"Processing chunk offset={offset}, limit={limit}")
                    
                    # We need to capture the fact if NO records were read.
                    # process_csv_to_jsonl doesn't return count.
                    # But it writes to output_file.
                    
                    await self.enricher.process_csv_to_jsonl(
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
                    if hasattr(self.loader, 'load_from_jsonl'):
                        # PostgresLoader is async; ChromaLoader is sync
                        import asyncio, inspect
                        if inspect.iscoroutinefunction(self.loader.load_from_jsonl):
                            await self.loader.load_from_jsonl(output_file)
                        else:
                            self.loader.load_from_jsonl(output_file)

                    # Cleanup
                    os.remove(output_file)

                except Exception as e:
                    logging.error(f"Pipeline failed at chunk {offset}: {e}")
                    # Write failed chunk info to DLQ
                    await self._record_chunk_failure(offset, limit, str(e))
                    continue
                
                chunk_duration = time.time() - chunk_start_time
                logging.info(f"Chunk finished in {chunk_duration:.2f}s")
                
                # Update progress bar
                pbar.update(limit)

                # Callback Update
                if self.progress_callback:
                    await self.progress_callback({
                        "status": "processing",
                        "current": offset + limit,
                        "total": self.total_records,
                        "last_log": f"Processed chunk {offset}-{offset+limit} in {chunk_duration:.2f}s"
                    })
                
                # SAVE CHECKPOINT
                self._save_checkpoint(offset + limit)
                
        logging.info("Ingestion Pipeline Completed Successfully.")

        # CLEANUP CHECKPOINT
        if os.path.exists(self.checkpoint_file):
            os.remove(self.checkpoint_file)

        if self.progress_callback:
            await self.progress_callback({
                "status": "completed",
                "current": self.total_records,
                "total": self.total_records,
                "last_log": "Ingestion Completed Successfully."
            })
        
        # Cleanup temp turned csv if it was converted
        if self.working_csv_file != self.original_input_file and os.path.exists(self.working_csv_file):
            logging.info("Cleaning up temporary CSV file...")
            os.remove(self.working_csv_file)
