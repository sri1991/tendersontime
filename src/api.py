import logging
import time
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import shutil
import json
import asyncio
from datetime import datetime

from src.search.engine import SmartSearchEngine
from src.ingestion.pipeline import IngestionPipeline

# Configure logging
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI(title="Tender Search API", version="1.0")

# Mount UI directory
app.mount("/src/ui", StaticFiles(directory="src/ui"), name="ui")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Search Engine
try:
    search_engine = SmartSearchEngine()
    logging.info("Search Engine initialized successfully.")
except Exception as e:
    logging.error(f"Failed to initialize Search Engine: {e}")
    search_engine = None

# Global Ingestion State
ingestion_state = {
    "status": "idle",
    "progress": 0,
    "total": 0,
    "current_offset": 0,
    "last_log": "",
    "chroma_count": 0
}

async def run_ingestion_task(file_path: str):
    global ingestion_state
    ingestion_state["status"] = "processing"
    
    async def update_progress(data):
        input_data = data # Capture data
        ingestion_state["status"] = input_data["status"]
        ingestion_state["current_offset"] = input_data["current"]
        ingestion_state["total"] = input_data["total"]
        ingestion_state["last_log"] = input_data["last_log"]
        # Calculate progress %
        if ingestion_state["total"] > 0:
            ingestion_state["progress"] = round((ingestion_state["current_offset"] / ingestion_state["total"]) * 100, 1)
        
    try:
        pipeline = IngestionPipeline(input_file=file_path, progress_callback=update_progress)
        await pipeline.run()
        ingestion_state["status"] = "completed"
        ingestion_state["progress"] = 100
    except Exception as e:
        ingestion_state["status"] = "error"
        ingestion_state["last_log"] = f"Error: {str(e)}"
        logging.error(f"Ingestion Task Failed: {e}")

# ... existing code ...

@app.post("/api/ingest/upload")
async def upload_ingest(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    # Save file
    os.makedirs("temp_ingest", exist_ok=True)
    file_path = f"temp_ingest/{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Start Background Task
    background_tasks.add_task(run_ingestion_task, file_path)
    
    return {"message": "Ingestion started", "file": file.filename}

@app.get("/api/ingest/status")
async def get_ingest_status():
    global ingestion_state
    return ingestion_state



class SearchRequest(BaseModel):
    query: str
    limit: int = 100
    include_corrigendum: bool = True

class ChatRequest(BaseModel):
    tender_id: str
    message: str

class FeedbackRequest(BaseModel):
    query: str
    result_id: str
    rating: int  # 1 for functional/relevant, -1 for not relevant
    position: Optional[int] = None # Rank in the results list (0, 1, 2...)
    session_id: Optional[str] = None # For tracking user sessions
    meta: Optional[Dict[str, Any]] = None # Snapshot of the result metadata for dataset training
    comment: Optional[str] = None


@app.get("/")
async def read_index():
    return FileResponse('src/ui/index.html')

@app.get("/health")
async def health_check():
    """Extended health check — reports status of DB, Redis, and search engine."""
    status: Dict[str, Any] = {"status": "ok"}

    # Check PostgreSQL
    try:
        from sqlalchemy import text
        from src.db.schema import engine as db_engine
        async with db_engine.connect() as conn:
            row = await conn.execute(text("SELECT COUNT(*) FROM tenders"))
            count = row.scalar()
        status["postgres"] = {"status": "ok", "tender_count": count}
    except Exception as e:
        status["postgres"] = {"status": "error", "detail": str(e)}
        status["status"] = "degraded"

    # Check Redis intent cache
    try:
        if search_engine and search_engine.intent_cache:
            redis_ok = await search_engine.intent_cache.ping()
            status["redis"] = {"status": "ok" if redis_ok else "unavailable"}
        else:
            status["redis"] = {"status": "not_initialised"}
    except Exception as e:
        status["redis"] = {"status": "error", "detail": str(e)}

    return status


@app.get("/api/dlq")
async def get_dlq(limit: int = Query(50, le=500), offset: int = Query(0, ge=0)):
    """Returns recent Dead Letter Queue entries for failed ingestion records."""
    try:
        from sqlalchemy import text
        from src.db.schema import engine as db_engine
        async with db_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, tender_id, error, retry_count, created_at "
                    "FROM ingestion_dlq ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset},
            )
            rows = [dict(r) for r in result.mappings().all()]

            total_result = await conn.execute(text("SELECT COUNT(*) FROM ingestion_dlq"))
            total = total_result.scalar()

        # Convert datetime to string for JSON serialisation
        for row in rows:
            if row.get("created_at"):
                row["created_at"] = str(row["created_at"])

        return {"total": total, "limit": limit, "offset": offset, "entries": rows}
    except Exception as e:
        logging.error(f"DLQ fetch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat_tender(request: ChatRequest):
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search Engine not initialized")
    
    answer = await search_engine.chat_with_tender(request.tender_id, request.message)
    return {"answer": answer}

@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    try:
        from sqlalchemy import text
        from src.db.schema import engine as db_engine
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO feedback (query, result_id, rating, position, session_id, comment, meta) "
                    "VALUES (:query, :result_id, :rating, :position, :session_id, :comment, CAST(:meta AS jsonb))"
                ),
                {
                    "query":      request.query,
                    "result_id":  request.result_id,
                    "rating":     request.rating,
                    "position":   request.position,
                    "session_id": request.session_id,
                    "comment":    request.comment,
                    "meta":       json.dumps(request.meta) if request.meta else None,
                },
            )
        return {"status": "success", "message": "Feedback recorded"}
    except Exception as e:
        logging.error(f"Feedback Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search")
async def search_tenders(request: SearchRequest):
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search Engine not initialized")
    
    start_time = time.time()
    try:
        # Perform Search
        results = await search_engine.search(request.query, k=request.limit, include_corrigendum=request.include_corrigendum)
        
        # Process Results for Frontend
        processed_results = []
        
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        # distances now holds the pre-computed multi-signal score [0,1] from the engine
        scores_raw = results.get("distances", [[]])[0]

        for i, tender_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            score = scores_raw[i] if i < len(scores_raw) else 0.0

            # Skip results that have effectively zero relevance
            if score <= 0.0:
                continue
            score_pct = round(score * 100, 1)
            
            # Determine Label and Color
            if score >= 0.85:
                label = "Excellent Match"
                color = "green"
            elif score >= 0.65:
                label = "Strong Match"
                color = "teal"
            elif score >= 0.45:
                label = "Good Match"
                color = "yellow"
            elif score >= 0.20:
                label = "Potential Lead"
                color = "gray"
            else:
                label = "Weak Lead"
                color = "lightgray"
            
            processed_results.append({
                "id": tender_id,
                "score": score_pct,
                "match_label": label,
                "match_color": color,
                "title": meta.get("title") or meta.get("original_title") or "No Title",
                "description": meta.get("description", "No description available."),
                "core_domain": meta.get("core_domain", "Unclassified"),
                "procurement_type": meta.get("procurement_type", "Unknown"),
                "authority": meta.get("authority_name", "Unknown"),
                "country": meta.get("country", "Unknown"),
                "city": meta.get("location_city", "Unknown"),
                "state": meta.get("location_state", "Unknown"),
                "closing_date": meta.get("closing_date", "N/A"),
                "url": meta.get("url", "#"),
                "ref_no": meta.get("ref_no", "N/A"),
                "tot_id": meta.get("tot_id", "N/A"),
                "is_corrigendum": meta.get("is_corrigendum", False)
            })
            
        latency = round(time.time() - start_time, 3)
        return {
            "query": request.query,
            "count": len(processed_results),
            "latency_seconds": latency,
            "search_meta": results.get("meta", {}),
            "results": processed_results,
        }
        
    except Exception as e:
        logging.error(f"Search API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
