import os
from typing import AsyncGenerator
from sqlalchemy import (
    Column, Text, Boolean, Integer, ARRAY, TIMESTAMP
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func
from pgvector.sqlalchemy import HALFVEC


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://tender:tender@localhost:5432/tenderscout"
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class Tender(Base):
    __tablename__ = "tenders"

    id               = Column(Text, primary_key=True)
    tot_id           = Column(Text)
    ref_no           = Column(Text)
    title            = Column(Text, nullable=False)
    description      = Column(Text)
    signal_summary   = Column(Text)
    search_keywords  = Column(ARRAY(Text))
    project_tags     = Column(ARRAY(Text))
    core_domain      = Column(Text, nullable=False, default="Other")
    procurement_type = Column(Text, nullable=False, default="Unknown")
    authority_name   = Column(Text)
    location_city    = Column(Text)
    location_state   = Column(Text)
    country          = Column(Text)
    closing_date     = Column(Text)   # stored as TEXT for flexibility; parsed on read
    url              = Column(Text)
    is_corrigendum   = Column(Boolean, default=False)
    embedding_text   = Column(Text)
    embedding        = Column(HALFVEC(3072))
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    query      = Column(Text, nullable=False)
    result_id  = Column(Text)
    rating     = Column(Integer, nullable=False)
    position   = Column(Integer)
    session_id = Column(Text)
    comment    = Column(Text)
    meta       = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class IngestionDLQ(Base):
    __tablename__ = "ingestion_dlq"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    tender_id   = Column(Text)
    raw_data    = Column(JSONB)
    error       = Column(Text)
    retry_count = Column(Integer, default=0)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now())


def get_engine():
    return engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
