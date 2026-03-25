"""
Ingestion job tracker — stores per-job state in Redis.

Schema (Redis key: ingest:job:{job_id}):
  {
    "job_id":        str,
    "filename":      str,
    "status":        "queued" | "running" | "completed" | "failed",
    "started_at":    ISO-8601,
    "finished_at":   ISO-8601 | null,
    "total_rows":    int | null,
    "rows_processed": int,
    "progress_pct":  float,
    "stats":         {total, dupes, tier0, tier2, indexed, errors, skipped_short},
    "error_message": str | null,
  }

Recent job list (Redis key: ingest:jobs): Redis list of job_ids (max 20)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import redis.asyncio as aioredis

from app.config import get_settings

_settings = get_settings()

# TTL for completed job records: 7 days
_JOB_TTL = 7 * 24 * 3600
_JOBS_LIST_KEY = "ingest:jobs"
_MAX_JOBS = 20


def _job_key(job_id: str) -> str:
    return f"ingest:job:{job_id}"


def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(_settings.redis_url, decode_responses=True)


async def create_job(filename: str, total_rows: Optional[int] = None) -> str:
    """Create a new job entry, return job_id."""
    job_id = str(uuid4())
    data = {
        "job_id": job_id,
        "filename": filename,
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "total_rows": total_rows,
        "rows_processed": 0,
        "progress_pct": 0.0,
        "stats": {
            "total": 0, "dupes": 0, "tier0": 0,
            "tier2": 0, "indexed": 0, "errors": 0, "skipped_short": 0,
        },
        "error_message": None,
    }
    async with _get_redis() as r:
        await r.set(_job_key(job_id), json.dumps(data), ex=_JOB_TTL)
        await r.lpush(_JOBS_LIST_KEY, job_id)
        await r.ltrim(_JOBS_LIST_KEY, 0, _MAX_JOBS - 1)
    return job_id


async def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    rows_processed: Optional[int] = None,
    total_rows: Optional[int] = None,
    stats_delta: Optional[dict] = None,
    error_message: Optional[str] = None,
    finished: bool = False,
) -> None:
    """Patch job fields in Redis."""
    async with _get_redis() as r:
        raw = await r.get(_job_key(job_id))
        if not raw:
            return
        data = json.loads(raw)

        if status:
            data["status"] = status
            if status == "running" and data.get("started_at") is None:
                data["started_at"] = datetime.now(timezone.utc).isoformat()
        if rows_processed is not None:
            data["rows_processed"] = rows_processed
        if total_rows is not None:
            data["total_rows"] = total_rows
        if stats_delta:
            for k, v in stats_delta.items():
                data["stats"][k] = data["stats"].get(k, 0) + v
        if error_message:
            data["error_message"] = error_message
        if finished:
            data["finished_at"] = datetime.now(timezone.utc).isoformat()

        # Recompute progress
        if data["total_rows"]:
            data["progress_pct"] = round(
                data["rows_processed"] / data["total_rows"] * 100, 1
            )

        await r.set(_job_key(job_id), json.dumps(data), ex=_JOB_TTL)


async def get_job(job_id: str) -> Optional[dict]:
    async with _get_redis() as r:
        raw = await r.get(_job_key(job_id))
        return json.loads(raw) if raw else None


async def list_jobs() -> list[dict]:
    """Return recent jobs, newest first."""
    async with _get_redis() as r:
        job_ids = await r.lrange(_JOBS_LIST_KEY, 0, _MAX_JOBS - 1)
        jobs = []
        for jid in job_ids:
            raw = await r.get(_job_key(jid))
            if raw:
                jobs.append(json.loads(raw))
        return jobs
