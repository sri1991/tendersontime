#!/usr/bin/env python3
"""
Benchmark TenderScout AI vs SOLR.

For each query, fetches top-N results from both SOLR and our API, then:
  - Lists COMMON tenders (found by both)
  - Lists OUR EXCLUSIVE tenders (we found, SOLR did not)
  - Lists SOLR EXCLUSIVE tenders, split into:
      * NOT IN DB  — we never ingested this tender (data gap, ignorable)
      * IN DB, NOT RANKED — we have it but didn't surface it (ranking gap, actionable)

Saves a timestamped HTML report to tests/reports/.

Usage:
    # Single ad-hoc query
    python scripts/benchmark_vs_solr.py --query "hospital equipment"

    # Full suite from tests/queries.json
    python scripts/benchmark_vs_solr.py

    # Custom queries file, custom limit
    python scripts/benchmark_vs_solr.py --queries-file tests/queries.json --limit 500

    # Override date range
    python scripts/benchmark_vs_solr.py --date-from 2025-08-06 --date-to 2025-08-06
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Optional

import asyncpg
import requests

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SOLR_URL      = "https://www.tendersontime.org/projects/TOT-CRM/ChaHPCM2021/aicompare-srinivas.php"
DEFAULT_API   = "http://localhost:8080"
DEFAULT_LIMIT = 500
DEFAULT_DATE  = "2025-08-06"
QUERIES_FILE  = Path(__file__).parent.parent / "tests" / "queries.json"
REPORTS_DIR   = Path(__file__).parent.parent / "tests" / "reports"

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tender:tender@localhost:5433/tenderscout"
).replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


# ---------------------------------------------------------------------------
# SOLR HTML parser
# ---------------------------------------------------------------------------

class SolrTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_td   = False
        self._col     = 0
        self._current = []
        self._rows: list[tuple[str, str]] = []
        self._skip    = False
        self._buf     = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "tr":
            cls = attr_dict.get("class", "")
            self._skip   = "details-row" in cls or "header" in cls
            self._current = []
            self._col     = 0
        if tag == "td" and not self._skip:
            self._in_td = True
            self._buf   = ""

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td and not self._skip:
            self._in_td = False
            text = re.sub(r"<[^>]+>", "", self._buf).strip()
            if self._col < 2:
                self._current.append(text)
            self._col += 1
        if tag == "tr" and not self._skip and len(self._current) == 2:
            tid, title = self._current
            if re.match(r"^\d+$", tid):
                self._rows.append((tid, title))
            self._current = []

    def handle_data(self, data):
        if self._in_td and not self._skip:
            self._buf += data

    @property
    def results(self) -> list[dict]:
        return [{"tot_id": r[0], "title": r[1]} for r in self._rows]


def parse_solr_html(html: str) -> list[dict]:
    p = SolrTableParser()
    p.feed(html)
    return p.results


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def call_solr(ekw: str, nkw: str, limit: int, date_from: str, date_to: str) -> tuple[list[dict], float]:
    payload = {
        "AjaxAction":    "solrsearchkw",
        "ekw":           ekw,
        "nkw":           nkw,
        "sector":        "",
        "date_from":     date_from,
        "date_to":       date_to,
        "page":          1,
        "perPage":       limit,
        "search_limit":  limit,
        "score":         0.5,
        "removeduplicate": 0,
        "showduplicate":   0,
    }
    headers = {
        "Content-Type":     "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }
    t0 = time.perf_counter()
    try:
        resp = requests.post(SOLR_URL, data=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        ms   = round((time.perf_counter() - t0) * 1000, 1)
        return parse_solr_html(data.get("solrTable", "")), ms
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        print(f"  [WARN] SOLR error: {e}", file=sys.stderr)
        return [], ms


def call_our_api(query: str, limit: int, api_base: str) -> tuple[list[dict], float]:
    url = f"{api_base.rstrip('/')}/api/search"
    t0  = time.perf_counter()
    try:
        resp = requests.post(url, json={"query": query, "limit": limit}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        ms   = round((time.perf_counter() - t0) * 1000, 1)
        results = [
            {
                "tot_id": str(r["tot_id"]),
                "title":  r.get("title", ""),
                "score":  r.get("score", 0),
            }
            for r in data.get("results", [])
            if r.get("tot_id") and str(r["tot_id"]) not in ("N/A", "None", "")
        ]
        return results, ms
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        print(f"  [WARN] Our API error: {e}", file=sys.stderr)
        return [], ms


# ---------------------------------------------------------------------------
# DB gap check — which SOLR-exclusive IDs exist in our tenders table?
# ---------------------------------------------------------------------------

async def check_ids_in_db(tot_ids: list[str]) -> dict[str, bool]:
    """Returns {tot_id: True/False} — True means it EXISTS in our DB."""
    if not tot_ids:
        return {}
    try:
        conn = await asyncpg.connect(DB_URL)
        rows = await conn.fetch(
            "SELECT tot_id FROM tenders WHERE tot_id = ANY($1::text[])",
            tot_ids,
        )
        await conn.close()
        found = {r["tot_id"] for r in rows}
        return {tid: (tid in found) for tid in tot_ids}
    except Exception as e:
        print(f"  [WARN] DB check failed: {e}", file=sys.stderr)
        return {tid: None for tid in tot_ids}  # None = unknown


def check_ids_in_db_sync(tot_ids: list[str]) -> dict[str, bool]:
    return asyncio.run(check_ids_in_db(tot_ids))


# ---------------------------------------------------------------------------
# Per-query analysis
# ---------------------------------------------------------------------------

def analyse_query(
    label: str,
    ekw: str,
    nkw: str,
    query: str,
    limit: int,
    date_from: str,
    date_to: str,
    api_base: str,
) -> dict:
    print(f"\n  [{label}] Querying SOLR ...", end="", flush=True)
    solr_results, solr_ms = call_solr(ekw, nkw, limit, date_from, date_to)

    print(f" {len(solr_results)} results. Querying our API ...", end="", flush=True)
    our_results, our_ms = call_our_api(query, limit, api_base)
    print(f" {len(our_results)} results.", flush=True)

    solr_ids = {r["tot_id"] for r in solr_results}
    our_ids  = {r["tot_id"] for r in our_results}

    solr_map = {r["tot_id"]: r for r in solr_results}
    our_map  = {r["tot_id"]: r for r in our_results}

    common_ids       = solr_ids & our_ids
    our_exclusive    = our_ids  - solr_ids
    solr_exclusive   = solr_ids - our_ids

    # Check which SOLR-exclusive IDs exist in our DB
    print(f"  [{label}] Checking {len(solr_exclusive)} SOLR-exclusive IDs against DB ...", end="", flush=True)
    db_presence = check_ids_in_db_sync(list(solr_exclusive))
    in_db_not_ranked = {tid for tid, present in db_presence.items() if present is True}
    not_in_db        = {tid for tid, present in db_presence.items() if present is False}
    db_unknown       = {tid for tid, present in db_presence.items() if present is None}
    print(f" done. In DB not ranked: {len(in_db_not_ranked)} | Not in DB: {len(not_in_db)}", flush=True)

    jaccard          = len(common_ids) / len(solr_ids | our_ids) if (solr_ids | our_ids) else 0.0
    recall_vs_solr   = len(common_ids) / len(solr_ids) if solr_ids else 0.0

    scores = [r["score"] for r in our_results if r.get("score", 0) > 0]
    score_stats = None
    if scores:
        s = sorted(scores)
        score_stats = {
            "min":  round(min(s), 1),
            "max":  round(max(s), 1),
            "mean": round(mean(s), 1),
            "p50":  round(s[len(s) // 2], 1),
            "p90":  round(s[int(len(s) * 0.9)], 1),
        }

    # Build enriched result lists for the report
    def enrich(ids, source_map, extra_map=None):
        out = []
        for tid in sorted(ids, key=lambda t: list(source_map.keys()).index(t) if t in source_map else 999):
            entry = {"tot_id": tid, "title": source_map.get(tid, {}).get("title", "—")}
            if extra_map and tid in extra_map:
                entry["score"] = extra_map[tid].get("score", "")
            out.append(entry)
        return out

    return {
        "label":          label,
        "ekw":            ekw,
        "nkw":            nkw,
        "query":          query,
        "solr_count":     len(solr_results),
        "our_count":      len(our_results),
        "common_count":   len(common_ids),
        "our_exclusive_count":  len(our_exclusive),
        "solr_exclusive_count": len(solr_exclusive),
        "in_db_not_ranked_count": len(in_db_not_ranked),
        "not_in_db_count":        len(not_in_db),
        "jaccard":          round(jaccard, 3),
        "recall_vs_solr":   round(recall_vs_solr, 3),
        "solr_latency_ms":  solr_ms,
        "our_latency_ms":   our_ms,
        "score_stats":      score_stats,
        "common":           enrich(common_ids, solr_map, our_map),
        "our_exclusive":    enrich(our_exclusive, our_map),
        "in_db_not_ranked": enrich(in_db_not_ranked, solr_map),
        "not_in_db":        enrich(not_in_db, solr_map),
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TenderScout vs SOLR — {timestamp}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; color: #222; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 20px; }}
  .summary-table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px #0001; }}
  .summary-table th, .summary-table td {{ padding: 8px 12px; text-align: right; font-size: 0.85rem; border-bottom: 1px solid #eee; }}
  .summary-table th {{ background: #1a1a2e; color: #fff; text-align: center; }}
  .summary-table td:first-child {{ text-align: left; font-weight: 600; }}
  .summary-table tr:last-child td {{ border-bottom: none; font-weight: 700; background: #f0f4ff; }}
  .query-block {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 4px #0001; margin-bottom: 28px; overflow: hidden; }}
  .query-header {{ background: #1a1a2e; color: #fff; padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; }}
  .query-header h2 {{ margin: 0; font-size: 1rem; }}
  .query-header .chips {{ font-size: 0.78rem; opacity: 0.8; }}
  .stats-row {{ display: flex; gap: 12px; padding: 10px 16px; background: #f8f8ff; border-bottom: 1px solid #eee; flex-wrap: wrap; }}
  .stat {{ background: #fff; border: 1px solid #dde; border-radius: 6px; padding: 6px 12px; font-size: 0.82rem; }}
  .stat b {{ display: block; font-size: 1.1rem; color: #1a1a2e; }}
  .sections {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; }}
  .section {{ padding: 12px 16px; border-right: 1px solid #eee; }}
  .section:last-child {{ border-right: none; }}
  .section h3 {{ margin: 0 0 8px; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .section.common h3   {{ color: #2e7d32; }}
  .section.ours h3     {{ color: #1565c0; }}
  .section.missing h3  {{ color: #c62828; }}
  .section.nodata h3   {{ color: #888; }}
  .tender-list {{ list-style: none; margin: 0; padding: 0; max-height: 300px; overflow-y: auto; }}
  .tender-list li {{ font-size: 0.78rem; padding: 4px 0; border-bottom: 1px solid #f0f0f0; display: flex; gap: 6px; }}
  .tender-list li:last-child {{ border-bottom: none; }}
  .tid {{ color: #888; flex-shrink: 0; }}
  .score {{ color: #1565c0; flex-shrink: 0; font-weight: 600; }}
  .full-sections {{ grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; gap: 0; border-top: 1px solid #eee; }}
</style>
</head>
<body>
<h1>TenderScout AI vs SOLR — Benchmark Report</h1>
<div class="meta">
  Run: {timestamp} &nbsp;|&nbsp;
  Limit: {limit} &nbsp;|&nbsp;
  Date range: {date_from} → {date_to} &nbsp;|&nbsp;
  API: {api_base}
</div>

<h2 style="font-size:1rem; margin-bottom:8px;">Summary</h2>
<table class="summary-table">
<thead>
<tr>
  <th>Query</th>
  <th>SOLR</th><th>Ours</th>
  <th>Common</th>
  <th>Our Extras</th>
  <th>SOLR-only (in DB)</th>
  <th>SOLR-only (not in DB)</th>
  <th>Recall</th><th>Jaccard</th>
  <th>SOLR ms</th><th>Our ms</th>
</tr>
</thead>
<tbody>
{summary_rows}
<tr>
  <td>AVERAGE</td>
  <td>{avg_solr}</td><td>{avg_ours}</td>
  <td>{avg_common}</td>
  <td>{avg_our_ex}</td>
  <td>{avg_in_db}</td>
  <td>{avg_not_db}</td>
  <td>{avg_recall}</td><td>{avg_jaccard}</td>
  <td>{avg_solr_ms}</td><td>{avg_our_ms}</td>
</tr>
</tbody>
</table>

{query_blocks}
</body>
</html>
"""

_SUMMARY_ROW = """\
<tr>
  <td>{label}</td>
  <td>{solr_count}</td><td>{our_count}</td>
  <td style="color:#2e7d32;font-weight:600">{common_count}</td>
  <td style="color:#1565c0">{our_exclusive_count}</td>
  <td style="color:#c62828">{in_db_not_ranked_count}</td>
  <td style="color:#888">{not_in_db_count}</td>
  <td>{recall_vs_solr:.1%}</td><td>{jaccard:.3f}</td>
  <td>{solr_latency_ms:.0f}</td><td>{our_latency_ms:.0f}</td>
</tr>"""

_QUERY_BLOCK = """\
<div class="query-block">
  <div class="query-header">
    <h2>{label}</h2>
    <div class="chips">
      SOLR ekw: <b>{ekw}</b> &nbsp; nkw: <b>{nkw_display}</b> &nbsp;|&nbsp;
      Our query: <b>{query}</b>
    </div>
  </div>
  <div class="stats-row">
    <div class="stat"><b>{solr_count}</b>SOLR results</div>
    <div class="stat"><b>{our_count}</b>Our results</div>
    <div class="stat" style="color:#2e7d32"><b>{common_count}</b>Common</div>
    <div class="stat" style="color:#1565c0"><b>{our_exclusive_count}</b>Our extras</div>
    <div class="stat" style="color:#c62828"><b>{in_db_not_ranked_count}</b>SOLR-only (in DB, not ranked)</div>
    <div class="stat" style="color:#888"><b>{not_in_db_count}</b>SOLR-only (not in DB)</div>
    <div class="stat"><b>{recall_pct}</b>Recall vs SOLR</div>
    <div class="stat"><b>{jaccard}</b>Jaccard</div>
    <div class="stat"><b>{solr_ms}ms</b>SOLR latency</div>
    <div class="stat"><b>{our_ms}ms</b>Our latency</div>
    {score_stat_html}
  </div>
  <div class="sections">
    <div class="section common">
      <h3>Common ({common_count})</h3>
      <ul class="tender-list">{common_items}</ul>
    </div>
    <div class="section ours">
      <h3>Our Extras — not in SOLR ({our_exclusive_count})</h3>
      <ul class="tender-list">{our_exclusive_items}</ul>
    </div>
  </div>
  <div class="full-sections">
    <div class="section missing">
      <h3>SOLR-only: IN DB but not ranked ({in_db_not_ranked_count}) — ranking gap</h3>
      <ul class="tender-list">{in_db_not_ranked_items}</ul>
    </div>
    <div class="section nodata">
      <h3>SOLR-only: NOT IN DB ({not_in_db_count}) — data gap, ignorable</h3>
      <ul class="tender-list">{not_in_db_items}</ul>
    </div>
  </div>
</div>"""


def _render_items(items: list[dict], show_score=False) -> str:
    if not items:
        return "<li style='color:#aaa'>None</li>"
    html = []
    for r in items:
        score = f'<span class="score">{r["score"]}%</span>' if show_score and r.get("score") else ""
        title = r.get("title", "—")[:90]
        html.append(f'<li><span class="tid">{r["tot_id"]}</span>{score} {title}</li>')
    return "\n".join(html)


def _score_stat_html(ss: Optional[dict]) -> str:
    if not ss:
        return ""
    return (
        f'<div class="stat"><b>{ss["mean"]}%</b>Score mean</div>'
        f'<div class="stat"><b>{ss["p50"]}%</b>Score p50</div>'
        f'<div class="stat"><b>{ss["p90"]}%</b>Score p90</div>'
    )


def generate_html(report: dict) -> str:
    cfg     = report["config"]
    queries = report["queries"]

    summary_rows = "\n".join(
        _SUMMARY_ROW.format(**q) for q in queries
    )

    def avg(field):
        vals = [q[field] for q in queries if q[field] is not None]
        return round(mean(vals), 1) if vals else 0

    query_blocks = "\n".join(
        _QUERY_BLOCK.format(
            **q,
            nkw_display=q["nkw"] or "—",
            recall_pct=f'{q["recall_vs_solr"]:.1%}',
            our_ms=int(q["our_latency_ms"]),
            solr_ms=int(q["solr_latency_ms"]),
            score_stat_html=_score_stat_html(q.get("score_stats")),
            common_items=_render_items(q["common"], show_score=True),
            our_exclusive_items=_render_items(q["our_exclusive"], show_score=True),
            in_db_not_ranked_items=_render_items(q["in_db_not_ranked"]),
            not_in_db_items=_render_items(q["not_in_db"]),
        )
        for q in queries
    )

    return _HTML_TEMPLATE.format(
        timestamp=cfg["timestamp"],
        limit=cfg["limit"],
        date_from=cfg["date_from"],
        date_to=cfg["date_to"],
        api_base=cfg["api_base"],
        summary_rows=summary_rows,
        query_blocks=query_blocks,
        avg_solr=f'{avg("solr_count"):.0f}',
        avg_ours=f'{avg("our_count"):.0f}',
        avg_common=f'{avg("common_count"):.0f}',
        avg_our_ex=f'{avg("our_exclusive_count"):.0f}',
        avg_in_db=f'{avg("in_db_not_ranked_count"):.0f}',
        avg_not_db=f'{avg("not_in_db_count"):.0f}',
        avg_recall=f'{mean(q["recall_vs_solr"] for q in queries):.1%}',
        avg_jaccard=f'{mean(q["jaccard"] for q in queries):.3f}',
        avg_solr_ms=f'{avg("solr_latency_ms"):.0f}',
        avg_our_ms=f'{avg("our_latency_ms"):.0f}',
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmark TenderScout AI vs SOLR")
    parser.add_argument("--api",          default=DEFAULT_API,    help=f"Our API base URL (default: {DEFAULT_API})")
    parser.add_argument("--limit",        type=int, default=DEFAULT_LIMIT, help=f"Results per query (default: {DEFAULT_LIMIT})")
    parser.add_argument("--date-from",    default=DEFAULT_DATE,   help=f"SOLR date_from (default: {DEFAULT_DATE})")
    parser.add_argument("--date-to",      default=DEFAULT_DATE,   help=f"SOLR date_to (default: {DEFAULT_DATE})")
    parser.add_argument("--queries-file", default=str(QUERIES_FILE), help=f"Path to queries JSON (default: {QUERIES_FILE})")
    parser.add_argument("--query",        default=None,           help="Run a single ad-hoc query (ekw=query, our query=query)")
    parser.add_argument("--ekw",          default=None,           help="SOLR exact keywords (used with --query)")
    parser.add_argument("--nkw",          default="",             help="SOLR normal keywords (used with --query)")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Build query list
    if args.query:
        queries = [{
            "label": args.query,
            "ekw":   args.ekw or args.query,
            "nkw":   args.nkw,
            "query": args.query,
        }]
    else:
        qfile = Path(args.queries_file)
        if not qfile.exists():
            print(f"Queries file not found: {qfile}", file=sys.stderr)
            sys.exit(1)
        queries = json.loads(qfile.read_text())

    print(f"\nTenderScout AI vs SOLR Benchmark")
    print(f"  API       : {args.api}")
    print(f"  Limit     : {args.limit}")
    print(f"  Date      : {args.date_from} → {args.date_to}")
    print(f"  Queries   : {len(queries)}")
    print()

    results = []
    for q in queries:
        result = analyse_query(
            label     = q["label"],
            ekw       = q["ekw"],
            nkw       = q.get("nkw", ""),
            query     = q.get("query", q["label"]),
            limit     = args.limit,
            date_from = args.date_from,
            date_to   = args.date_to,
            api_base  = args.api,
        )
        results.append(result)

    # Print console summary
    print()
    header = f"{'Query':<28} {'SOLR':>5} {'Ours':>5} {'Cmn':>5} {'OurX':>5} {'InDB':>6} {'NoDB':>6} {'Recall':>7} {'Jac':>6}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['label']:<28} {r['solr_count']:>5} {r['our_count']:>5} "
            f"{r['common_count']:>5} {r['our_exclusive_count']:>5} "
            f"{r['in_db_not_ranked_count']:>6} {r['not_in_db_count']:>6} "
            f"{r['recall_vs_solr']:>7.1%} {r['jaccard']:>6.3f}"
        )

    valid = [r for r in results if r["solr_count"] > 0 or r["our_count"] > 0]
    if valid:
        print("-" * len(header))
        print(
            f"{'AVERAGE':<28} "
            f"{mean(r['solr_count'] for r in valid):>5.0f} "
            f"{mean(r['our_count'] for r in valid):>5.0f} "
            f"{mean(r['common_count'] for r in valid):>5.0f} "
            f"{mean(r['our_exclusive_count'] for r in valid):>5.0f} "
            f"{mean(r['in_db_not_ranked_count'] for r in valid):>6.0f} "
            f"{mean(r['not_in_db_count'] for r in valid):>6.0f} "
            f"{mean(r['recall_vs_solr'] for r in valid):>7.1%} "
            f"{mean(r['jaccard'] for r in valid):>6.3f}"
        )

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "config": {
            "timestamp": timestamp,
            "api_base":  args.api,
            "limit":     args.limit,
            "date_from": args.date_from,
            "date_to":   args.date_to,
        },
        "queries": results,
    }

    json_path = REPORTS_DIR / f"{timestamp}.json"
    html_path = REPORTS_DIR / f"{timestamp}.html"

    json_path.write_text(json.dumps(report, indent=2))
    html_path.write_text(generate_html(report))

    print(f"\n  Report saved:")
    print(f"    HTML → {html_path}")
    print(f"    JSON → {json_path}")
    print(f"\n  Open in browser: open {html_path}\n")


if __name__ == "__main__":
    main()
