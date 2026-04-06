# TenderScout AI

AI-powered tender search engine. Processes 80k+ tenders, indexes into PostgreSQL+pgvector, answers natural language queries with hybrid semantic + full-text search.

**Stack**: FastAPI · PostgreSQL+pgvector · Redis · Google Gemini · BM25 → pg FTS · Vanilla HTML/JS

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- Google Gemini API key

### Start the system

```bash
# Copy and fill in your API key
cp .env.example .env
# GEMINI_API_KEY=your_key_here

# Start all services (PostgreSQL, Redis, API)
docker-compose up -d

# Run migrations (first time only — adds FTS index + feedback table)
docker cp src/db/migrations/002_fts_and_feedback.sql to_postgres:/tmp/002.sql
docker exec to_postgres psql -U tender -d tenderscout -f /tmp/002.sql

# Open the UI
open http://localhost:8000
```

### Health check

```bash
curl http://localhost:8000/health
```

---

## Architecture

```
Query → Intent Analysis (Gemini) ──┐
      → Query Embedding (Gemini) ──┤
                                   ↓
                    pgvector HNSW search  ──┐
                    pg FTS (GIN index)   ──┤→ RRF Fusion → Multi-signal Score → Rerank → Results
                                            │
                              0.50×vector + 0.25×fts + 0.15×title_match + 0.10×freshness
```

### Ingestion pipeline
1. Pre-filter (length + keyword check)
2. SHA-256 hash cache (skip already-processed tenders)
3. Batch LLM enrichment (Gemini 2.5 Flash Lite, 10/prompt)
4. Embedding (gemini-embedding-001, 3072-dim halfvec)
5. PostgreSQL upsert → FTS column auto-updated by trigger
6. Failed records → `ingestion_dlq` table

---

## Operations

### Ingest new tenders

```bash
# Upload via UI
open http://localhost:8000/src/ui/ingest.html

# Or copy a CSV into the worker and run directly
docker cp your_tenders.csv to_worker:/app/data/
docker exec to_worker python -c "
import asyncio
from src.ingestion.pipeline import IngestionPipeline
asyncio.run(IngestionPipeline('data/your_tenders.csv').run())
"
```

### Check ingestion status

```bash
curl http://localhost:8000/api/ingest/status
```

### View failed ingestion records (DLQ)

```bash
curl http://localhost:8000/api/dlq
```

### Database stats

```bash
docker exec to_postgres psql -U tender -d tenderscout -c "
SELECT
    COUNT(*) as total_tenders,
    COUNT(DISTINCT core_domain) as domains,
    COUNT(DISTINCT country) as countries
FROM tenders;
"
```

---

## Feedback Analysis

After users rate results (thumbs up/down), run the automated analysis:

```bash
# Copy the script into the container and run
docker cp scripts/analyze_feedback.py to_api:/app/scripts/analyze_feedback.py
docker exec to_api python scripts/analyze_feedback.py

# Generate HTML report
docker exec to_api python scripts/analyze_feedback.py --html
docker cp to_api:/app/tests/reports/<filename>.html tests/reports/

# Only include queries with 5+ ratings
docker exec to_api python scripts/analyze_feedback.py --min-feedback 5 --html
```

The report shows:
- Per-query precision (thumbs up %)
- Score calibration (are our score bands aligned with user perception?)
- Score floor recommendation (what threshold to filter weak results)
- Domain classification issues (Unclassified leakage)
- Failure patterns (low-score results surfacing, top-20 irrelevant results)

---

## Benchmarking (vs SOLR baseline)

```bash
# Run full benchmark suite (10 queries)
python scripts/benchmark_vs_solr.py

# Single query
python scripts/benchmark_vs_solr.py --query "animal ear tag" --limit 50

# Reports saved to tests/reports/
```

---

## Migrations

```bash
# Migration 001 — initial schema (runs automatically on first docker-compose up)
# Migration 002 — FTS index + feedback table (run manually once)
docker cp src/db/migrations/002_fts_and_feedback.sql to_postgres:/tmp/002.sql
docker exec to_postgres psql -U tender -d tenderscout -f /tmp/002.sql
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Required |
| `DATABASE_URL` | `postgresql+asyncpg://tender:tender@postgres:5432/tenderscout` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `SEARCH_BACKEND` | `postgres` | `postgres` or `chroma` (migration fallback) |
| `INTENT_CACHE_TTL_SECONDS` | `3600` | Intent cache TTL (1 hour) |
| `SEARCH_CACHE_TTL_SECONDS` | `600` | Search result cache TTL (10 min) |

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/search` | POST | Search tenders `{query, limit, include_corrigendum}` |
| `/api/chat` | POST | Chat about a tender `{tender_id, message}` |
| `/api/feedback` | POST | Submit rating `{query, result_id, rating, position, meta}` |
| `/api/ingest/upload` | POST | Upload CSV for ingestion |
| `/api/ingest/status` | GET | Ingestion progress |
| `/api/dlq` | GET | Dead letter queue entries |
| `/health` | GET | Service health (postgres, redis) |

---

## GCP Deployment

### 1. Create the VM

```bash
gcloud compute instances create tenderscout \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --tags=http-server \
  --zone=asia-south1-a

gcloud compute firewall-rules create allow-http \
  --allow tcp:80 --target-tags http-server
```

Minimum VM size: **e2-standard-2** (2 vCPU, 8GB RAM).
Anything smaller will OOM-kill PostgreSQL during vector index load.

### 2. SSH in and deploy

```bash
gcloud compute ssh tenderscout --zone=asia-south1-a

# On the VM:
git clone https://github.com/sri1991/tendersontime.git /opt/tenderscout
cd /opt/tenderscout
chmod +x scripts/deploy_gcp.sh
sudo ./scripts/deploy_gcp.sh
```

The script installs Docker, starts all containers, runs migrations, and configures nginx on port 80.

### 3. Set your API key

```bash
sudo nano /opt/tenderscout/.env
# Set: GEMINI_API_KEY=your_real_key

sudo docker compose -f /opt/tenderscout/docker-compose.yml restart api ingest_worker
```

### 4. Restore your database (if migrating from local)

```bash
# On local machine — dump the DB
docker exec to_postgres pg_dump -U tender tenderscout > tenderscout.pgdump

# Copy to VM
gcloud compute scp tenderscout.pgdump tenderscout:/tmp/ --zone=asia-south1-a

# On VM — restore
docker cp /tmp/tenderscout.pgdump to_postgres:/tmp/
docker exec to_postgres pg_restore -U tender -d tenderscout -v /tmp/tenderscout.pgdump

# Re-run migration 002 after restore
docker cp src/db/migrations/002_fts_and_feedback.sql to_postgres:/tmp/002.sql
docker exec to_postgres psql -U tender -d tenderscout -f /tmp/002.sql
```

### 5. Check it's running

```bash
curl http://$(curl -s ifconfig.me)/health
```

### Updating after code changes

```bash
cd /opt/tenderscout
git pull
docker compose up -d --build
```

---

## Logs

```bash
# API logs
docker logs to_api -f

# PostgreSQL logs
docker logs to_postgres -f
```
