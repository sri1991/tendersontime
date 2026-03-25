"""
Tier 0 — Rule-Based Enrichment (zero LLM cost).

Steps (in order):
  1. Length filter      — discard descriptions < MIN_LENGTH chars
  2. SHA-256 dedup      — return cached enrichment if hash already seen
  3. Regex NER          — extract dates, currency values, location mentions
  4. Procurement type   — rule-based detection from summary/description keywords
  5. CPV trie lookup    — keyword match → CPV code (handled by cpv.mapper)

Passes Tier 0 if:
  - CPV confidence > THRESHOLD (0.85)
  - Procurement type is not UNKNOWN
  - No ambiguous signals

Records that don't pass Tier 0 fall through to Tier 1 / Tier 2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from app.config import get_settings
from app.models.tender import ProcurementType, TenderCSVRow, TenderEnriched, TierProcessed

settings = get_settings()

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Currency + value: "$1,234,567" / "USD 2.5M" / "EUR 500,000"
_CURRENCY_RE = re.compile(
    r"(?:USD|EUR|GBP|BRL|RUB|INR|AUD|CAD|JPY|CNY|MXN|ZAR|KES|NGN|EGP|TRY|AED|SAR|CHF|SEK|NOK|DKK)?\s*"
    r"[\$€£₹¥₽]?\s*"
    r"(\d[\d,\.]+)"
    r"\s*(?:million|mn|M|billion|bn|B|thousand|k|K)?",
    re.IGNORECASE,
)

# Rough location mention: "in [CITY/COUNTRY]" or standalone country names
_LOCATION_RE = re.compile(
    r"\b(?:in|at|for|from|within)\s+([A-Z][a-zA-Z\s]{2,30}?)(?=\s*[,\.\;]|\s+(?:and|for|by|with)|\s*$)",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Procurement type keyword rules (order matters — more specific first)
# ---------------------------------------------------------------------------

_PROCUREMENT_RULES: list[tuple[re.Pattern, ProcurementType]] = [
    # Works — most specific first
    (re.compile(r"\b(construction|civil works?|infrastructure|road works?|bridge|renovati|erection|installation of pipes|laying|earthworks?|excavat)\b", re.I), ProcurementType.WORKS),
    # Consultancy
    (re.compile(r"\b(consulting|consultancy|advisory|feasibility|technical assistance|design service|expert review|evaluation|evaluator|supervisor|supervisory|third.?party inspection|conformity assessment)\b", re.I), ProcurementType.CONSULTANCY),
    # Supply
    (re.compile(r"\b(supply of|purchase of|procurement of|delivery of|acquisition of|provision of goods|purchase|acquisition|supply)\b", re.I), ProcurementType.SUPPLY),
    # Services (broad catch-all — must come after Supply)
    (re.compile(r"\b(services?|maintenance|support|management|operation|cleaning|catering|security|training|audit|inspection|hiring of|provision of services|transport|logistics|monitoring)\b", re.I), ProcurementType.SERVICES),
]


def detect_procurement_type(text: str) -> ProcurementType:
    """Return first matched procurement type, or UNKNOWN."""
    if not text:
        return ProcurementType.UNKNOWN
    for pattern, ptype in _PROCUREMENT_RULES:
        if pattern.search(text):
            return ptype
    return ProcurementType.UNKNOWN


def extract_value_usd(text: str) -> Optional[float]:
    """Extract the first plausible numeric value from text."""
    if not text:
        return None
    matches = _CURRENCY_RE.findall(text)
    if not matches:
        return None
    try:
        raw = matches[0].replace(",", "")
        return float(raw)
    except ValueError:
        return None


def extract_locations(text: str) -> list[str]:
    """Extract rough location mentions from text."""
    if not text:
        return []
    found = _LOCATION_RE.findall(text)
    return [loc.strip() for loc in found if len(loc.strip()) > 2]


# ---------------------------------------------------------------------------
# Authority name normalisation (simple pass for now)
# ---------------------------------------------------------------------------

_AUTHORITY_SUFFIXES = re.compile(
    r"\b(ltd|llc|inc|plc|s\.a\.|s\.r\.l\.|gmbh|pvt|limited|incorporated|corporation|corp)\b",
    re.I,
)


def normalize_authority(name: str | None) -> str | None:
    if not name:
        return None
    name = _AUTHORITY_SUFFIXES.sub("", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name or None


# ---------------------------------------------------------------------------
# Tier 0 main processor
# ---------------------------------------------------------------------------

@dataclass
class Tier0Result:
    passed: bool
    reason: str  # why it passed or failed Tier 0
    enriched: Optional[TenderEnriched] = None


def process_tier0(
    row: TenderCSVRow,
    cpv_code: Optional[str] = None,
    cpv_description: Optional[str] = None,
    cpv_confidence: Optional[float] = None,
) -> Tier0Result:
    """
    Apply Tier 0 rule-based enrichment.

    The CPV mapping is done separately (by cpv.mapper) before calling this.
    Pass the result in so Tier 0 can decide if CPV confidence is sufficient.

    Returns:
        Tier0Result with passed=True if the record is fully enriched by rules.
    """
    text = row.searchable_text()

    # --- Step 1: Length filter ---
    if not text or len(text) < settings.min_description_length:
        return Tier0Result(
            passed=False,
            reason=f"Text too short ({len(text)} chars)",
        )

    # --- Step 2: CPV confidence check ---
    cpv_ok = (cpv_confidence is not None and cpv_confidence >= settings.tier0_cpv_confidence_threshold)

    # --- Step 3: Procurement type ---
    proc_type = detect_procurement_type(text)
    proc_ok = proc_type != ProcurementType.UNKNOWN

    # --- Step 4: Extract value and locations ---
    extracted_value = extract_value_usd(text)
    locations = extract_locations(text)

    # --- Step 5: Decide pass/fail ---
    if cpv_ok and proc_ok:
        enriched = TenderEnriched(
            tot_id=row.tot_id,
            cpv_code=cpv_code,
            cpv_description=cpv_description,
            cpv_confidence=cpv_confidence,
            procurement_type=proc_type,
            tier_processed=TierProcessed.TIER0,
            extracted_value_usd=extracted_value,
            location_mentions=locations,
        )
        return Tier0Result(passed=True, reason="CPV+ProcType resolved by rules", enriched=enriched)

    reasons = []
    if not cpv_ok:
        conf_str = f"{cpv_confidence:.2f}" if cpv_confidence is not None else "None"
        reasons.append(f"CPV confidence={conf_str} < {settings.tier0_cpv_confidence_threshold}")
    if not proc_ok:
        reasons.append("ProcurementType=UNKNOWN")

    return Tier0Result(passed=False, reason="; ".join(reasons))
