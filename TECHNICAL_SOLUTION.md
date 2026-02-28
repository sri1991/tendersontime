# 🏗️ Technical Solution: TenderScout AI

## Overview

TenderScout AI is a two-pipeline system:
1. **Ingestion Pipeline** — Processes raw tender data daily, enriches it using LLMs, and indexes it into a vector database.
2. **Search Pipeline** — Accepts natural language queries and returns ranked, re-ranked results using a 3-stage retrieval approach.

---

## System Architecture

```d2
direction: right

# --- Ingestion Pipeline ---
ingestion: "Ingestion Pipeline" {
  shape: rectangle
  style.fill: "#f0f4ff"

  csv: "Raw CSV / Excel\n(80k records/day)" { shape: document }
  
  filter: "1. Pre-Filter\n(Length + Keyword check)" { shape: diamond }
  
  cache_check: "2. Semantic Cache\n(SHA-256 Hash Lookup)" { shape: diamond }
  
  gemini_batch: "3. Batch LLM Enrichment\n(Gemini 2.5 Flash Lite)\n10 tenders per prompt" {
    shape: rectangle
    style.fill: "#e8f5e9"
  }
  
  validate: "4. Schema Validation\n(CoreDomain + ProcurementType Enum)" { shape: rectangle }
  
  cache_store: "Local JSON Cache\n(data/enrichment_cache/cache.json)" { shape: cylinder }
  
  embed: "5. Embedding\n(gemini-embedding-001\n768-dim vectors)" { shape: rectangle }
  
  chromadb: "ChromaDB\nVector Store\n(85k+ records)" {
    shape: cylinder
    style.fill: "#fff3e0"
  }

  csv -> filter
  filter -> cache_check: "Passes filter"
  filter -> csv: "Skip" { style.stroke-dash: 4 }
  cache_check -> cache_store: "Cache HIT →\nReturn cached result" { style.stroke-dash: 4 }
  cache_check -> gemini_batch: "Cache MISS"
  gemini_batch -> validate
  validate -> cache_store: "Store result"
  validate -> embed
  embed -> chromadb
}

# --- Search Pipeline ---
search: "Search Pipeline" {
  shape: rectangle
  style.fill: "#fff8f0"

  query: "User Query\n(Natural Language)" { shape: oval }
  
  intent: "1. Intent Analysis\n(Gemini 2.5 Flash Lite)\nExtract: Domain, Type, Keywords" { shape: rectangle }
  
  hybrid: "2. Hybrid Retrieval" {
    shape: rectangle
    style.fill: "#e3f2fd"
    
    vector_search: "Vector Search\n(ChromaDB Cosine Similarity\nTop-60 with domain filter)" { shape: rectangle }
    bm25: "BM25 Keyword Search\n(In-memory index\nTop-60 exact matches)" { shape: rectangle }
    rrf: "Reciprocal Rank Fusion\n(Combine + deduplicate)" { shape: diamond }
    
    vector_search -> rrf
    bm25 -> rrf
  }
  
  rerank: "3. Two-Stage Re-ranking\n(Gemini 2.0 Flash)\nRe-orders top 50 by query intent" { shape: rectangle }
  
  score: "4. Scoring\n(Real cosine distance\n→ relevance %)" { shape: rectangle }
  
  filter_zero: "5. Filter 0-score results\n(Remove irrelevant)" { shape: diamond }
  
  api: "FastAPI Backend\n(src/api.py)" { shape: rectangle }
  ui: "Frontend UI\n(HTML + JS)" { shape: rectangle }

  query -> intent
  intent -> hybrid
  hybrid -> rerank: "Top 60 candidates"
  rerank -> score: "Top 20 re-ranked"
  score -> filter_zero
  filter_zero -> api: "Relevant only"
  api -> ui
}

# Cross-pipeline connections
ingestion.chromadb -> search.hybrid.vector_search: "Read vectors"
ingestion.cache_store -> search.hybrid.bm25: "Documents for\nBM25 index"
```

---

## Pipeline 1: Ingestion

### Stage 1 — Pre-Filter
Before calling any LLM, we eliminate low-value records:
- Records with description < 20 characters are discarded
- Records with no keyword match against our 200+ term taxonomy are skipped
- **Impact**: Saves ~15-20% of LLM calls at zero cost

### Stage 2 — Semantic Cache (SHA-256 Hash)
```
hash = SHA256(title + "\n" + description)
if hash in cache.json → return cached enrichment (FREE)
else → call Gemini API
```
- Cache persists to `data/enrichment_cache/cache.json`
- Hit rate grows over time as recurring tenders are seen again
- **Impact**: Eliminates ~30-40% of API calls on repeat ingestion

### Stage 3 — Batch LLM Enrichment
Instead of one API call per tender, we batch **10 tenders into a single prompt**:
```
Extract enrichment for all 10 tenders below. Return a JSON array...
[tender 1], [tender 2], ... [tender 10]
```
- Model: `gemini-2.5-flash-lite` (cheapest Gemini model)
- **Impact**: ~10x fewer API calls vs. single-item processing

### Stage 4 — Schema Validation
All LLM outputs are validated against fixed enums before storage:
```python
CoreDomain: [Agriculture, Healthcare, Infrastructure, Energy, Defense, Technology, Transport, Other]
ProcurementType: [Works, Supply, Services, Unknown]
```
Hallucinated values (e.g. `"Unclassified"`, `"N/A"`) are automatically corrected to `"Other"` / `"Unknown"`.

### Stage 5 — Embedding & Indexing
Each enriched tender is converted to a **768-dimensional vector** using `gemini-embedding-001`. The embedding text is:
```
{title}. {signal_summary}. {description[:1000]}. Tags: {tags}. Keywords: {keywords}
```
Stored in ChromaDB with rich metadata for filtering.

---

## Pipeline 2: Search

### Stage 1 — Intent Analysis
The query is parsed by Gemini to extract:
- `core_domains`: Which domain(s) to filter on (e.g. `["Technology"]`)
- `refined_query`: Cleaned-up version of the query
- `is_broad_query`: If true, no domain filter is applied (e.g. query = "all tenders")

### Stage 2 — Hybrid Retrieval (Vector + BM25)
Two parallel searches run simultaneously:

| Method | Strength | Weakness |
|---|---|---|
| **Vector Search** | Finds semantically similar results | Can miss exact ID/keyword matches |
| **BM25 (Keyword)** | Finds exact keyword or part-number matches | Misses synonyms, context |

Results are fused using **Reciprocal Rank Fusion (RRF)**:
```
score(doc) = 1/(k + rank_vector) + 1/(k + rank_bm25)   [k=60]
```
This rewards documents that appear high in **either** list.

### Stage 3 — Two-Stage Re-ranking
The top 50 fused candidates are sent to `gemini-2.0-flash`, which re-orders them based on true relevance to the original query:
- Direct keyword matches (e.g. "CCTV", "RFID") are promoted
- Loosely related results (e.g. "Locksmith Work" for a CCTV query) are demoted or dropped
- The re-ranker returns only indices it considers relevant

### Stage 4 — Scoring
Real cosine distances from ChromaDB are preserved throughout the pipeline and converted to a human-readable match score:

| Distance | Score | Label |
|---|---|---|
| ≤ 0.50 | 100% | Excellent Match |
| 0.50 – 0.70 | 85–100% | Excellent Match |
| 0.70 – 0.90 | 55–85% | Strong Match |
| 0.90 – 1.10 | 25–55% | Good Match |
| 1.10 – 1.30 | 0–25% | Potential Lead |
| > 1.30 | **Filtered out** | Not shown |

---

## Cost & Performance Summary

| Metric | Value |
|---|---|
| **Records/day** | 80,000 |
| **Ingestion Time** | ~2.5 hours (4 batches) |
| **Daily Enrichment Cost** | ~$10–12 (with caching + batching) |
| **Search Latency** | < 3 seconds end-to-end |
| **Vector Store Size** | 85,000+ records, 768-dim |
| **Re-ranking Model** | `gemini-2.0-flash` (fast, cheap) |

---

## Key Design Decisions

### Why Hybrid Search?
Pure vector search is great for semantic similarity but misses exact matches. A user searching for `"Tender #24005038"` or `"RFID tag"` needs keyword precision — that's what BM25 provides. RRF combines both without requiring manual weight tuning.

### Why Two-Stage Re-ranking?
The initial hybrid retrieval casts a wide net (top 60). Re-ranking narrows it down to a highly precise top 10-20 using a bigger LLM that understands the query's intent more deeply. The cost is low (one API call per search) but the precision improvement is significant.

### Why SHA-256 Caching?
Government tenders are frequently re-published with minor amendments. Hashing the title + description catches these duplicates and avoids redundant LLM calls, cutting costs significantly over time.

### Why Schema Validation?
LLMs occasionally hallucinate field values (e.g., `core_domain = "Unclassified"`). Enforcing enums at ingestion time ensures that ChromaDB metadata filters always work correctly during search.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **LLM (Enrichment)** | Google Gemini 2.5 Flash Lite |
| **LLM (Intent + Re-rank)** | Google Gemini 2.0 Flash |
| **Embeddings** | `gemini-embedding-001` (768 dims) |
| **Keyword Search** | `rank_bm25` (BM25Okapi) |
| **Vector DB** | ChromaDB (HTTP Server mode) |
| **Backend** | Python FastAPI |
| **Frontend** | Vanilla HTML / JS / CSS |
| **Deployment** | Linux VM + Docker Compose |
