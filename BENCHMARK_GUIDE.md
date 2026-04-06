# Benchmark Guide — TenderScout AI vs SOLR

How to run search comparison reports, interpret results, and improve the system.

---

## Prerequisites

Make sure these are running before you start:

**1. Postgres (with tender data)**
```bash
docker start ts_pg
```

**2. Redis**
```bash
docker start tenderscout_redis
```

**3. The API**
```bash
GEMINI_API_KEY=your_gemini_api_key_here \
DATABASE_URL=postgresql+asyncpg://tender:tender@localhost:5433/tenderscout \
REDIS_URL=redis://localhost:6379/0 \
SEARCH_BACKEND=postgres \
venv/bin/uvicorn src.api:app --host 0.0.0.0 --port 8080 2>&1 &
```

Verify everything is healthy:
```bash
curl http://localhost:8080/health
```

Expected response:
```json
{ "status": "ok", "postgres": { "status": "ok", "tender_count": 84291 }, "redis": { "status": "ok" }, "bm25": { "ready": true } }
```

---

## Running a Report

### Single ad-hoc query
Use this when you want to quickly test one specific search term.

```bash
venv/bin/python scripts/benchmark_vs_solr.py \
  --query "hospital equipment" \
  --ekw "hospital equipment" \
  --nkw "medical" \
  --limit 500
```

- `--query` — what gets sent to **our** API
- `--ekw` — exact keywords sent to **SOLR**
- `--nkw` — normal/broad keywords sent to **SOLR** (can be empty)
- `--limit` — how many results to fetch from each system (default: 500)

### Full test suite
Runs all queries defined in `tests/queries.json`.

```bash
venv/bin/python scripts/benchmark_vs_solr.py --limit 500
```

### Custom queries file
```bash
venv/bin/python scripts/benchmark_vs_solr.py \
  --queries-file tests/queries.json \
  --limit 500
```

---

## Editing the Query Suite

Open `tests/queries.json` to add, remove, or tune queries.

Each entry has:
```json
{
  "label":  "hospital equipment",      ← name shown in the report
  "ekw":    "hospital equipment",      ← exact keywords for SOLR
  "nkw":    "medical",                 ← normal/broad keywords for SOLR (or "")
  "query":  "hospital medical equipment supply"  ← query sent to our API
}
```

The `ekw`/`nkw` fields should match what the SOLR system expects on their backend.
The `query` field can be phrased differently to make the most of our semantic search.

---

## Reading the Report

After each run a timestamped report is saved to `tests/reports/`:
- `tests/reports/YYYY-MM-DD_HH-MM-SS.html` — open this in your browser
- `tests/reports/YYYY-MM-DD_HH-MM-SS.json` — raw data for further analysis

Open the latest HTML report:
```bash
open tests/reports/$(ls tests/reports/*.html | sort | tail -1)
```

### What the columns mean

| Column | What it tells you |
|---|---|
| **SOLR** | Number of results SOLR returned |
| **Ours** | Number of results our API returned |
| **Common** | Tenders found by both systems |
| **Our Extras** | Tenders we found that SOLR did not — our semantic advantage |
| **SOLR-only (in DB)** | SOLR found these, we have them in DB but didn't rank them — **ranking gap, actionable** |
| **SOLR-only (not in DB)** | SOLR found these, we don't have them at all — **data gap, ignorable for now** |
| **Recall** | % of SOLR results we also returned — higher is better |
| **Jaccard** | Overlap / Union — overall similarity of result sets |

### The four sections per query

1. **Common** — both systems agree. These are your high-confidence results.
2. **Our Extras** — semantic matches SOLR missed. Review these: are they genuinely relevant?
3. **SOLR-only, in DB** — we have the tender but didn't rank it. This is where to focus improvement.
4. **SOLR-only, not in DB** — missing from our data. Note the IDs and re-ingest if important.

---

## Interpreting Results

### Good signs
- High **Common** count — both systems finding the same relevant tenders
- Large **Our Extras** list with genuinely relevant results — semantic search adding value
- Low **SOLR-only (in DB)** count — our ranking is capturing what SOLR captures

### Warning signs
- Very low **Recall** (< 20%) — we're missing most of what SOLR finds
- Large **SOLR-only (in DB)** count — we have the data but scoring is ranking it too low
- Large **SOLR-only (not in DB)** count — significant data gaps, consider re-ingestion

### Why we might miss tenders that are in the DB

Look at the titles in the "SOLR-only, in DB" section. Common reasons:

1. **Domain filter too narrow** — intent analysis assigned the wrong domain, filtered them out
2. **Embedding mismatch** — the tender's text is too different from the query phrasing
3. **BM25 not catching keywords** — keyword isn't in the BM25 index for that tender
4. **Score too low** — vector similarity is weak, BM25 score is 0, freshness drags it down

---

## Tracking Improvement Over Time

Every report run is saved with a timestamp. As you make changes to the search engine:

1. Run a benchmark **before** making changes → note the recall % and Jaccard scores
2. Make your changes
3. Run the same benchmark again
4. Compare the two HTML reports side by side

The JSON files can also be used to script a diff if needed.

---

## Quick Reference

```bash
# Health check
curl http://localhost:8080/health

# Single query test
venv/bin/python scripts/benchmark_vs_solr.py --query "solar panels" --limit 100

# Full suite at limit 500
venv/bin/python scripts/benchmark_vs_solr.py --limit 500

# Open latest report
open tests/reports/$(ls tests/reports/*.html | sort | tail -1)

# Stop the API
kill $(lsof -ti:8080)

# Stop the DB container
docker stop ts_pg
```
