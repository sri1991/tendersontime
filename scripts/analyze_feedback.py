"""
Feedback Analyser — automated analysis of thumbs up/down signals.

Reads from the `feedback` DB table and produces:
  1. Per-query precision/recall summary
  2. Failure pattern analysis (what do thumbed-down results have in common?)
  3. Score calibration gap (what scores are users thumbing up/down?)
  4. Domain classification health (Unclassified leakage, wrong domains)
  5. Actionable recommendations

Run:
    python scripts/analyze_feedback.py
    python scripts/analyze_feedback.py --min-feedback 3   # only queries with 3+ ratings
    python scripts/analyze_feedback.py --html             # also write HTML report
"""

import asyncio
import json
import os
import sys
import argparse
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.schema import engine


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

async def load_feedback(min_feedback: int = 1):
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT query, position, rating, session_id, meta, created_at
            FROM feedback
            ORDER BY created_at ASC
        """))
        rows = [dict(r) for r in result.mappings().all()]

    # Filter queries with enough feedback
    query_counts = defaultdict(int)
    for r in rows:
        query_counts[r["query"]] += 1
    return [r for r in rows if query_counts[r["query"]] >= min_feedback]


# ─────────────────────────────────────────────────────────────────────────────
# Analysis functions
# ─────────────────────────────────────────────────────────────────────────────

def query_summary(rows):
    """Per-query precision and position stats."""
    queries = defaultdict(lambda: {"up": [], "down": []})
    for r in rows:
        meta = r.get("meta") or {}
        score = float(meta.get("score") or 0)
        bucket = "up" if r["rating"] == 1 else "down"
        queries[r["query"]][bucket].append({
            "position": r["position"],
            "score":    score,
            "title":    meta.get("title", ""),
            "domain":   meta.get("core_domain", ""),
            "country":  meta.get("country", ""),
        })

    summaries = []
    for query, data in queries.items():
        total = len(data["up"]) + len(data["down"])
        precision = len(data["up"]) / total if total else 0
        up_scores   = [d["score"] for d in data["up"]]
        down_scores = [d["score"] for d in data["down"]]
        summaries.append({
            "query":          query,
            "total":          total,
            "thumbs_up":      len(data["up"]),
            "thumbs_down":    len(data["down"]),
            "precision":      round(precision * 100, 1),
            "avg_score_up":   round(sum(up_scores) / len(up_scores), 1) if up_scores else None,
            "avg_score_down": round(sum(down_scores) / len(down_scores), 1) if down_scores else None,
            "down_examples":  data["down"][:5],
        })

    return sorted(summaries, key=lambda x: x["precision"])


def score_calibration(rows):
    """
    Compares scores of thumbed-up vs thumbed-down results.
    Reveals if our scoring thresholds are aligned with user perception.
    """
    buckets = defaultdict(lambda: {"up": 0, "down": 0})
    for r in rows:
        meta = r.get("meta") or {}
        score = float(meta.get("score") or 0)
        band = f"{int(score // 10) * 10}-{int(score // 10) * 10 + 9}%"
        if r["rating"] == 1:
            buckets[band]["up"] += 1
        else:
            buckets[band]["down"] += 1

    return dict(sorted(buckets.items()))


def domain_health(rows):
    """
    Detects domain classification problems:
    - 'Unclassified' results that users thumbed up (enricher missed the domain)
    - Wrong domain assignments revealed by user feedback patterns
    """
    issues = []
    unclassified_up = []

    for r in rows:
        meta = r.get("meta") or {}
        domain = meta.get("core_domain", "")
        if domain in ("Unclassified", "", None) and r["rating"] == 1:
            unclassified_up.append({
                "query":  r["query"],
                "title":  meta.get("title", ""),
                "score":  meta.get("score"),
                "country": meta.get("country", ""),
            })

    if unclassified_up:
        issues.append({
            "type":    "unclassified_leak",
            "count":   len(unclassified_up),
            "message": f"{len(unclassified_up)} relevant results have 'Unclassified' domain — enricher failed to classify these.",
            "examples": unclassified_up[:5],
        })

    return issues


def failure_patterns(rows):
    """
    Groups thumbed-down results to find common failure modes:
    - Wrong domain filter causing misses
    - Low-score results surfacing (rerank not filtering enough)
    - Specific countries/authorities consistently irrelevant
    """
    down_rows = [r for r in rows if r["rating"] == -1]
    if not down_rows:
        return []

    patterns = []

    # Pattern 1: Low score but still surfaced
    low_score_shown = [r for r in down_rows if float((r.get("meta") or {}).get("score") or 0) < 30]
    if low_score_shown:
        patterns.append({
            "type":    "low_score_surfaced",
            "count":   len(low_score_shown),
            "message": f"{len(low_score_shown)} thumbed-down results had score < 30% — the score floor is too low.",
            "fix":     "Raise minimum score threshold to filter out weak results before returning to user.",
        })

    # Pattern 2: High position thumbed down (ranking problem, not coverage)
    high_pos_down = [r for r in down_rows if (r["position"] or 99) < 20]
    if high_pos_down:
        patterns.append({
            "type":    "top20_irrelevant",
            "count":   len(high_pos_down),
            "message": f"{len(high_pos_down)} thumbed-down results were in top 20 positions — ranking is wrong, not just coverage.",
            "examples": [{"pos": r["position"], "title": (r.get("meta") or {}).get("title", ""), "score": (r.get("meta") or {}).get("score")} for r in high_pos_down[:5]],
            "fix":     "Investigate domain filter logic and vector similarity for these specific queries.",
        })

    # Pattern 3: Domain mismatch
    domain_counts = defaultdict(int)
    for r in down_rows:
        d = (r.get("meta") or {}).get("core_domain", "Unknown")
        domain_counts[d] += 1
    if domain_counts:
        worst = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[0]
        if worst[1] >= 2:
            patterns.append({
                "type":    "domain_mismatch",
                "message": f"Most thumbed-down results are classified as '{worst[0]}' ({worst[1]} cases).",
                "fix":     f"Review '{worst[0]}' domain classification rules in enrichment prompt.",
            })

    return patterns


def score_threshold_recommendation(rows):
    """
    Finds the score value where up/down flips — suggests a better minimum threshold.
    """
    scored = []
    for r in rows:
        meta = r.get("meta") or {}
        score = float(meta.get("score") or 0)
        scored.append((score, r["rating"]))

    if not scored:
        return None

    scored.sort()
    # Find lowest score that still got a thumbs up
    up_scores = [s for s, rating in scored if rating == 1]
    down_scores = [s for s, rating in scored if rating == -1]

    return {
        "lowest_thumbs_up":   round(min(up_scores), 1) if up_scores else None,
        "highest_thumbs_down": round(max(down_scores), 1) if down_scores else None,
        "suggested_floor":    round(min(up_scores) * 0.95, 1) if up_scores else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────────────

def print_report(rows, min_feedback):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*70}")
    print(f"  TENDERSCOUT FEEDBACK ANALYSIS  —  {now}")
    print(f"  Total ratings: {len(rows)}  |  min_feedback filter: {min_feedback}")
    print(f"{'='*70}\n")

    # 1. Per-query summary
    summaries = query_summary(rows)
    print("── PER-QUERY PRECISION ──────────────────────────────────────────────")
    for s in summaries:
        bar = "█" * int(s["precision"] / 5) + "░" * (20 - int(s["precision"] / 5))
        print(f"  {s['query']:<20} {bar} {s['precision']:5.1f}%  "
              f"(👍{s['thumbs_up']} 👎{s['thumbs_down']})  "
              f"avg↑={s['avg_score_up']}  avg↓={s['avg_score_down']}")
        if s["down_examples"]:
            for ex in s["down_examples"]:
                print(f"    ✗ [{ex['score']}%] pos={ex['position']}  {ex['title'][:60]}")
    print()

    # 2. Score calibration
    calibration = score_calibration(rows)
    print("── SCORE CALIBRATION (what scores do users rate?) ───────────────────")
    for band, counts in calibration.items():
        total = counts["up"] + counts["down"]
        pct = counts["up"] / total * 100 if total else 0
        print(f"  {band:<10} 👍{counts['up']:3}  👎{counts['down']:3}  "
              f"{'✅ good' if pct >= 70 else '⚠️  mixed' if pct >= 40 else '❌ bad'}")
    print()

    # 3. Score threshold recommendation
    threshold = score_threshold_recommendation(rows)
    if threshold:
        print("── SCORE THRESHOLD RECOMMENDATION ───────────────────────────────────")
        print(f"  Lowest thumbs-up score:    {threshold['lowest_thumbs_up']}%")
        print(f"  Highest thumbs-down score: {threshold['highest_thumbs_down']}%")
        if threshold["suggested_floor"]:
            print(f"  Suggested minimum floor:   {threshold['suggested_floor']}%  "
                  f"(filter results below this before returning to user)")
        print()

    # 4. Domain health
    issues = domain_health(rows)
    if issues:
        print("── DOMAIN CLASSIFICATION ISSUES ──────────────────────────────────────")
        for issue in issues:
            print(f"  ⚠️  {issue['message']}")
            for ex in issue.get("examples", []):
                print(f"     • [{ex.get('score')}%] {ex.get('title', '')[:60]}  ({ex.get('country', '')})")
        print()

    # 5. Failure patterns
    patterns = failure_patterns(rows)
    if patterns:
        print("── FAILURE PATTERNS ──────────────────────────────────────────────────")
        for p in patterns:
            print(f"  ❌ {p['message']}")
            print(f"     → Fix: {p.get('fix', 'investigate further')}")
            for ex in p.get("examples", []):
                print(f"        pos={ex['pos']}  [{ex['score']}%]  {ex['title'][:55]}")
        print()

    print("── SUMMARY OF ACTIONS ────────────────────────────────────────────────")
    actions = []
    for s in summaries:
        if s["precision"] < 70 and s["thumbs_down"] > 0:
            actions.append(f"Query '{s['query']}': precision {s['precision']}% — investigate ranking for this query")
    for issue in issues:
        actions.append(issue["message"])
    for p in patterns:
        actions.append(p.get("fix", ""))
    if threshold and threshold["suggested_floor"] and threshold["suggested_floor"] > 20:
        actions.append(f"Raise minimum score floor to ~{threshold['suggested_floor']}% in api.py")

    if actions:
        for i, a in enumerate(actions, 1):
            print(f"  {i}. {a}")
    else:
        print("  ✅ No critical issues found in current feedback data.")
    print()


def write_html_report(rows, output_path: str):
    summaries = query_summary(rows)
    calibration = score_calibration(rows)
    patterns = failure_patterns(rows)
    issues = domain_health(rows)
    threshold = score_threshold_recommendation(rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows_html = ""
    for s in summaries:
        color = "#dcfce7" if s["precision"] >= 80 else "#fef9c3" if s["precision"] >= 50 else "#fee2e2"
        rows_html += f"""
        <tr style="background:{color}">
            <td><strong>{s['query']}</strong></td>
            <td>{s['total']}</td>
            <td>👍 {s['thumbs_up']}</td>
            <td>👎 {s['thumbs_down']}</td>
            <td>{s['precision']}%</td>
            <td>{s['avg_score_up'] or '—'}</td>
            <td>{s['avg_score_down'] or '—'}</td>
        </tr>"""
        for ex in s["down_examples"]:
            rows_html += f"""
        <tr style="background:#fff1f2; font-size:0.85em">
            <td colspan="2" style="padding-left:2em; color:#6b7280">✗ thumbed down</td>
            <td colspan="2">{ex['title'][:60]}</td>
            <td>{ex['score']}%</td>
            <td>pos={ex['position']}</td>
            <td>{ex['domain']} / {ex['country']}</td>
        </tr>"""

    calib_html = ""
    for band, counts in calibration.items():
        total = counts["up"] + counts["down"]
        pct = counts["up"] / total * 100 if total else 0
        color = "#dcfce7" if pct >= 70 else "#fef9c3" if pct >= 40 else "#fee2e2"
        calib_html += f"<tr style='background:{color}'><td>{band}</td><td>👍 {counts['up']}</td><td>👎 {counts['down']}</td><td>{pct:.0f}% relevant</td></tr>"

    issues_html = "".join(f"<li>⚠️ {i['message']}</li>" for i in issues)
    patterns_html = "".join(f"<li>❌ {p['message']}<br><em>→ {p.get('fix','')}</em></li>" for p in patterns)
    threshold_html = ""
    if threshold:
        threshold_html = f"""
        <p>Lowest thumbs-up score: <strong>{threshold['lowest_thumbs_up']}%</strong></p>
        <p>Highest thumbs-down score: <strong>{threshold['highest_thumbs_down']}%</strong></p>
        <p>Suggested floor: <strong>{threshold['suggested_floor']}%</strong></p>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TenderScout Feedback Analysis — {now}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; color: #1e293b; }}
  h1 {{ color: #2563eb; }} h2 {{ color: #475569; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
  th {{ background: #f1f5f9; padding: 8px 12px; text-align: left; font-size: 0.85rem; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #e2e8f0; font-size: 0.9rem; }}
  .badge {{ padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }}
  ul {{ line-height: 2; }}
</style>
</head>
<body>
<h1>TenderScout — Feedback Analysis</h1>
<p style="color:#64748b">Generated: {now} &nbsp;|&nbsp; Total ratings: {len(rows)}</p>

<h2>Per-Query Precision</h2>
<table>
  <tr><th>Query</th><th>Total</th><th>👍</th><th>👎</th><th>Precision</th><th>Avg Score ↑</th><th>Avg Score ↓</th></tr>
  {rows_html}
</table>

<h2>Score Calibration</h2>
<table>
  <tr><th>Score Band</th><th>Thumbs Up</th><th>Thumbs Down</th><th>% Relevant</th></tr>
  {calib_html}
</table>

<h2>Score Threshold</h2>
{threshold_html}

<h2>Domain Classification Issues</h2>
<ul>{issues_html or '<li>None detected</li>'}</ul>

<h2>Failure Patterns</h2>
<ul>{patterns_html or '<li>None detected</li>'}</ul>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
    print(f"HTML report saved: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-feedback", type=int, default=1, help="Min ratings per query to include")
    parser.add_argument("--html", action="store_true", help="Also write HTML report")
    args = parser.parse_args()

    rows = await load_feedback(args.min_feedback)
    if not rows:
        print("No feedback data found. Go rate some results first.")
        return

    print_report(rows, args.min_feedback)

    if args.html:
        os.makedirs("tests/reports", exist_ok=True)
        path = f"tests/reports/feedback_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.html"
        write_html_report(rows, path)

asyncio.run(main())
