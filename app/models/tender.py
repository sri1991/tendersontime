"""
SQLAlchemy ORM model + Pydantic schemas for tender records.
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum as SAEnum,
    Float, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TierProcessed(str, enum.Enum):
    TIER0 = "tier0"
    TIER1 = "tier1"
    TIER2 = "tier2"


class ProcurementType(str, enum.Enum):
    SUPPLY = "Supply"
    WORKS = "Works"
    SERVICES = "Services"
    CONSULTANCY = "Consultancy"
    UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------

class TenderRecord(Base):
    __tablename__ = "tender_records"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tot_id = Column(String(50), unique=True, nullable=False, index=True)
    tender_notice_no = Column(String(200), nullable=True)
    country = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    purchaser_name = Column(String(500), nullable=True)
    purchaser_address = Column(Text, nullable=True)
    purchaser_email = Column(String(200), nullable=True)
    purchaser_url = Column(String(500), nullable=True)
    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tender_value = Column(String(50), nullable=True)
    currency = Column(String(10), nullable=True)
    usd_tender_value = Column(Numeric(20, 2), nullable=True)
    published_date = Column(DateTime, nullable=True)
    closing_date = Column(DateTime, nullable=True)
    competition = Column(String(50), nullable=True)
    financier_name = Column(String(200), nullable=True)
    cpv_code = Column(String(20), nullable=True, index=True)
    cpv_description = Column(String(300), nullable=True)
    cpv_confidence = Column(Float, nullable=True)
    tender_notice_document = Column(String(1000), nullable=True)

    # Enrichment fields
    procurement_type = Column(SAEnum(ProcurementType), nullable=True)
    tier_processed = Column(SAEnum(TierProcessed), nullable=True)
    dedup_hash = Column(String(64), unique=True, nullable=False, index=True)
    is_indexed = Column(Boolean, default=False, nullable=False)
    qdrant_id = Column(String(100), nullable=True)

    # Extracted by Tier 0 / Tier 2
    extracted_value_usd = Column(Numeric(20, 2), nullable=True)
    location_mentions = Column(Text, nullable=True)  # JSON array string

    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_tender_country_published", "country", "published_date"),
        Index("ix_tender_cpv_tier", "cpv_code", "tier_processed"),
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas (for CSV row validation and API)
# ---------------------------------------------------------------------------

class TenderCSVRow(BaseModel):
    """Maps directly to a row in the CSV file."""
    tot_id: str = Field(alias="TOT_ID")
    tender_notice_no: Optional[str] = Field(None, alias="Tender_Notice_No")
    country: Optional[str] = Field(None, alias="Country")
    state: Optional[str] = Field(None, alias="State")
    purchaser_name: Optional[str] = Field(None, alias="Purchaser_Name")
    purchaser_address: Optional[str] = Field(None, alias="Purchaser_Address")
    purchaser_email: Optional[str] = Field(None, alias="Purchaser_EmailId")
    purchaser_url: Optional[str] = Field(None, alias="Purchaser_Url")
    summary: Optional[str] = Field(None, alias="Summary")
    description: Optional[str] = Field(None, alias="Description")
    tender_value: Optional[str] = Field(None, alias="Tender_Value")
    currency: Optional[str] = Field(None, alias="Currency")
    usd_tender_value: Optional[str] = Field(None, alias="USD_Tender_Value")
    published_date: Optional[str] = Field(None, alias="Published_Date")
    closing_date: Optional[str] = Field(None, alias="Closing_Date")
    competition: Optional[str] = Field(None, alias="Competition")
    financier_name: Optional[str] = Field(None, alias="Financier_Name")
    cpv: Optional[str] = Field(None, alias="CPV")
    tender_notice_document: Optional[str] = Field(None, alias="Tender_Notice_Document")

    model_config = {"populate_by_name": True}

    @field_validator("tot_id", mode="before")
    @classmethod
    def strip_tot_id(cls, v):
        return str(v).strip() if v else v

    @field_validator("description", "summary", mode="before")
    @classmethod
    def clean_text(cls, v):
        if not v or str(v).strip() in ("_", "-", "N/A", "n/a", ""):
            return None
        return str(v).strip()

    @field_validator("usd_tender_value", mode="before")
    @classmethod
    def parse_usd(cls, v):
        if not v or str(v).strip() == "":
            return None
        return str(v).strip()

    def compute_dedup_hash(self) -> str:
        """SHA-256 of title + description for deduplication."""
        content = f"{self.summary or ''}{self.description or ''}".lower().strip()
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def searchable_text(self) -> str:
        """Combined text used for embedding and BM25."""
        parts = [self.summary or "", self.description or ""]
        return " ".join(p for p in parts if p).strip()


class TenderEnriched(BaseModel):
    """Output schema after enrichment pipeline."""
    tot_id: str
    cpv_code: Optional[str] = None
    cpv_description: Optional[str] = None
    cpv_confidence: Optional[float] = None
    procurement_type: ProcurementType = ProcurementType.UNKNOWN
    tier_processed: TierProcessed
    extracted_value_usd: Optional[float] = None
    location_mentions: list[str] = []


class TenderSearchResult(BaseModel):
    """API response for a single search hit."""
    tot_id: str
    score: float
    summary: Optional[str]
    description: Optional[str]
    country: Optional[str]
    purchaser_name: Optional[str]
    cpv_code: Optional[str]
    cpv_description: Optional[str]
    procurement_type: Optional[str]
    usd_tender_value: Optional[float]
    published_date: Optional[str]
    closing_date: Optional[str]
    tender_notice_document: Optional[str]
