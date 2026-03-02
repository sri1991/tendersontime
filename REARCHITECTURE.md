# TenderScout AI — Re-Architecture Document

## Why We're Re-Architecting

The original system was built to validate the product idea quickly. It works, but has several structural issues that will compound as usage and data volume grow:

| Problem | Impact |
|---|---|
| BM25 index rebuilt in memory from 85k+ docs on every startup | Multi-minute cold start, memory spike |
| ChromaDB handles both vectors and metadata | Can't filter/sort by date with SQL, no pagination |
| 2 LLM calls per search query, serially | 2–4s latency, cost grows linearly with users |
| Failed ingestion batches silently `continue` | Unknown data loss, no recovery path |
| Enrichment cache is a flat JSON file | Will hit hundreds of MB, single writer, no TTL |
| Two Gemini SDK versions (`google.generativeai` + `google.genai`) | Two auth paths, two upgrade tracks |
| Closing dates stored but never used in ranking | Tender closing tomorrow ranks same as one in 6 months |
| Feedback logs collected but never used | Wasted signal |

---

## New Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        INGESTION                             │
│                                                              │
│  CSV/Excel → Pre-filter → SHA256 cache → Batch LLM (x10)    │
│                                   ↓                          │
│                           Schema Validation                  │
│                                   ↓                          │
│              ┌────────────────────┴────────────────────┐     │
│              ↓                                         ↓     │
│       PostgreSQL+pgvector                    BM25 Index      │
│       (tenders table)                     (disk-persisted)   │
│              ↓ (on failure)                                   │
│          ingestion_dlq table                                  │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                          SEARCH                              │
│                                                              │
│  User Query                                                  │
│       │                                                      │
│       ├──→ Redis Intent Cache (hit → skip LLM)               │
│       │         ↓ (miss)                                     │
│       │    ┌────┴──────────────────────────┐                 │
│       │    │  asyncio.gather (parallel)    │                 │
│       │    │  ┌─────────────┐ ┌─────────┐  │                 │
│       │    │  │Intent (LLM) │ │Embedding│  │                 │
│       │    │  └─────────────┘ └─────────┘  │                 │
│       │    └───────────────────────────────┘                 │
│       │                   ↓                                  │
│       │         Hybrid Retrieval                             │
│       │    ┌──────────────────────────┐                      │
│       │    │ pgvector cosine search   │                      │
│       │    │ BM25 (from disk index)   │                      │
│       │    │ RRF fusion               │                      │
│       │    └──────────────────────────┘                      │
│       │                   ↓                                  │
│       │    Multi-signal Scoring                              │
│       │    (vector + BM25 + freshness)                       │
│       │                   ↓                                  │
│       │    Conditional Re-rank (LLM only if needed)          │
│       │                   ↓                                  │
│       └──→  FastAPI Response                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## Infrastructure Changes

### docker-compose.yml — 4 Services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | Primary datastore (tenders + DLQ) |
| `redis` | `redis:7-alpine` | Intent analysis cache |
| `api` | local build | FastAPI search + ingest |
| `chroma` | `chromadb/chroma` | Retained for migration period only |

### New Environment Variables
```
DATABASE_URL=postgresql+asyncpg://tender:tender@postgres:5432/tenderscout
REDIS_URL=redis://redis:6379/0
```

---

## Database Schema

### `tenders` table
```sql
CREATE TABLE tenders (
    id               TEXT PRIMARY KEY,         -- RefNo or TOT_ID
    tot_id           TEXT,
    ref_no           TEXT,
    title            TEXT NOT NULL,
    description      TEXT,
    signal_summary   TEXT,
    search_keywords  TEXT[],
    project_tags     TEXT[],
    core_domain      TEXT NOT NULL DEFAULT 'Other',
    procurement_type TEXT NOT NULL DEFAULT 'Unknown',
    authority_name   TEXT,
    location_city    TEXT,
    location_state   TEXT,
    country          TEXT,
    closing_date     DATE,
    url              TEXT,
    is_corrigendum   BOOLEAN DEFAULT FALSE,
    embedding_text   TEXT,
    embedding        vector(768),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX ON tenders USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX ON tenders (core_domain);
CREATE INDEX ON tenders (closing_date);
CREATE INDEX ON tenders (is_corrigendum);
```

### `ingestion_dlq` table (Dead Letter Queue)
```sql
CREATE TABLE ingestion_dlq (
    id          SERIAL PRIMARY KEY,
    tender_id   TEXT,
    raw_data    JSONB,
    error       TEXT,
    retry_count INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Search Pipeline Changes

### 1. Parallel Intent Analysis + Embedding
Before: Intent LLM → embed (sequential ~800ms+)
After: Both run simultaneously via `asyncio.gather` (~400ms saved per query)

```python
intent, query_vec = await asyncio.gather(
    self.analyze_intent(query),
    asyncio.to_thread(self.get_embedding, query)
)
```

### 2. Intent Cache (Redis, TTL 1 hour)
The same query or very similar queries repeat throughout the day. Caching the intent analysis result eliminates one LLM call entirely for warm cache hits.

```python
cache_key = f"intent:{sha256(query.lower().strip())}"
# Cache hit → return in <1ms instead of ~400ms LLM call
```

### 3. Vector Search via pgvector
ChromaDB replaced by direct PostgreSQL+pgvector query. Enables:
- `WHERE closing_date > CURRENT_DATE` — filter out closed tenders
- `ORDER BY closing_date ASC` — sort by urgency
- `WHERE core_domain = $1` — fast indexed domain filter
- Proper `LIMIT` / `OFFSET` pagination

```sql
SELECT id, title, ..., 1 - (embedding <=> $1::vector) AS vector_sim
FROM tenders
WHERE core_domain = ANY($2)
  AND closing_date > CURRENT_DATE
ORDER BY embedding <=> $1::vector
LIMIT $3;
```

### 4. Honest Multi-Signal Scoring

Old scoring used cosine distance only, with fake `0.85` defaults for BM25-only hits.

New formula:
```
vector_sim  = max(0, 1 - cosine_distance)         # [0, 1]
bm25_norm   = bm25_score / max_bm25_score          # [0, 1], 0 if not in BM25
freshness   = based on days until closing_date
              < 7 days  → 1.0   (urgent)
              7–30 days → 0.8
              30–90 days→ 0.6
              > 90 days → 0.4
              past/null → 0.1

final_score = 0.6 * vector_sim + 0.3 * bm25_norm + 0.1 * freshness
```

Results now show a "Keyword Match" badge when BM25 was the primary signal, making the match source transparent to users.

### 5. Conditional Re-ranking
Re-ranking costs one LLM call per search. We skip it when retrieval is clearly good:

```python
# If top result scores >= 0.85 AND query is specific (not broad) → skip re-rank
if not is_broad and scores[0] >= 0.85:
    return results  # Already excellent
```

Estimated savings: ~50% of re-rank calls eliminated for narrow, specific queries.

---

## Ingestion Pipeline Changes

### Dead Letter Queue
Failed records now written to `ingestion_dlq` table instead of silently skipped:
```python
except Exception as e:
    await db.execute(
        "INSERT INTO ingestion_dlq (tender_id, raw_data, error) VALUES ($1, $2, $3)",
        tender_id, json.dumps(raw_row), str(e)
    )
    continue  # Still continues, but failure is now tracked
```

### Persisted BM25 Index
BM25 index serialized to `data/bm25_index/` after each ingestion batch.
At API startup: load from disk in <1 second instead of rebuilding from 85k+ ChromaDB documents.

### SDK Consolidation
`TenderEnricher` migrated from `google.generativeai` (old) to `google.genai` (new).
Single SDK, single auth path, consistent retry/error behavior.

---

## New File Structure

```
src/
├── db/
│   ├── __init__.py
│   ├── schema.py              # SQLAlchemy async models
│   ├── migrations/
│   │   └── 001_initial.sql    # PostgreSQL + pgvector schema
│   ├── postgres_loader.py     # Replaces chroma_loader.py
│   └── bm25_store.py          # Persisted BM25 index manager
├── cache/
│   ├── __init__.py
│   └── intent_cache.py        # Redis-backed intent analysis cache
scripts/
└── migrate_chroma_to_postgres.py  # One-time migration
```

---

## Migration Plan

1. `docker-compose up` — starts postgres + redis alongside existing chroma
2. `python scripts/migrate_chroma_to_postgres.py` — reads all records from ChromaDB, writes to PostgreSQL (batched, resumable)
3. Verify record count matches
4. Update `SEARCH_BACKEND=postgres` env var → API switches to pgvector
5. Run parallel search validation (compare results between backends)
6. Remove ChromaDB service from docker-compose once confident

---

## Performance Expectations

| Metric | Before | After |
|---|---|---|
| API cold start (BM25 load) | 30–120s for 85k docs | <2s (load from disk) |
| Search latency (avg) | 2–4s | 1–2s (parallel intent+embed, intent cache) |
| Search latency (cache hit) | 2–4s | 0.5–1s (intent cached) |
| Ingestion visibility | Silent failures | DLQ table, queryable |
| Filtering capability | Domain only | Domain + date + type + location |
| Score transparency | Distance % only | Vector + BM25 + freshness breakdown |
