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
from src.enrichment.prompts import ENRICHMENT_PROMPT, STATIC_SYSTEM_PROMPT_TEMPLATE, TENDER_USER_PROMPT_TEMPLATE
from src.models.taxonomy import CoreDomain, ProcurementType
import datetime
from google.generativeai import caching

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TenderEnricher:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logging.warning("No GEMINI_API_KEY found. API calls will fail.")
        
        genai.configure(api_key=self.api_key)
        
        # Load keywords FIRST so we can use them in caching
        self.keywords_str = "No specific keywords loaded."
        try:
            keywords_path = os.path.join(os.path.dirname(__file__), "keywords.json")
            if os.path.exists(keywords_path):
                with open(keywords_path, 'r', encoding='utf-8') as f:
                    keywords_data = json.load(f)
                    self.keywords_str = json.dumps(keywords_data, indent=2)
                    logging.info(f"Loaded keywords from {keywords_path}")
            else:
                logging.warning(f"keywords.json not found at {keywords_path}")
                keywords_data = [] # Fallback
        except Exception as e:
             logging.warning(f"Error loading keywords: {e}")
             keywords_data = []

        # Strategy A: Context Caching
        self.use_cache = False
        try:
            # Format the static part once
            formatted_system_prompt = STATIC_SYSTEM_PROMPT_TEMPLATE.format(keyword_mapping=self.keywords_str)
            
            # Create Cache
            # ATTEMPT to use gemini-2.5-flash-lite with caching.
            # If this model is too new for caching, the try/except will catch it.
            self.cache = caching.CachedContent.create(
                model='models/gemini-2.5-flash-lite', 
                display_name="tender_enrichment_cache",
                system_instruction=formatted_system_prompt,
                ttl=datetime.timedelta(minutes=60),
            )
            self.model = genai.GenerativeModel.from_cached_content(cached_content=self.cache)
            self.use_cache = True
            logging.info("Context Caching ENABLED for Enrichment (gemini-2.5-flash-lite).")
        except Exception as e:
            logging.warning(f"Context Caching setup failed for gemini-2.5-flash-lite: {e}. Falling back to standard generation.")
            # Fallback to standard generation WITHOUT cache using the requested model
            self.model = genai.GenerativeModel("gemini-2.5-flash-lite") 
            self.use_cache = False

        # Pre-Filter init
        # Flatten keywords for fast searching
             
        # Flatten keywords for fast searching
        self.flat_keywords = []
        if isinstance(keywords_data, dict):
            for category, keys in keywords_data.items():
                if isinstance(keys, list):
                    self.flat_keywords.extend([k.lower() for k in keys])
        elif isinstance(keywords_data, list):
            self.flat_keywords = [k.lower() for k in keywords_data]
            
        logging.info(f"Pre-Filter initialized with {len(self.flat_keywords)} keywords.")

        # Hashing + Caching Layer
        self.cache_dir = "data/enrichment_cache"
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "cache.json")
        self.local_cache = self._load_local_cache()

    def _load_local_cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Failed to load local cache: {e}")
        return {}

    def _save_local_cache(self):
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.local_cache, f)
        except Exception as e:
            logging.warning(f"Failed to save local cache: {e}")

    def _get_text_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def validate_enrichment(self, enrichment: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates and fixes enrichment data against the taxonomy.
        """
        # Validate Core Domain
        domain = enrichment.get("core_domain")
        if domain not in [d.value for d in CoreDomain]:
            logging.warning(f"Invalid core_domain '{domain}'. Resetting to 'Other'.")
            enrichment["core_domain"] = CoreDomain.OTHER.value
        
        # Validate Procurement Type
        p_type = enrichment.get("procurement_type")
        if p_type not in [p.value for p in ProcurementType]:
            logging.warning(f"Invalid procurement_type '{p_type}'. Resetting to 'Unknown'.")
            enrichment["procurement_type"] = ProcurementType.UNKNOWN.value
            
        return enrichment

    def _should_enrich(self, title: str, description: str) -> bool:
        """
        Strategy B: Cost Optimization.
        Returns False if tender is likely junk (too short AND no keywords).
        """
        text = (str(title) + " " + str(description)).lower()
        
        # 1. Length Check
        if len(text) < 20: # Extremely short
            return False
            
        # 2. Keyword Check (Fast fail)
        # If text is reasonably long (>50 chars), we might give it a chance even without keywords?
        # But to be safe for cost, let's require at least ONE broad match if text is short-ish.
        if len(text) < 50:
            # For short text, STRICTLY require a keyword match
            if not any(k in text for k in self.flat_keywords):
                return False
                
        return True

    async def enrich_tender(self, title: str, description: str = "") -> Dict[str, Any]:
        """
        Enriches a single tender using the comprehensive enrichment prompt.
        """
        # Fallback for nulls
        desc_text = description if description and pd.notna(description) else "No description provided."
        
        # Strategy B: Pre-Filter
        if not self._should_enrich(title, desc_text):
            return {
                "core_domain": "Unclassified",
                "project_tags": [],
                "procurement_type": "Unknown",
                "search_keywords": [],
                "entities": {},
                "signal_summary": title,
                "note": "Skipped by Cost Optimizer (Pre-Filter)"
            }
        
        # Strategy C: Semantic Caching
        text_to_hash = f"{title}\n{desc_text}"
        text_hash = self._get_text_hash(text_to_hash)
        if text_hash in self.local_cache:
            logging.info(f"Cache HIT for: {title[:30]}...")
            return self.local_cache[text_hash]

        # Select Prompt based on Cache Status
        if self.use_cache:
            # We only send the dynamic part, system prompt is cached
            prompt = TENDER_USER_PROMPT_TEMPLATE.format(title=title, description=desc_text)
        else:
            # Full prompt
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
                
            result = json.loads(response_text)
            
            # Save to cache
            self.local_cache[text_hash] = result
            self._save_local_cache()
            
            return result
            
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

    async def enrich_batch_genai(self, batch_data: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Sends a batch of 10-20 tenders to Gemini in a single prompt.
        """
        if not batch_data:
            return []

        # Construct a combined prompt for the batch
        batch_items_str = ""
        for i, item in enumerate(batch_data):
            batch_items_str += f"--- TENDER {i} ---\nTitle: {item['title']}\nDescription: {item['description']}\n\n"

        batch_prompt = f"""
        Analyze the following batch of {len(batch_data)} tenders. 
        Return a JSON ARRAY of objects, each following the specified schema.
        Ensure the order in the array matches the order in the input.

        BATCH DATA:
        {batch_items_str}
        """

        if self.use_cache:
            # Note: For batching, we might need a different system prompt or just append.
            # Using standard prompt for now to ensure schema compliance.
            prompt = batch_prompt
        else:
            prompt = ENRICHMENT_PROMPT.format(
                title="BATCH PROCESSING", 
                description=batch_prompt, 
                keyword_mapping=self.keywords_str
            )

        config = genai.types.GenerationConfig(
            temperature=0.1, 
            response_mime_type="application/json",
        )

        try:
            response = await self.model.generate_content_async(
                prompt,
                generation_config=config
            )
            
            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:-3]
            elif response_text.startswith("```"):
                response_text = response_text[3:-3]
                
            results = json.loads(response_text)
            if not isinstance(results, list):
                logging.error("Batch response is not a list. Attempting to wrap.")
                results = [results]
            
            # Validate each item
            validated_results = [self.validate_enrichment(res) if res else None for res in results]
            return validated_results
        except Exception as e:
            logging.error(f"Batch enrichment failed: {e}")
            return [None] * len(batch_data)

    async def process_batch(self, tenders_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a list of dictionaries (rows) with caching and batching.
        """
        results = [None] * len(tenders_data)
        to_enrich_indices = []
        to_enrich_data = []

        # 1. Check Cache and Pre-Filter
        for i, row in enumerate(tenders_data):
            title = row.get("Summary") or row.get("Title") or ""
            description = row.get("Description") or ""
            
            # Pre-Filter
            if not self._should_enrich(title, description):
                results[i] = {
                    "core_domain": "Unclassified",
                    "project_tags": [],
                    "procurement_type": "Unknown",
                    "search_keywords": [],
                    "entities": {},
                    "signal_summary": title,
                    "note": "Skipped by Cost Optimizer (Pre-Filter)"
                }
                continue

            # Cache Check
            text_to_hash = f"{title}\n{description}"
            text_hash = self._get_text_hash(text_to_hash)
            if text_hash in self.local_cache:
                results[i] = self.local_cache[text_hash]
                continue

            # If not in cache and passed filter, add to batch
            to_enrich_indices.append(i)
            to_enrich_data.append({"title": title, "description": description, "hash": text_hash})

        # 2. Process Batch in chunks of 10
        batch_size = 10
        for i in range(0, len(to_enrich_data), batch_size):
            chunk = to_enrich_data[i:i+batch_size]
            chunk_indices = to_enrich_indices[i:i+batch_size]
            
            logging.info(f"Enriching batch chunk of {len(chunk)}...")
            enriched_chunk = await self.enrich_batch_genai(chunk)
            
            for j, res in enumerate(enriched_chunk):
                idx = chunk_indices[j]
                if res:
                    results[idx] = res
                    # Update cache
                    self.local_cache[chunk[j]['hash']] = res
                else:
                    # Fallback to single if batch item failed? 
                    # For simplicity, just return default
                    results[idx] = {
                        "core_domain": "Unclassified",
                        "signal_summary": chunk[j]['title'],
                        "error": "Batch enrichment failed for this item"
                    }
        
        self._save_local_cache()

        enriched_rows = []
        for original, result in zip(tenders_data, results):
            merged = original.copy()
            merged.update(result if result else {})
            enriched_rows.append(merged)
            
        return enriched_rows

    async def process_csv_to_jsonl(self, input_csv: str, output_jsonl: str, batch_size: int = 50, limit: int = None, offset: int = 0):
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

        # Await the execution directly since we are now async
        await _run_all()

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
    asyncio.run(enricher.process_csv_to_jsonl(args.input_csv, args.output_jsonl, limit=args.limit, offset=args.offset))
