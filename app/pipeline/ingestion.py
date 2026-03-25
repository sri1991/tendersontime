"""
CSV ingestion module — reads, validates, and yields TenderCSVRow objects.
Handles encoding issues, malformed rows, and empty/placeholder fields gracefully.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Generator, Iterator

from bs4 import BeautifulSoup
from loguru import logger
from pydantic import ValidationError

from app.models.tender import TenderCSVRow


def _strip_html(text: str | None) -> str | None:
    """Remove HTML tags from a field (e.g. Purchaser_Address)."""
    if not text:
        return None
    if "<" not in text:
        return text.strip() or None
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip() or None


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        cleaned = value.replace(",", "").replace(" ", "")
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def iter_csv_rows(
    filepath: str | Path,
    limit: int | None = None,
    skip: int = 0,
) -> Generator[TenderCSVRow, None, None]:
    """
    Lazily yield validated TenderCSVRow from a CSV file.

    Args:
        filepath: Path to the CSV file.
        limit:    Max number of rows to yield (None = all).
        skip:     Number of data rows to skip from the start.

    Yields:
        TenderCSVRow objects.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    yielded = 0
    skipped = 0
    errors = 0

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)

        for raw_row in reader:
            # Skip rows
            if skipped < skip:
                skipped += 1
                continue

            # Stop at limit
            if limit is not None and yielded >= limit:
                break

            # Strip whitespace from all values
            row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items()}

            # Clean HTML from address field
            if "Purchaser_Address" in row:
                row["Purchaser_Address"] = _strip_html(row.get("Purchaser_Address"))

            try:
                tender = TenderCSVRow.model_validate(row)
                yield tender
                yielded += 1
            except ValidationError as e:
                errors += 1
                logger.warning(
                    f"Validation error on TOT_ID={row.get('TOT_ID', '?')}: {e.error_count()} errors"
                )
                continue

    logger.info(
        f"CSV ingestion complete: {yielded} yielded, {errors} validation errors, skip={skip}"
    )


def load_sample(
    filepath: str | Path,
    n: int = 50,
    skip: int = 0,
) -> list[TenderCSVRow]:
    """Load a fixed sample of n rows from the CSV (for testing)."""
    return list(iter_csv_rows(filepath, limit=n, skip=skip))
