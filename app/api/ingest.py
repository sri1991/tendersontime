"""
Ingestion API endpoints.

POST /api/v1/ingest/upload   — upload a CSV file, start ingestion job
POST /api/v1/ingest/csv      — trigger ingestion on a server-side CSV path
GET  /api/v1/ingest/status/{job_id} — poll job progress
GET  /api/v1/ingest/jobs     — list recent jobs
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel

from app.pipeline.job_tracker import create_job, get_job, list_jobs, update_job

router = APIRouter(prefix="/api/v1/ingest", tags=["Ingestion"])

# Uploads stored here on VM; created on first use
_UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"


# ─── Response models ──────────────────────────────────────────────────────────

class JobStartedResponse(BaseModel):
    job_id: str
    filename: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    filename: str
    status: str                     # queued | running | completed | failed
    started_at: Optional[str]
    finished_at: Optional[str]
    total_rows: Optional[int]
    rows_processed: int
    progress_pct: float
    stats: dict
    error_message: Optional[str]


class IngestCSVRequest(BaseModel):
    csv_path: Optional[str] = None
    limit: Optional[int] = None
    skip: int = 0
    batch_size: int = 200
    dry_run: bool = False


# ─── Background pipeline ──────────────────────────────────────────────────────

async def _run_pipeline(
    job_id: str,
    csv_path: str,
    limit: Optional[int],
    skip: int,
    batch_size: int,
    dry_run: bool,
) -> None:
    """Background task: stream CSV → batched orchestrator, update Redis state."""
    from app.database import AsyncSessionLocal
    from app.pipeline.ingestion import iter_csv_rows
    from app.pipeline.orchestrator import process_batch

    await update_job(job_id, status="running")

    try:
        rows_processed = 0
        batch: list = []

        async with AsyncSessionLocal() as session:
            for row in iter_csv_rows(csv_path, limit=limit, skip=skip):
                batch.append(row)
                if len(batch) < batch_size:
                    continue

                stats = await process_batch(batch, session, dry_run=dry_run)
                rows_processed += len(batch)
                batch.clear()

                await update_job(
                    job_id,
                    rows_processed=rows_processed,
                    stats_delta=stats,
                )

            # Final partial batch
            if batch:
                stats = await process_batch(batch, session, dry_run=dry_run)
                rows_processed += len(batch)
                batch.clear()
                await update_job(
                    job_id,
                    rows_processed=rows_processed,
                    stats_delta=stats,
                )

        await update_job(job_id, status="completed", finished=True)
        logger.info(f"Job {job_id} completed: {rows_processed} rows processed")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        await update_job(
            job_id,
            status="failed",
            error_message=str(e),
            finished=True,
        )
    finally:
        # Clean up uploaded file after processing
        p = Path(csv_path)
        if p.is_relative_to(_UPLOAD_DIR) and p.exists():
            p.unlink()
            logger.info(f"Cleaned up uploaded file: {p.name}")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=JobStartedResponse, status_code=202)
async def upload_and_ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="CSV file to ingest"),
    batch_size: int = Query(200, ge=10, le=1000),
    limit: Optional[int] = Query(None, description="Max rows (None = all)"),
    skip: int = Query(0, ge=0),
    dry_run: bool = Query(False),
):
    """
    Upload a CSV and start ingestion in the background.
    Returns a job_id you can poll via GET /api/v1/ingest/status/{job_id}.

    Example:
        curl -X POST https://your-vm/api/v1/ingest/upload \\
             -F "file=@tenders_2026_03_08.csv"
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    # Save upload to disk
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_DIR / file.filename
    # If same filename exists, make unique
    if dest.exists():
        stem = Path(file.filename).stem
        dest = _UPLOAD_DIR / f"{stem}_{_unique_suffix()}.csv"

    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    # Count rows for progress (fast, header-only scan)
    total_rows = _count_csv_rows(dest)

    job_id = await create_job(filename=dest.name, total_rows=total_rows)
    logger.info(f"Job {job_id} created for {dest.name} ({total_rows or '?'} rows)")

    background_tasks.add_task(
        _run_pipeline,
        job_id=job_id,
        csv_path=str(dest),
        limit=limit,
        skip=skip,
        batch_size=batch_size,
        dry_run=dry_run,
    )

    return JobStartedResponse(
        job_id=job_id,
        filename=dest.name,
        message=f"Ingestion started. Poll /api/v1/ingest/status/{job_id} for progress.",
    )


@router.post("/csv", response_model=JobStartedResponse, status_code=202)
async def ingest_server_csv(
    req: IngestCSVRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger ingestion on a CSV already present on the server."""
    csv_path = req.csv_path or str(
        Path(__file__).parent.parent.parent / "tender_dataset_06082025_6Jan2026.csv"
    )
    if not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail=f"CSV not found: {csv_path}")

    total_rows = _count_csv_rows(Path(csv_path))
    job_id = await create_job(filename=Path(csv_path).name, total_rows=total_rows)

    background_tasks.add_task(
        _run_pipeline,
        job_id=job_id,
        csv_path=csv_path,
        limit=req.limit,
        skip=req.skip,
        batch_size=req.batch_size,
        dry_run=req.dry_run,
    )

    return JobStartedResponse(
        job_id=job_id,
        filename=Path(csv_path).name,
        message=f"Ingestion started. Poll /api/v1/ingest/status/{job_id} for progress.",
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Poll ingestion job progress."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatusResponse(**job)


@router.get("/jobs", response_model=list[JobStatusResponse])
async def get_recent_jobs():
    """List the 20 most recent ingestion jobs."""
    jobs = await list_jobs()
    return [JobStatusResponse(**j) for j in jobs]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _count_csv_rows(path: Path) -> Optional[int]:
    """Fast line count (approximate row count) without loading into memory."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f) - 1  # subtract header
    except Exception:
        return None


def _unique_suffix() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")
