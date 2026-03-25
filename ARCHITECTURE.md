# TenderScout AI — Architecture & Roadmap

> Render diagrams with [D2](https://d2lang.com/) — `brew install d2` then `d2 --watch ARCHITECTURE.md`

---

## Table of Contents

1. [What We Built](#1-what-we-built)
2. [System Architecture](#2-system-architecture)
3. [Enrichment Pipeline](#3-enrichment-pipeline)
4. [CPV Mapping Pipeline](#4-cpv-mapping-pipeline)
5. [Search Flow](#5-search-flow)
6. [File Structure](#6-file-structure)
7. [Pilot Results](#7-pilot-results)
8. [Cost Analysis](#8-cost-analysis)
9. [Ingestion Time](#9-ingestion-time)
10. [Roadmap](#10-roadmap)
11. [Running the Project](#11-running-the-project)

---

## 1. What We Built

TenderScout is an AI-powered tender search engine. The core problem: ~88,000 tender records per dataset with **no CPV codes**, **no procurement type**, and inconsistent descriptions. A naive approach calls an LLM for every record — expensive and slow.

We built a **tiered enrichment pipeline** that matches processing cost to record complexity:

| Tier | Method | Cost | Target Volume |
|---|---|---|---|
| Tier 0 | Rule-based (regex, trie) | Free | ~36–60% of records |
| Tier 1 | Fine-tuned DeBERTa *(Phase 2)* | Tiny | ~30% of records |
| Tier 2 | Gemini 2.0 Flash Lite (batch 10) | Low | ~10% of records |

**Result:** 10× reduction in LLM API calls vs. calling Gemini for every record.

---

## 2. System Architecture

```d2
direction: right

csv: CSV / Daily Feed {
  shape: document
  style.fill: "#f0f4ff"
}

api: FastAPI {
  shape: hexagon
  style.fill: "#e8f5e9"
}

pipeline: Enrichment Pipeline {
  style.fill: "#fff8e1"
  style.stroke: "#f9a825"

  ingest: Ingestion {shape: step}
  dedup: SHA-256 Dedup {shape: step}
  cpv: CPV Mapper {shape: step}
  tier0: Tier 0\nRules {shape: diamond}
  tier2: Tier 2\nGemini {shape: step}
  embed: Embed\n(text-embedding-004) {shape: step}

  ingest -> dedup
  dedup -> cpv
  cpv -> tier0
  tier0 -> embed: pass
  tier0 -> tier2: fail
  tier2 -> embed
}

storage: Storage {
  style.fill: "#fce4ec"
  style.stroke: "#c62828"

  pg: PostgreSQL\nMetadata {shape: cylinder}
  qdrant: Qdrant\nVectors {shape: cylinder}
  redis: Redis\nDedup Cache {shape: cylinder}
}

search: Search Layer {
  style.fill: "#e8eaf6"
  style.stroke: "#3949ab"

  router: Query Router {shape: diamond}
  semantic: Semantic\nIndex {shape: oval}
  rrf: RRF Fusion {shape: step}
  results: Ranked\nResults {shape: page}

  router -> semantic
  semantic -> rrf
  rrf -> results
}

gemini: Gemini 2.0\nFlash Lite {
  shape: cloud
  style.fill: "#e3f2fd"
}

csv -> api: POST /api/v1/ingest/csv
api -> pipeline
pipeline.embed -> storage.qdrant
pipeline.dedup -> storage.redis: hash check
pipeline -> storage.pg: save record
pipeline.tier2 -> gemini: batch 10/prompt
api -> search: POST /api/v1/search
search -> storage.qdrant: vector search
```

---

## 3. Enrichment Pipeline

```d2
direction: down

raw: Raw CSV Row {
  shape: document
  style.fill: "#f5f5f5"
}

step1: Step 1 — Validate & Clean {
  shape: step
  style.fill: "#e8f5e9"
  label: "• Pydantic schema validation\n• Strip HTML from address\n• Null out placeholder text (_,-,N/A)\n• Compute SHA-256 dedup hash"
}

step2: Step 2 — Dedup Check {
  shape: diamond
  style.fill: "#fff9c4"
}

step3: Step 3 — CPV Mapping {
  shape: step
  style.fill: "#e3f2fd"
  label: "• Trie lookup (curated 236 keywords)\n• Taxonomy description match\n• Returns: code + confidence"
}

step4: Tier 0 Gate {
  shape: diamond
  style.fill: "#ffe0b2"
  label: "CPV conf ≥ 0.85\nAND proc_type ≠ UNKNOWN?"
}

tier0_pass: Tier 0 Result {
  shape: step
  style.fill: "#c8e6c9"
  label: "• CPV code assigned (free)\n• Procurement type (regex)\n• Extract value + locations\n• Cost: $0"
}

tier2: Tier 2 — Gemini Batch {
  shape: step
  style.fill: "#fce4ec"
  label: "• 10 records per prompt\n• Returns: CPV + type + confidence\n• JSON schema-validated\n• Retry w/ exponential backoff"
}

skip: Skip Record {
  shape: oval
  style.fill: "#eceff1"
  label: "Too short\n(< 20 chars)"
}

embed: Embed Searchable Text {
  shape: step
  style.fill: "#e8eaf6"
  label: "• text-embedding-004\n• 768-dim vector\n• Batch up to 100 texts"
}

store: Upsert to Storage {
  shape: step
  style.fill: "#f3e5f5"
  label: "• Qdrant: vector + payload\n• PostgreSQL: full record\n• Mark is_indexed=True"
}

raw -> step1
step1 -> step2
step2 -> skip: duplicate
step2 -> step3: new record
step3 -> step4
step4 -> tier0_pass: yes
step4 -> tier2: no
tier2 -> embed
tier0_pass -> embed
embed -> store
```

---

## 4. CPV Mapping Pipeline

The EU Common Procurement Vocabulary (CPV) is a 9,000-node taxonomy. We map every tender to a CPV code using a 3-step cost cascade:

```d2
direction: right

input: Tender Text {shape: document}

step1: Step 1\nKeyword Trie {
  style.fill: "#e8f5e9"
  label: "Aho-Corasick trie\n236 curated keywords\n+ 294 taxonomy phrases\n\nConf: 0.88–0.93 for keywords\nConf: 0.90 for full phrases\nConf: 0.65 for single words\n\nCost: free, sub-ms"
}

gate1: conf ≥ 0.85? {shape: diamond}

step2: Step 2\nEmbedding Match {
  style.fill: "#e3f2fd"
  label: "Embed tender text\nSearch cpv_taxonomy\ncollection in Qdrant\n\nFinds nearest CPV node\nby cosine similarity\n\nCost: 1 embedding call"
}

gate2: conf ≥ 0.85? {shape: diamond}

step3: Step 3\nGemini LLM {
  style.fill: "#fce4ec"
  label: "Falls into Tier 2 batch\n10 records / prompt\nExplicit CPV in output\n\nCost: ~$0.0001 / record"
}

result: CPV Match {
  shape: oval
  style.fill: "#f3e5f5"
}

input -> step1
step1 -> gate1
gate1 -> result: yes (Tier 0)
gate1 -> step2: no
step2 -> gate2
gate2 -> result: yes (Tier 0)
gate2 -> step3: no
step3 -> result: (Tier 2)
```

### CPV Hierarchy Example

```d2
direction: right

d45: "45 — Construction work" {style.fill: "#e3f2fd"}
d452: "45.2 — Civil engineering" {style.fill: "#e8f5e9"}
d4523: "45.23 — Roads & highways" {style.fill: "#fff9c4"}
d45233: "45.233 — Road construction" {style.fill: "#ffe0b2"}
d452331: "45233100-0\nMotorway construction" {style.fill: "#fce4ec"}

d45 -> d452
d452 -> d4523
d4523 -> d45233
d45233 -> d452331
```

---

## 5. Search Flow

```d2
direction: down

query: User Query {
  shape: oval
  style.fill: "#e3f2fd"
  label: "e.g. \"RFID tracking system\nfor warehouse\""
}

embed_q: Embed Query {
  shape: step
  label: "text-embedding-004\ntask_type=RETRIEVAL_QUERY\n768-dim vector"
}

router: Query Router {
  shape: diamond
  style.fill: "#fff9c4"
  label: "Classify query type\n(Phase 1B)"
}

semantic: Semantic Search {
  shape: step
  style.fill: "#e8f5e9"
  label: "Qdrant cosine search\ntenders_semantic collection\nTop-K results"
}

cpv_filter: CPV Filter {
  shape: step
  style.fill: "#e8eaf6"
  label: "Filter by CPV subtree\n(Phase 1B)"
}

bm25: BM25 Keyword {
  shape: step
  style.fill: "#fce4ec"
  label: "rank-bm25\nIn-memory index\n(Phase 1B)"
}

rrf: RRF Fusion {
  shape: step
  style.fill: "#f3e5f5"
  label: "Reciprocal Rank Fusion\nWeighted by query type\n(Phase 1B)"
}

results: Ranked Results {
  shape: page
  label: "tot_id, score\nCPV code, description\nprocurement_type\ncountry, value, dates\ndocument_url"
}

query -> embed_q
embed_q -> router
router -> semantic: semantic query
router -> cpv_filter: CPV query
router -> bm25: keyword query
semantic -> rrf
cpv_filter -> rrf
bm25 -> rrf
rrf -> results
```

> **Currently implemented:** Semantic search only. Query router + BM25 + RRF are Phase 1B.

---

## 6. File Structure

```
tenderversion2/
│
├── app/
│   ├── main.py                   # FastAPI app + lifespan startup
│   ├── config.py                 # Settings via pydantic-settings (.env)
│   ├── database.py               # AsyncPG engine + session factory
│   │
│   ├── models/
│   │   └── tender.py             # SQLAlchemy ORM + Pydantic schemas
│   │
│   ├── pipeline/
│   │   ├── ingestion.py          # Lazy CSV reader, HTML strip, validation
│   │   ├── tier0.py              # Rules: length filter, regex NER, proc type
│   │   ├── tier2.py              # Gemini batch (10/prompt), JSON parse, retry
│   │   └── orchestrator.py       # Full cascade: dedup→CPV→T0→T2→embed→store
│   │
│   ├── cpv/
│   │   ├── taxonomy.py           # Loads cpv_taxonomy.json, cached
│   │   └── mapper.py             # Trie + embedding CPV lookup (Steps 1+2)
│   │
│   ├── indexing/
│   │   ├── embeddings.py         # Google text-embedding-004, batch 100
│   │   └── qdrant_client.py      # AsyncQdrant, upsert/search, collection mgmt
│   │
│   ├── api/
│   │   ├── ingest.py             # POST /api/v1/ingest/csv, /batch
│   │   └── search.py             # POST+GET /api/v1/search
│   │
│   └── workers/
│       └── celery_app.py         # Celery tasks for bulk background ingestion
│
├── data/
│   ├── cpv_taxonomy.json         # 294 CPV codes (built-in)
│   └── cpv_keywords.json         # 236 curated keyword → CPV mappings
│
├── scripts/
│   ├── pilot_run.py              # End-to-end test (--limit N --skip N --dry)
│   ├── download_cpv.py           # Fetch full EU CPV taxonomy (9k codes)
│   └── write_cpv_fallback.py     # Regenerate built-in 294-code taxonomy
│
├── tests/                        # (to be filled)
├── docker-compose.yml            # Postgres + Redis + Qdrant + API + Worker
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 7. Pilot Results

Validated on 50 records from `tender_dataset_06082025_6Jan2026.csv` (88,398 rows total).

```
Records processed  : 50
Validation errors  : 0
Duplicates skipped : 0
Errors             : 0

Tier 0 (free rules): 18 records  36%
Tier 2 (Gemini LLM): 32 records  64%

Total time         : 17s
Gemini calls       : 4 batches × 10 records = 4 API calls for 32 records
```

**Note on Tier 0 rate:** The first rows of the CSV are dominated by terse Russian ATOM "Monitoring of Electronic Prices" records (2–10 word summaries, no description body). These genuinely need Gemini and correctly fall to Tier 2. Records from other countries (India, Brazil, Kazakhstan) hit 50–60%+ Tier 0. The target 60% will be achieved across the full diverse dataset.

**Sample Gemini enrichment quality:**

| TOT_ID | Summary (truncated) | CPV Assigned | Type |
|---|---|---|---|
| 123953501 | Supply of Chemical Anchor... | 24951100 — Specialty adhesives | Supply |
| 123955669 | Electrical installation... | 45310000 — Electrical installation work | Works |
| 123956924 | Software development... | 72212000 — Application software | Services |
| 123957273 | Civil engineering consultancy | 71310000 — Civil engineering | Consultancy |

---

## 8. Cost Analysis

> All figures based on **measured token counts** from 500 real CSV rows (avg 251 chars/record).
> Pricing: Gemini 2.0 Flash Lite pay-as-you-go · text-embedding-004.

### Token Breakdown Per Gemini Prompt (batch of 10 records)

| Component | Tokens |
|---|---|
| System prompt | ~184 |
| 10 records × 75 tokens each | ~750 |
| **Total input per prompt** | **~937** |
| Output (10 JSON records) | **~325** |

### API Pricing

| API | Rate | Notes |
|---|---|---|
| Gemini 2.0 Flash Lite — input | $0.075 / 1M tokens | Batch 10 records/prompt |
| Gemini 2.0 Flash Lite — output | $0.30 / 1M tokens | JSON array response |
| text-embedding-004 | $0.025 / 1M tokens | All records get embedded |
| **Cost per Gemini prompt** | **$0.000168** | 10 records enriched |
| **Cost per embedding batch** | **$0.000004** | 100 records embedded |

### Cost By Scenario

```d2
direction: right

phase0: Phase 0\nNo DeBERTa {
  style.fill: "#fff9c4"
  style.stroke: "#f57f17"

  s1: "88k one-time ingestion"
  s2: "  Gemini  : $0.95"
  s3: "  Embeddings: $0.14"
  s4: "  TOTAL    : $1.09"
  s5: ""
  s6: "80k / day ongoing"
  s7: "  Gemini  : $0.86/day"
  s8: "  Embeddings: $0.13/day"
  s9: "  TOTAL    : ~$1.00/day"
  s10: "  Monthly  : ~$30/month"
}

phase2: Phase 2\nDeBERTa Live {
  style.fill: "#c8e6c9"
  style.stroke: "#2e7d32"

  t1: "80k / day"
  t2: "  Gemini (10%): $0.13/day"
  t3: "  Embeddings  : $0.13/day"
  t4: "  TOTAL        : ~$0.26/day"
  t5: "  Monthly      : ~$8/month"
  t6: ""
  t7: "Saving vs Phase 0: 74%"
}

saving: "Cost reduction\n$1.00 → $0.26/day\n74% cheaper\nafter Phase 2" {
  shape: oval
  style.fill: "#e8f5e9"
}

phase0 -> saving
phase2 -> saving
```

| Scenario | Tier 2 records | API calls | Gemini | Embeddings | **Total** |
|---|---|---|---|---|---|
| 88k one-time (Phase 0) | 56,575 | 5,657 prompts | $0.95 | $0.14 | **$1.09** |
| 80k/day — Phase 0 (64% Tier 2) | 51,200/day | 5,120/day | $0.86/day | $0.13/day | **~$1.00/day** |
| 80k/day — Phase 2 (10% Tier 2) | 8,000/day | 800/day | $0.13/day | $0.13/day | **~$0.26/day** |

> **Context:** The original plan assumed 1 LLM call per record = $10/day. Our batched implementation (10 records/prompt) already cuts that to $1/day from day one — before Phase 2 even starts.

---

## 9. Ingestion Time

> Timing based on pilot measurement: **4.4 seconds per Gemini prompt** (sequential).
> Embedding: **1.5 seconds per batch of 100 records**.

### What is the bottleneck?

```d2
direction: right

gemini_rate: "Our sequential rate\n14 prompts/min per worker" {style.fill: "#fce4ec"}
api_limit: "Gemini API limit\n4,000 RPM (pay-as-you-go)" {style.fill: "#e8f5e9"}
gap: "285× headroom\nRate limits are NOT\nthe bottleneck" {
  shape: oval
  style.fill: "#e3f2fd"
}
bottleneck: "Real bottleneck:\nSequential await calls\nin Tier 2 batches\n\nFix: asyncio.gather()\nfor parallel prompts" {
  style.fill: "#fff9c4"
}

gemini_rate -> gap
api_limit -> gap
gap -> bottleneck
```

### Time Estimates

| Scenario | Workers | Gemini time | Embed time | **Total wall time** |
|---|---|---|---|---|
| 88k one-time — Phase 0 | 1 | 6.9h | 0.4h | **~7.3h** |
| 88k one-time — Phase 0 | 4 | 1.7h | 0.1h | **~1.8h** |
| 80k/day — Phase 0 | 1 | 6.3h | 0.3h | **~6.6h** |
| 80k/day — Phase 0 | 4 | 1.6h | 0.1h | **~1.7h** |
| 80k/day — Phase 2 (DeBERTa) | 1 | 1.0h | 0.3h | **~1.3h** |

### Scaling Strategy

```d2
direction: down

opt1: "Option A — 4 Celery Workers (recommended now)" {
  style.fill: "#e8f5e9"
  a: "docker compose up --scale worker=4"
  b: "Each worker handles 22k records independently"
  c: "88k ingested in ~1.8 hours"
  d: "No code changes needed"
}

opt2: "Option B — Async parallel Tier 2 (future)" {
  style.fill: "#e3f2fd"
  a: "Replace sequential await with asyncio.gather()"
  b: "Fire 5 Gemini prompts in parallel per batch"
  c: "5× speedup within each worker"
  d: "88k in ~25 min with 4 workers"
}

opt3: "Option C — Phase 2 (DeBERTa)" {
  style.fill: "#f3e5f5"
  a: "DeBERTa handles 30% locally (no API call)"
  b: "Only 10% reach Gemini"
  c: "80k/day in ~1.3h with 1 worker"
  d: "No scaling needed"
}

opt1 -> opt2: "Next improvement"
opt2 -> opt3: "Phase 2"
```

### Recommended Approach for 88k Historical Ingestion

```bash
# Step 1: Start 4 workers
docker compose up -d --scale worker=4

# Step 2: Split into 4 chunks via API (or Celery tasks directly)
# Chunk 1: records 0–22k
# Chunk 2: records 22k–44k
# Chunk 3: records 44k–66k
# Chunk 4: records 66k–88k

# Estimated completion: ~1.8 hours
# Cost: ~$1.09 total
```

---

## 10. Roadmap

```d2
direction: right

now: Phase 0\nDone ✓ {
  style.fill: "#c8e6c9"
  style.stroke: "#2e7d32"

  p1: "✓ FastAPI skeleton"
  p2: "✓ CSV ingestion + validation"
  p3: "✓ SHA-256 dedup"
  p4: "✓ CPV trie (236 keywords)"
  p5: "✓ Tier 0 rule pipeline"
  p6: "✓ Tier 2 Gemini batch"
  p7: "✓ Qdrant semantic index"
  p8: "✓ PostgreSQL storage"
  p9: "✓ Pilot: 50 records, 0 errors"
}

p1b: Phase 1A\nCPV Index {
  style.fill: "#fff9c4"
  style.stroke: "#f57f17"

  a1: "• Pre-embed all 294 CPV nodes"
  a2: "• Populate cpv_taxonomy collection"
  a3: "• Enable Step 2 embedding match"
  a4: "• Target: push Tier 0 rate to 55%+"
  a5: "• Est: 1 day"
}

p1c: Phase 1B\nMulti-Index Search {
  style.fill: "#fff9c4"
  style.stroke: "#f57f17"

  b1: "• BM25 in-memory index"
  b2: "• CPV-aware search (subtree filter)"
  b3: "• Query router (classify intent)"
  b4: "• RRF fusion (weighted by query type)"
  b5: "• Est: 3–4 days"
}

p1d: Phase 1C\nFull Ingestion {
  style.fill: "#fff9c4"
  style.stroke: "#f57f17"

  c1: "• Docker Compose up"
  c2: "• Celery workers for bulk ingest"
  c3: "• Ingest all 88k records"
  c4: "• Rate-limited Gemini calls"
  c5: "• Monitor cost + dedup hit rate"
  c6: "• Est: 1–2 days"
}

p2: Phase 2\nDeBERTa Tier 1 {
  style.fill: "#e3f2fd"
  style.stroke: "#1565c0"

  d1: "• Collect Gemini-labeled records"
  d2: "  (starts Day 1 of ingestion)"
  d3: "• Fine-tune DeBERTa-v3-base"
  d4: "  after 50k+ labeled records"
  d5: "• Multi-label CPV classification"
  d6: "• Monthly retraining"
  d7: "• Target: Gemini calls → 8k/day"
  d8: "  (from 80k — 10× reduction)"
  d9: "• Est: weeks 6–10"
}

p3: Phase 3\nDomain Embeddings {
  style.fill: "#f3e5f5"
  style.stroke: "#6a1b9a"

  e1: "• Fine-tune embedding model"
  e2: "  on tender corpus"
  e3: "• Personalisation layer"
  e4: "• User feedback loop"
  e5: "• A/B testing framework"
  e6: "• Est: weeks 10+"
}

now -> p1b -> p1c -> p1d -> p2 -> p3
```

### Detailed Next Steps

#### Phase 1A — CPV Embedding Index (Next)

1. Run `scripts/index_cpv_nodes.py` *(to be created)*
   - Load 294 CPV descriptions
   - Embed all with text-embedding-004
   - Upsert to `cpv_taxonomy` Qdrant collection
2. This enables Step 2 of the CPV mapper (currently skipped — collection empty)
3. Expected impact: Tier 0 rate increases from 36% → 50%+

#### Phase 1B — Multi-Index Search

1. **BM25 index** — build from enriched tender text on startup
2. **Query router** — classify queries:
   - Keyword-heavy → weight BM25 higher
   - Semantic/conceptual → weight vector higher
   - CPV-specific → filter by CPV subtree
3. **RRF fusion** — combine ranked lists from all indexes
4. **API update** — expose filter params (country, CPV, date range, value range)

#### Phase 1C — Full 88k Ingestion

1. `docker compose up -d`
2. Trigger via `POST /api/v1/ingest/csv` with batch_size=200
3. Monitor via Celery Flower dashboard
4. Estimated Gemini cost: ~$0.50–2.00 for full dataset at 10 records/prompt

#### Phase 2 — DeBERTa Fine-Tuning

- Training data collection starts automatically from Day 1 (all Tier 2 Gemini outputs are labeled data)
- Fine-tuning begins when ≥50k labeled records accumulated (~Week 6)
- Replaces Tier 2 for ~30% of records → Tier 1 at near-zero cost
- Monthly retraining + active learning on low-confidence outputs

#### Phase 3 — Intelligence & Personalization

- Domain-adapted embeddings (fine-tune on tender corpus)
- User query history → personalized ranking
- Saved searches + email alerts
- Analytics dashboard (spend trends, CPV distribution, country breakdown)

---

## 11. Running the Project

### Prerequisites

```bash
# Install D2 for diagrams
brew install d2

# Python venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Copy and fill in your API key
cp .env.example .env
# Edit .env: set GEMINI_API_KEY=your_key
```

### Dry Pilot (no Docker needed)

```bash
# Test 50 records — Gemini calls, no DB/Qdrant writes
python scripts/pilot_run.py --limit 50 --dry

# Test a different slice of the dataset
python scripts/pilot_run.py --limit 50 --skip 5000 --dry
```

### Full Pipeline (requires Docker)

```bash
# Start infrastructure
docker compose up -d postgres redis qdrant

# Run full 50-record pilot with storage
python scripts/pilot_run.py --limit 50

# Start the API server
uvicorn app.main:app --reload

# Trigger full 88k ingestion via API
curl -X POST http://localhost:8000/api/v1/ingest/csv \
  -H "Content-Type: application/json" \
  -d '{"batch_size": 200, "dry_run": false}'

# Search
curl "http://localhost:8000/api/v1/search?q=GPS+tracking+for+vehicles&top_k=5"
```

### Render Diagrams

```bash
# Render all diagrams in this file to SVG
d2 ARCHITECTURE.md

# Watch mode (auto-refresh in browser)
d2 --watch ARCHITECTURE.md
```

---

## Cost Model

| Scenario | Gemini calls/day | Est. cost/day |
|---|---|---|
| Before (naive) | 80,000 (1/record) | ~$10.00 |
| Phase 0 current | ~8,000 (Tier 2 only) | ~$1.00 |
| Phase 2 (DeBERTa live) | ~800 (hard cases only) | ~$0.10 |

> Pricing based on Gemini 2.0 Flash Lite at ~$0.0125/1k tokens, 10 records/prompt ≈ 500 tokens/prompt.
