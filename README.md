Tender Enrichment & Search Pipeline using Gemini AI & ChromaDB.
# 🛡️ TenderScout AI

A high-precision, AI-powered Tender Search Engine designed to solve the "needle in a haystack" problem. It uses **Multimodal Enrichment (Gemini 2.5)** and **Vector Search (ChromaDB)** to deliver accurate, context-aware results.

---

## 🏗️ System Architecture

The pipeline consists of four main stages: **Cleaning**, **Enrichment**, **Indexing**, and **Search**.

```mermaid
graph TD
    A[Raw CSV Data] -->|Cleaning & Normalizing| B(Cleaner Module)
    B -->|Cleaned Text| C{AI Enrichment Agent}
    C -->|Gemini 2.5 Flash| D[Enriched Metadata]
    D -->|Title Correction & Tagging| D
    D -->|Entity Extraction| E[JSONL Records]
    E -->|Vector Embeddings| F(ChromaDB Vector Store)
    
    U[User Query] -->|Intent Analysis| G{Search Engine}
    G -->|Vector Search| F
    F -->|Ranked Results| H[FastAPI Backend]
    H -->|JSON Response| I[Frontend UI]
```

---

## 🚀 Key Features

### 1. **Semantic Search (Beyond Keywords)**
   - Understands intent (e.g., "Hospital Construction" vs. "Medical Supply").
   - Powered by `text-embedding-004` (768 dimensions).
   - "Relatedness Map": Automatically expands queries (e.g., "Road" -> "Highway, Asphalt").

### 2. **Rich Metadata Display**
   - Displays **Authority**, **Location**, **Closing Date**, and **TOT_ID**.
   - **Clickable Titles** linking to original documents.
   - **Match Score**: Visual indicator of relevance based on vector distance.

### 3. **Smart Filters**
   - **Corrigendum Toggle**: Easily include or exclude amendments/updates.
   - **Entity Extraction**: Automatically identifies State, City, and Authority from unstructured text.

### 4. **Resilient Data Pipeline**
   - **Title Auto-Correction**: Fixes typos in source data (e.g., "Constraction" -> "Construction").
   - **Metadata Fallback**: Uses raw CSV data if AI extraction returns "Unknown".

---

## Match Score Logic

The percentage score displayed in the UI is a **heuristic representation** of the vector distance between your query and the tender document.

1.  **Metric**: We use **Distance** in high-dimensional space (768 dimensions).
2.  **Calculation**: `Score = MAX(0, 1 - Distance)`
3.  **Interpretation**:
    - **100%**: Exact semantic match (rare in vector search).
    - **~40-60%**: Strong conceptual match (e.g., "Hospital" matches "Clinic").
    - **~30%**: Related context (useful for broad discovery).

> **Note**: In vector search, even a 35% score often indicates a highly relevant result compared to keyword matching.

---

## Tech Stack

- **AI Model**: Google Gemini 2.5 Flash Lite (Enrichment & Intent)
- **Embeddings**: Google `text-embedding-004`
- **Vector DB**: ChromaDB (Local Persistent)
- **Backend**: Python FastAPI (`src/api.py`)
- **Frontend**: Vanilla HTML/JS/CSS (`src/ui/index.html`)
- **Orchestration**: Python Scripts (`src/enrichment/processor.py`)

---

## Getting Started

### Prerequisites
- Python 3.10+
- Google Cloud API Key (Gemini)

### Installation

1.  **Clone the Repo**
    ```bash
    git clone https://github.com/sri1991/tendersontime.git
    cd tendersontime
    ```

2.  **Install Dependencies**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configure Environment**
    Create a `.env` file:
    ```bash
    GEMINI_API_KEY="your_api_key_here"
    ```

### Running the App

1.  **Start the Server**
    ```bash
    venv/bin/python -m uvicorn src.api:app --reload --port 8000
    ```
2.  **Open in Browser**
    Go to `http://localhost:8000`

---

## 📦 Data Pipeline Usage

To ingest new data:

1.  **Run Enrichment (Batch Processing)**
    ```bash
    python src/enrichment/processor.py input.csv output.jsonl --limit 1000
    ```

2.  **Index into ChromaDB**
    ```bash
    python src/indexing/chroma_loader.py output.jsonl
    ```

3.  **Full Automation**
    Use `src/ingest_full.py` to process large datasets in chunks.

---

## 🤝 Contributing
1.  Fork the repo.
2.  Create a feature branch (`git checkout -b feature/amazing`).
3.  Commit changes (`git commit -m 'Add amazing feature'`).
4.  Push to branch (`git push origin feature/amazing`).
5.  Open a Pull Request.

---
*Built with ❤️ by TenderScout Team*
