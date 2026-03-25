"""
TenderScout API — FastAPI application entry point.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.api.admin import router as admin_router
from app.api.ingest import router as ingest_router
from app.api.search import router as search_router
from app.database import init_db
from app.indexing.bm25 import load_bm25_from_db
from app.indexing.qdrant_client import ensure_collections


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting TenderScout API...")

    await init_db()
    logger.info("Database tables ready")

    await ensure_collections()
    logger.info("Qdrant collections ready")

    try:
        await load_bm25_from_db()
    except Exception as e:
        logger.warning(f"BM25 index load skipped (DB may be empty): {e}")

    yield

    logger.info("Shutting down TenderScout API")


app = FastAPI(
    title="TenderScout API",
    description="AI-powered tender search — multi-index with CPV enrichment",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(admin_router)


@app.get("/health")
async def health():
    from app.indexing.bm25 import get_bm25_index
    return {
        "status": "ok",
        "version": "0.2.0",
        "bm25_documents": get_bm25_index().size,
    }
