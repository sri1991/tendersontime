import logging
import time
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import json
from datetime import datetime

from src.search.engine import SmartSearchEngine

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI(title="Tender Search API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all for local dev
    allow_credentials=True,
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

class SearchRequest(BaseModel):
    query: str
    limit: int = 100
    include_corrigendum: bool = False

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
def health_check():
    return {"status": "ok"}

@app.post("/api/chat")
async def chat_tender(request: ChatRequest):
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search Engine not initialized")
    
    answer = await search_engine.chat_with_tender(request.tender_id, request.message)
    return {"answer": answer}

@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    try:
        feedback_entry = {
            "timestamp": datetime.now().isoformat(),
            "query": request.query,
            "result_id": request.result_id,
            "rating": request.rating,
            "position": request.position,
            "session_id": request.session_id,
            "result_metadata_snapshot": request.meta, # Valuable for training later
            "comment": request.comment
        }
        
        # Ensure data directory exists
        os.makedirs("data", exist_ok=True)
        
        # In Docker, 'data/' should be a mounted volume
        with open("data/feedback_logs.jsonl", "a") as f:
            f.write(json.dumps(feedback_entry) + "\n")
            
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
        distances = results.get("distances", [[]])[0]
        
        for i, tender_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            # Chroma returns distance. For Cosine/L2, lower is better.
            # We convert to a 'match score' relative to a threshold.
            # Assuming distance ranges ~0.5 (good) to ~1.0 (bad) for this model.
            dist = distances[i]
            # Calibrated Scoring using Piecewise Linear Mapping for text-embedding-004
            # Dist 0.5 -> 100%
            # Dist 0.7 -> 90% (Strong Semantic Match)
            # Dist 0.9 -> 60% (Broad Context)
            # Dist 1.1 -> 20% (Weak)
            # Dist 1.2 -> 0%
            
            def get_score(d):
                if d <= 0.5: return 1.0
                if d <= 0.7: return 1.0 - (0.1 * (d - 0.5) / 0.2) # 1.0 -> 0.9
                if d <= 0.9: return 0.9 - (0.3 * (d - 0.7) / 0.2) # 0.9 -> 0.6
                if d <= 1.1: return 0.6 - (0.4 * (d - 0.9) / 0.2) # 0.6 -> 0.2
                if d <= 1.2: return 0.2 - (0.2 * (d - 1.1) / 0.1) # 0.2 -> 0.0
                return 0.0

            score = get_score(dist)
            score_pct = round(score * 100, 1)
            
            # Determine Label and Color
            if score >= 0.85:
                label = "Excellent Match"
                color = "green" # UI class
            elif score >= 0.65:
                label = "Strong Match"
                color = "teal"
            elif score >= 0.45:
                label = "Good Match"
                color = "yellow"
            else:
                label = "Potential Lead"
                color = "gray"
            
            processed_results.append({
                "id": tender_id,
                "score": score_pct,
                "match_label": label,
                "match_color": color,
                "title": meta.get("original_title", "No Title"),
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
            "results": processed_results
        }
        
    except Exception as e:
        logging.error(f"Search API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
