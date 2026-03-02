"""
TenderEnricher — migrated to the new google.genai SDK.

Changes from v1:
  - Removed google.generativeai (old SDK) — now uses google.genai exclusively
  - Context caching attempt retained via system_instruction config parameter
  - All other logic (pre-filter, SHA-256 cache, batch enrichment) unchanged
"""

import os
import json
import logging
import asyncio
import hashlib
from typing import List, Dict, Any

import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

from src.cleaning.cleaner import CurrencyNormalizer, DateStandardizer, Deduplicator
from src.enrichment.prompts import (
    ENRICHMENT_PROMPT,
    STATIC_SYSTEM_PROMPT_TEMPLATE,
    TENDER_USER_PROMPT_TEMPLATE,
)
from src.models.taxonomy import CoreDomain, ProcurementType

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class TenderEnricher:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY found. API calls will fail.")

        # New SDK client (single instance for all calls)
        self.client = genai.Client(api_key=self.api_key)

        # Load keyword taxonomy
        self.keywords_str = "No specific keywords loaded."
        keywords_data: Any = []
        try:
            keywords_path = os.path.join(os.path.dirname(__file__), "keywords.json")
            if os.path.exists(keywords_path):
                with open(keywords_path, "r", encoding="utf-8") as f:
                    keywords_data = json.load(f)
                self.keywords_str = json.dumps(keywords_data, indent=2)
                logger.info(f"Loaded keywords from {keywords_path}")
            else:
                logger.warning("keywords.json not found.")
        except Exception as e:
            logger.warning(f"Error loading keywords: {e}")

        # Flatten keywords for pre-filter
        self.flat_keywords: List[str] = []
        if isinstance(keywords_data, dict):
            for keys in keywords_data.values():
                if isinstance(keys, list):
                    self.flat_keywords.extend(k.lower() for k in keys)
        elif isinstance(keywords_data, list):
            self.flat_keywords = [k.lower() for k in keywords_data]
        logger.info(f"Pre-filter initialised with {len(self.flat_keywords)} keywords.")

        # System prompt (formatted once, passed as system_instruction per call)
        self.system_prompt = STATIC_SYSTEM_PROMPT_TEMPLATE.format(
            keyword_mapping=self.keywords_str
        )

        # SHA-256 enrichment cache
        self.cache_dir = "data/enrichment_cache"
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "cache.json")
        self.local_cache: Dict[str, Any] = self._load_local_cache()

    # ──────────────────────────────────────────────────────────
    # Local SHA-256 cache helpers
    # ──────────────────────────────────────────────────────────

    def _load_local_cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load local cache: {e}")
        return {}

    def _save_local_cache(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.local_cache, f)
        except Exception as e:
            logger.warning(f"Failed to save local cache: {e}")

    def _get_text_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # ──────────────────────────────────────────────────────────
    # Pre-filter (cost optimisation)
    # ──────────────────────────────────────────────────────────

    def _should_enrich(self, title: str, description: str) -> bool:
        text = (str(title) + " " + str(description)).lower()
        if len(text) < 20:
            return False
        if len(text) < 50:
            return any(k in text for k in self.flat_keywords)
        return True

    # ──────────────────────────────────────────────────────────
    # Schema validation
    # ──────────────────────────────────────────────────────────

    def validate_enrichment(self, enrichment: Dict[str, Any]) -> Dict[str, Any]:
        domain = enrichment.get("core_domain")
        if domain not in [d.value for d in CoreDomain]:
            logger.warning(f"Invalid core_domain '{domain}'. Resetting to 'Other'.")
            enrichment["core_domain"] = CoreDomain.OTHER.value

        p_type = enrichment.get("procurement_type")
        if p_type not in [p.value for p in ProcurementType]:
            logger.warning(f"Invalid procurement_type '{p_type}'. Resetting to 'Unknown'.")
            enrichment["procurement_type"] = ProcurementType.UNKNOWN.value

        return enrichment

    # ──────────────────────────────────────────────────────────
    # Single tender enrichment (with SHA-256 cache)
    # ──────────────────────────────────────────────────────────

    async def enrich_tender(self, title: str, description: str = "") -> Dict[str, Any]:
        desc_text = description if description and pd.notna(description) else "No description provided."

        if not self._should_enrich(title, desc_text):
            return self._skipped_result(title)

        text_hash = self._get_text_hash(f"{title}\n{desc_text}")
        if text_hash in self.local_cache:
            logger.debug(f"Cache HIT: {title[:40]}")
            return self.local_cache[text_hash]

        prompt = TENDER_USER_PROMPT_TEMPLATE.format(title=title, description=desc_text)

        try:
            response = await self.client.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    system_instruction=self.system_prompt,
                ),
            )
            text_raw = response.text.strip()
            if text_raw.startswith("```json"):
                text_raw = text_raw[7:-3]
            elif text_raw.startswith("```"):
                text_raw = text_raw[3:-3]

            result = self.validate_enrichment(json.loads(text_raw))
            self.local_cache[text_hash] = result
            self._save_local_cache()
            return result

        except Exception as e:
            logger.error(f"Error enriching '{title}': {e}")
            return self._error_result(title, str(e))

    # ──────────────────────────────────────────────────────────
    # Batch enrichment (10 tenders per prompt)
    # ──────────────────────────────────────────────────────────

    async def enrich_batch_genai(self, batch_data: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        if not batch_data:
            return []

        batch_str = ""
        for i, item in enumerate(batch_data):
            batch_str += (
                f"--- TENDER {i} ---\n"
                f"Title: {item['title']}\n"
                f"Description: {item['description']}\n\n"
            )

        prompt = (
            f"Analyze the following batch of {len(batch_data)} tenders.\n"
            f"Return a JSON ARRAY of exactly {len(batch_data)} objects following the OUTPUT SCHEMA.\n"
            f"Maintain the same order as the input.\n\n"
            f"BATCH DATA:\n{batch_str}"
        )

        try:
            response = await self.client.aio.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    system_instruction=self.system_prompt,
                ),
            )
            text_raw = response.text.strip()
            if text_raw.startswith("```json"):
                text_raw = text_raw[7:-3]
            elif text_raw.startswith("```"):
                text_raw = text_raw[3:-3]

            results = json.loads(text_raw)
            if not isinstance(results, list):
                results = [results]

            return [self.validate_enrichment(r) if r else None for r in results]

        except Exception as e:
            logger.error(f"Batch enrichment failed: {e}")
            return [None] * len(batch_data)

    # ──────────────────────────────────────────────────────────
    # Batch processing with cache + pre-filter
    # ──────────────────────────────────────────────────────────

    async def process_batch(self, tenders_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Any] = [None] * len(tenders_data)
        to_enrich_indices: List[int] = []
        to_enrich_data: List[Dict] = []

        for i, row in enumerate(tenders_data):
            title = row.get("Summary") or row.get("Title") or ""
            description = row.get("Description") or ""

            if not self._should_enrich(title, description):
                results[i] = self._skipped_result(title)
                continue

            text_hash = self._get_text_hash(f"{title}\n{description}")
            if text_hash in self.local_cache:
                results[i] = self.local_cache[text_hash]
                continue

            to_enrich_indices.append(i)
            to_enrich_data.append({"title": title, "description": description, "hash": text_hash})

        # Enrich in sub-batches of 10
        for i in range(0, len(to_enrich_data), 10):
            chunk = to_enrich_data[i:i + 10]
            chunk_indices = to_enrich_indices[i:i + 10]
            logger.info(f"Enriching batch chunk of {len(chunk)}...")
            enriched_chunk = await self.enrich_batch_genai(chunk)

            for j, res in enumerate(enriched_chunk):
                idx = chunk_indices[j]
                if res:
                    results[idx] = res
                    self.local_cache[chunk[j]["hash"]] = res
                else:
                    results[idx] = self._error_result(chunk[j]["title"], "Batch item failed")

        self._save_local_cache()

        return [
            {**original, **(result or {})}
            for original, result in zip(tenders_data, results)
        ]

    # ──────────────────────────────────────────────────────────
    # CSV → JSONL (pipeline entry point)
    # ──────────────────────────────────────────────────────────

    async def process_csv_to_jsonl(
        self,
        input_csv: str,
        output_jsonl: str,
        batch_size: int = 50,
        limit: int = None,
        offset: int = 0,
    ):
        try:
            if limit:
                df = pd.read_csv(input_csv, skiprows=range(1, offset + 1), nrows=limit)
            else:
                df = pd.read_csv(input_csv, skiprows=range(1, offset + 1))
        except pd.errors.EmptyDataError:
            logger.warning("No data in specified range.")
            return

        records = df.to_dict(orient="records")
        logger.info(f"Loaded {len(records)} raw records.")

        # Cleaning + dedup
        cleaned, seen_hashes = [], set()
        for row in records:
            if "Amount" in row:
                row["AmountInt"] = CurrencyNormalizer.normalize(str(row["Amount"]))
            if "Date" in row:
                row["DateISO"] = DateStandardizer.to_iso(str(row["Date"]))

            title = str(row.get("Summary") or row.get("Title") or "")
            location = str(row.get("Country") or row.get("Location") or "")
            row_hash = Deduplicator.generate_hash(title, location)
            row["DedupHash"] = row_hash

            if "RefNo" not in row or pd.isna(row.get("RefNo", float("nan"))):
                row["RefNo"] = row_hash

            if row_hash in seen_hashes:
                continue
            seen_hashes.add(row_hash)
            cleaned.append(row)

        logger.info(f"After dedup: {len(cleaned)} records.")
        os.makedirs(os.path.dirname(output_jsonl) or ".", exist_ok=True)

        for i in range(0, len(cleaned), batch_size):
            batch = cleaned[i:i + batch_size]
            logger.info(f"Processing batch {i}–{i + batch_size}...")
            enriched = await self.process_batch(batch)
            with open(output_jsonl, "a") as f:
                for item in enriched:
                    f.write(json.dumps(item) + "\n")

        logger.info(f"Done. Output: {output_jsonl}")

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    def _skipped_result(self, title: str) -> Dict[str, Any]:
        return {
            "core_domain": "Unclassified",
            "project_tags": [],
            "procurement_type": "Unknown",
            "search_keywords": [],
            "entities": {},
            "signal_summary": title,
            "note": "Skipped by pre-filter",
        }

    def _error_result(self, title: str, error: str) -> Dict[str, Any]:
        return {
            "core_domain": "Unclassified",
            "project_tags": [],
            "procurement_type": "Unknown",
            "search_keywords": [],
            "entities": {},
            "signal_summary": title,
            "error": error,
        }


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Batch Enrichment Processor")
    parser.add_argument("input_csv")
    parser.add_argument("output_jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    if not os.getenv("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY")
        sys.exit(1)

    enricher = TenderEnricher()
    asyncio.run(
        enricher.process_csv_to_jsonl(args.input_csv, args.output_jsonl, limit=args.limit, offset=args.offset)
    )
