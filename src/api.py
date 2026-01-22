import logging
import time
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

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
    limit: int = 20
    include_corrigendum: bool = False

@app.get("/")
async def read_index():
    return FileResponse('src/ui/index.html')

@app.get("/health")
def health_check():
    return {"status": "ok"}

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
            # Heuristic scaling: Map 0.0-1.0 distance to 100-0% score
            # But "good" matches in vectors are often 0.3-0.5 distance.
            # Let's simple invert and clip for now, or use exponential decay.
            # score = 1 / (1 + dist) # 0->1, 1->0.5
            
            # Simple linear inversion for now, but calibrated
            score = max(0, 1 - dist) if i < len(distances) else 0 
            
            processed_results.append({
                "id": tender_id,
                "score": round(score, 4),
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
