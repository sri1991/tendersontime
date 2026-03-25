"""
CPV taxonomy loader.

Loads CPV codes from a JSON file (data/cpv_taxonomy.json).
Falls back to a minimal built-in set if file not found (for first-run bootstrap).

Expected JSON format:
[
  {"code": "30213100", "description": "Portable computers"},
  ...
]
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from loguru import logger

_TAXONOMY_PATH = Path(__file__).parent.parent.parent / "data" / "cpv_taxonomy.json"

# Minimal fallback (expanded during bootstrap)
_FALLBACK_CPV = [
    {"code": "30213100", "description": "Portable computers"},
    {"code": "30200000", "description": "Computer equipment and supplies"},
    {"code": "45000000", "description": "Construction work"},
    {"code": "72000000", "description": "IT services: consulting, software development, Internet and support"},
    {"code": "33100000", "description": "Medical equipments"},
    {"code": "34100000", "description": "Motor vehicles"},
    {"code": "79000000", "description": "Business services: law, marketing, consulting, recruitment, printing and security"},
    {"code": "90000000", "description": "Sewage, refuse, cleaning and environmental services"},
    {"code": "60000000", "description": "Transport services"},
    {"code": "55000000", "description": "Hotel, restaurant and retail trade services"},
]


@lru_cache(maxsize=1)
def load_taxonomy() -> list[dict]:
    """Load and cache CPV taxonomy. Returns list of {code, description} dicts."""
    if _TAXONOMY_PATH.exists():
        with open(_TAXONOMY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded {len(data)} CPV codes from {_TAXONOMY_PATH}")
        return data
    else:
        logger.warning(
            f"CPV taxonomy not found at {_TAXONOMY_PATH}. "
            "Using fallback set. Run scripts/download_cpv.py to get the full taxonomy."
        )
        return _FALLBACK_CPV


def get_code_map() -> dict[str, str]:
    """Return {code: description} dict."""
    return {item["code"]: item["description"] for item in load_taxonomy()}
