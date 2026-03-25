"""
Tier 2 — Gemini LLM Batch Enrichment.

Processes only the hard cases (~10%) that Tier 0 couldn't resolve.
Batches 10 records per prompt to minimize API calls.

Output per record:
  - cpv_code         : best-match CPV code
  - cpv_description  : human-readable label
  - cpv_confidence   : float 0-1
  - procurement_type : Supply | Works | Services | Consultancy | Unknown
  - summary_clean    : cleaned/normalized summary (optional improvement)
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import google.generativeai as genai
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.cpv.taxonomy import get_code_map
from app.models.tender import ProcurementType, TenderCSVRow, TenderEnriched, TierProcessed

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)

_GEMINI_MODEL = genai.GenerativeModel(settings.gemini_model)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a procurement data specialist. For each tender record below, return a JSON array where each element has:
- "tot_id": the tender ID (copy from input)
- "cpv_code": best matching EU CPV code (8-digit format like "30213100"), or null if unknown
- "cpv_description": short human-readable label for that CPV code
- "cpv_confidence": float 0.0-1.0 (your confidence in the CPV assignment)
- "procurement_type": one of "Supply", "Works", "Services", "Consultancy", "Unknown"

Rules:
- Respond ONLY with a valid JSON array, no markdown, no explanation.
- If the description is empty or uninformative, set cpv_code=null, cpv_confidence=0.0, procurement_type="Unknown".
- CPV codes must be exactly 8 digits (no dash/suffix).

Input records:
"""


def _build_prompt(rows: list[TenderCSVRow]) -> str:
    records = []
    for row in rows:
        records.append({
            "tot_id": row.tot_id,
            "summary": (row.summary or "")[:500],
            "description": (row.description or "")[:1000],
            "country": row.country or "",
        })
    return _SYSTEM_PROMPT + json.dumps(records, ensure_ascii=False)


def _parse_response(response_text: str, rows: list[TenderCSVRow]) -> list[TenderEnriched]:
    """Parse Gemini JSON response into TenderEnriched objects."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?", "", response_text).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response: {e}\nResponse: {response_text[:500]}")
        # Return safe fallback for all rows in batch
        return [
            TenderEnriched(
                tot_id=row.tot_id,
                tier_processed=TierProcessed.TIER2,
                procurement_type=ProcurementType.UNKNOWN,
            )
            for row in rows
        ]

    # Build a lookup by tot_id from parsed data
    parsed_map: dict[str, dict] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "tot_id" in item:
                parsed_map[str(item["tot_id"])] = item

    # Valid CPV codes from the taxonomy — cached, so only loaded once
    _valid_cpv_codes = get_code_map()

    enriched_list = []
    for row in rows:
        item = parsed_map.get(row.tot_id, {})

        # Validate CPV code: must be 8 digits AND exist in the taxonomy
        cpv_code = str(item.get("cpv_code") or "").strip()
        if not re.fullmatch(r"\d{8}", cpv_code) or cpv_code not in _valid_cpv_codes:
            if cpv_code and re.fullmatch(r"\d{8}", cpv_code):
                logger.debug(f"Gemini returned unknown CPV code {cpv_code} for {row.tot_id} — discarding")
            cpv_code = None

        # Clamp confidence
        try:
            confidence = float(item.get("cpv_confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        # Validate procurement type
        raw_type = str(item.get("procurement_type", "Unknown")).strip()
        try:
            proc_type = ProcurementType(raw_type)
        except ValueError:
            proc_type = ProcurementType.UNKNOWN

        enriched_list.append(
            TenderEnriched(
                tot_id=row.tot_id,
                cpv_code=cpv_code if cpv_code else None,
                cpv_description=str(item.get("cpv_description") or "").strip() or None,
                cpv_confidence=confidence if cpv_code else None,
                procurement_type=proc_type,
                tier_processed=TierProcessed.TIER2,
            )
        )

    return enriched_list


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=60))
def _call_gemini_sync(prompt: str) -> str:
    response = _GEMINI_MODEL.generate_content(prompt)
    return response.text


async def _call_gemini_batch(
    batch: list[TenderCSVRow],
    batch_num: int,
    total_batches: int,
) -> list[TenderEnriched]:
    """Process a single batch of records through Gemini (one prompt)."""
    logger.info(f"Tier 2: calling Gemini for batch {batch_num}/{total_batches} ({len(batch)} records)")
    prompt = _build_prompt(batch)
    try:
        loop = asyncio.get_running_loop()
        response_text = await loop.run_in_executor(
            None, lambda p=prompt: _call_gemini_sync(p)
        )
        return _parse_response(response_text, batch)
    except Exception as e:
        logger.error(f"Gemini batch {batch_num}/{total_batches} failed after retries: {e}")
        return [
            TenderEnriched(
                tot_id=row.tot_id,
                tier_processed=TierProcessed.TIER2,
                procurement_type=ProcurementType.UNKNOWN,
            )
            for row in batch
        ]


async def enrich_batch_tier2(rows: list[TenderCSVRow]) -> list[TenderEnriched]:
    """
    Enrich a batch of tender records using Gemini.
    Splits into sub-batches of TIER2_BATCH_SIZE and fires all prompts in parallel.

    Returns one TenderEnriched per input row.
    """
    if not rows:
        return []

    batch_size = settings.tier2_batch_size
    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

    logger.info(f"Tier 2: firing {len(batches)} Gemini prompts in parallel")

    results = await asyncio.gather(*[
        _call_gemini_batch(batch, i + 1, len(batches))
        for i, batch in enumerate(batches)
    ])

    return [enriched for batch_result in results for enriched in batch_result]
