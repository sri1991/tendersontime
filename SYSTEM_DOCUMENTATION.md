# Tender Search System Documentation

## 1. System Overview
The Tender Search System is a semantic search engine built on **ChromaDB** and **Google Gemini**. It ingests tender data from CSV files, generates vector embeddings, and provides a smart search interface that understands user intent.

### Core Components
- **Ingestion Pipeline**: `src/ingestion/pipeline.py` & `src/indexing/chroma_loader.py`
    - Reads CSV data.
    - Enriches content using Gemini (Summarization, Keywords).
    - Generates Embeddings (Title + Description + Tags).
    - Upserts to ChromaDB.
- **Search Engine**: `src/search/engine.py`
    - Analyzes query intent (Category, Location, Procurement Type).
    - Constructs Metadata Filters (`where` clause).
    - Performs Vector Search (Cosine Distance).
    - Reranks/Refines results.

---

## 2. Ingestion Logic
### Data Flow
1.  **Load**: Reads `tender_dataset_06082025_6Jan2026.csv`.
2.  **Enrich**: `TenderEnricher` uses Gemini to extract:
    - `signal_summary`: A concise 1-sentence summary.
    - `search_keywords`: High-value terms for retrieval.
    - `project_tags`: Categorization tags.
3.  **Embed**: `ChromaLoader` constructs the **Embedding Text**:
    ```python
    embedding_text = f"{title}. {signal_summary}. {description[:1000]}. Tags: {tags}"
    ```
    > **Critical Update (Phase 3 Fix)**: Previously, `description` was omitted, causing "Description Loss" where items with generic titles (e.g., "Procurement of Item") were unsearchable. The current logic **includes the full description**.

---

## 3. Search Logic
### `SmartSearchEngine.search(query)`
1.  **Intent Analysis (Gemini)**:
    - Determines `core_domains` (e.g., "Agriculture", "Technology").
    - Determines `procurement_types` (e.g., "Supply", "Works").
    - Flags `is_broad_query` (True/False).
2.  **Filter Construction**:
    - If `is_broad_query` is False, applies strict filters:
        ```json
        { "core_domain": { "$in": ["Agriculture"] } }
        ```
    - If `is_broad_query` is True, **removes filters** to allow wider discovery.
3.  **Vector Retrieval**:
    - Queries ChromaDB using the `embedding-001` model.
    - Returns Top-K results based on Cosine Distance.

---

## 4. Root Cause Analysis (RCA) & Debugging
If a Tender ID is "missing" from search results, follow this RCA workflow using the provided scripts.

### Step 1: Check Source Existence
**Script**: `grep` or `inspect_excel.py`
**Goal**: Is the ID in the source CSV?
- **Issue**: ID not found.
- **Fix**: Check source data provider.
- **Issue**: ID found but line number < `START_OFFSET` (e.g., 85,000).
- **Fix**: The main ingestion pipeline skips early records. Use `scripts/ingest_missing_ids.py` to force-ingest specific IDs.

### Step 2: Check Index Existence
**Script**: `scripts/analyze_persistence.py` or `scripts/debug_chroma_raw.py`
**Goal**: Is the ID in ChromaDB?
- **Command**: `collection.get(ids=["<ID>"])`
- **Issue**: ID returns `None` / Empty.
- **Fix**: Run `scripts/ingest_missing_ids.py` with the target ID.

### Step 3: Check Metadata & Filters
**Script**: `scripts/analyze_category_rca.py`
**Goal**: Is the item classified correctly?
- **Issue**: Item is "Unclassified" but search query filters for "Agriculture".
- **Fix**: Update the item's metadata using `scripts/fix_domain.py`.

### Step 4: Check Ranking (Vector Distance)
**Script**: `scripts/check_specific_rank.py`
**Goal**: Does the item rank high for *specific* queries?
- **Observation**: Item ranks #500 for "Agricultural Machines" but #1 for "Combine Harvester".
- **Conclusion**: This is **Expected Behavior**. Generic queries prioritize items that match *multiple* high-level terms. Specific queries should retrieve the exact item.

---

## 5. Maintenance Scripts
| Script | Purpose |
| :--- | :--- |
| `scripts/ingest_missing_ids.py` | Force-ingest a list of IDs that were skipped or need updating. |
| `scripts/analyze_persistence.py` | Deep-dive analysis of specific IDs (Index status, Vector Distance). |
| `scripts/fix_domain.py` | Manually patch the `core_domain` of an indexed item. |
| `scripts/check_specific_rank.py` | Verify that specific items rank highly for their exact product names. |
