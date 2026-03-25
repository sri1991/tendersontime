"""
Celery worker for background ingestion tasks.
Used for production bulk ingestion (not needed for pilot run).
"""
from celery import Celery
from app.config import get_settings

settings = get_settings()

celery = Celery(
    "tenderscout",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # process one task at a time per worker
)


@celery.task(name="ingest_csv_chunk")
def ingest_csv_chunk(csv_path: str, skip: int, limit: int, dry_run: bool = False):
    """Process a chunk of the CSV file."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.pipeline.ingestion import iter_csv_rows
    from app.pipeline.orchestrator import process_batch

    async def _run():
        rows = list(iter_csv_rows(csv_path, limit=limit, skip=skip))
        async with AsyncSessionLocal() as session:
            return await process_batch(rows, session, dry_run=dry_run)

    return asyncio.run(_run())
