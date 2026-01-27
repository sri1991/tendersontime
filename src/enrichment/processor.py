import os
import json
import logging
import asyncio
from typing import List, Dict, Any
import pandas as pd
import google.generativeai as genai
from dotenv import load_dotenv
from src.cleaning.cleaner import CurrencyNormalizer, DateStandardizer, Deduplicator

load_dotenv()
from src.enrichment.prompts import ENRICHMENT_PROMPT

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TenderEnricher:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logging.warning("No GEMINI_API_KEY found. API calls will fail.")
        
        genai.configure(api_key=self.api_key)
        # using 'gemini-2.5-flash-lite' as requested
        self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
        
        # Load keywords
        self.keywords_str = "No specific keywords loaded."
        try:
            keywords_path = os.path.join(os.path.dirname(__file__), "keywords.json")
            if os.path.exists(keywords_path):
                with open(keywords_path, 'r', encoding='utf-8') as f:
                    # Load as dict but format as JSON string for the prompt
                    keywords_data = json.load(f)
                    self.keywords_str = json.dumps(keywords_data, indent=2)
                    logging.info(f"Loaded keywords from {keywords_path}")
            else:
                logging.warning(f"keywords.json not found at {keywords_path}")
        except Exception as e:
             logging.warning(f"Error loading keywords: {e}")

    async def enrich_tender(self, title: str, description: str = "") -> Dict[str, Any]:
        """
        Enriches a single tender using the comprehensive enrichment prompt.
        """
        # Fallback for nulls
        desc_text = description if description and pd.notna(description) else "No description provided."
        
        prompt = ENRICHMENT_PROMPT.format(
            title=title, 
            description=desc_text, 
            keyword_mapping=self.keywords_str
        )
        
        config = genai.types.GenerationConfig(
            temperature=0.1, 
            response_mime_type="application/json",
        )

        try:
            # Use async generation for better concurrency
            response = await self.model.generate_content_async(
                prompt,
                generation_config=config
            )
            
            response_text = response.text.strip()
            # Clean potential markdown
            if response_text.startswith("```json"):
                response_text = response_text[7:-3]
            elif response_text.startswith("```"):
                response_text = response_text[3:-3]
                
            return json.loads(response_text)
            
        except Exception as e:
            logging.error(f"Error enriching tender '{title}': {e}")
            return {
                "core_domain": "Unclassified",
                "project_tags": [],
                "procurement_type": "Unknown",
                "search_keywords": [],
                "entities": {},
                "signal_summary": title,
                "error": str(e)
            }

    async def process_batch(self, tenders_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a list of dictionaries (rows).
        """
        tasks = []
        for row in tenders_data:
            # TITLE MAPPING: Use 'Summary' as Title
            title = row.get("Summary") or row.get("Title") or ""
            tasks.append(self.enrich_tender(title, row.get("Description", "")))
        
        results = await asyncio.gather(*tasks)
        
        enriched_rows = []
        for original, result in zip(tenders_data, results):
            # Merge original data with enrichment result
            merged = original.copy()
            merged.update(result)
            enriched_rows.append(merged)
            
        return enriched_rows

    def process_csv_to_jsonl(self, input_csv: str, output_jsonl: str, batch_size: int = 50, limit: int = None, offset: int = 0):
        """
        Reads CSV, cleans & deduplicates, enriches in batches, writes to JSONL.
        Supports pagination via limit/offset.
        """
        # Read only specific chunk of the CSV
        # offset+1 because header is row 0
        try:
             # Load chunk
             logging.info(f"Reading CSV from offset {offset} with limit {limit}...")
             if limit:
                 df = pd.read_csv(input_csv, skiprows=range(1, offset+1), nrows=limit)
             else:
                 df = pd.read_csv(input_csv, skiprows=range(1, offset+1))
        except pd.errors.EmptyDataError:
            logging.warning("No data found in the specified range.")
            return

        records = df.to_dict(orient='records')
        logging.info(f"Loaded {len(records)} raw records from CSV chunk.")
        
        # 1. CLEANING & DEDUPLICATION (Local to this batch)
        cleaned_records = []
        seen_hashes = set()
        
        for row in records:
            # Clean fields
            # Assuming CSV cols: Title, Location, Amount, Date. Adjust as needed or make generic.
            # We'll just add the clean fields to the row
            if "Amount" in row:
                row["AmountInt"] = CurrencyNormalizer.normalize(str(row["Amount"]))
            if "Date" in row:
                row["DateISO"] = DateStandardizer.to_iso(str(row["Date"]))
            
            # MAPPING: Map CSV specific columns to generic ones
            title = str(row.get("Summary") or row.get("Title") or "")
            location = str(row.get("Country") or row.get("Location") or "")
            
            # Dedup
            row_hash = Deduplicator.generate_hash(title, location)
            row["DedupHash"] = row_hash
            # Ensure RefNo exists
            if "RefNo" not in row or pd.isna(row["RefNo"]):
                row["RefNo"] = row_hash
            
            if row_hash in seen_hashes:
                continue
            
            seen_hashes.add(row_hash)
            cleaned_records.append(row)
            
        logging.info(f"After cleanup & dedup: {len(cleaned_records)} records to process.")
        
        async def _run_all():
            all_results = []
            for i in range(0, len(cleaned_records), batch_size):
                batch = cleaned_records[i:i+batch_size]
                logging.info(f"Processing batch {i} to {i+batch_size}...")
                enriched_batch = await self.process_batch(batch)
                all_results.extend(enriched_batch)
                
                # Appending to file incrementally is safer for large jobs
                with open(output_jsonl, 'a') as f:
                    for item in enriched_batch:
                        f.write(json.dumps(item) + "\n")
                        
            logging.info(f"Finished processing. Output wrote to {output_jsonl}")

        # Ensure dir exists
        os.makedirs(os.path.dirname(output_jsonl) or ".", exist_ok=True)
        # We don't clear output file here because we might be appending from multiple runs. 
        # User responsible for managing output file cleanup or rotation.

        asyncio.run(_run_all())

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Batch Enrichment Processor")
    parser.add_argument("input_csv", help="Path to input CSV")
    parser.add_argument("output_jsonl", help="Path to output JSONL")
    parser.add_argument("--limit", type=int, default=None, help="Max records to read")
    parser.add_argument("--offset", type=int, default=0, help="Records to skip")
    
    args = parser.parse_args()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Please set GEMINI_API_KEY env var.")
        sys.exit(1)
        
    enricher = TenderEnricher(api_key=api_key)
    enricher.process_csv_to_jsonl(args.input_csv, args.output_jsonl, limit=args.limit, offset=args.offset)
