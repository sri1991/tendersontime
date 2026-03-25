"""
Admin UI — simple HTML pages for monitoring ingestion jobs and testing search.

GET /admin/ingest  — dashboard: upload form + live job table
GET /admin/search  — search testing UI
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/admin", tags=["Admin"])

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TenderScout — Ingestion Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1rem 2rem; display: flex; align-items: center; gap: 1rem; border-bottom: 1px solid #334155; }
  header h1 { font-size: 1.2rem; font-weight: 600; }
  header span { color: #64748b; font-size: 0.85rem; }
  .container { max-width: 1100px; margin: 0 auto; padding: 2rem; }

  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }
  .card h2 { font-size: 0.9rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; margin-bottom: 1rem; }

  /* Upload form */
  .upload-row { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: flex-end; }
  .upload-row label { font-size: 0.82rem; color: #94a3b8; display: block; margin-bottom: 0.3rem; }
  input[type="file"] { background: #0f172a; border: 1px solid #475569; border-radius: 6px; padding: 0.5rem; color: #e2e8f0; font-size: 0.85rem; }
  input[type="number"] { background: #0f172a; border: 1px solid #475569; border-radius: 6px; padding: 0.5rem 0.75rem; color: #e2e8f0; font-size: 0.85rem; width: 90px; }
  button.upload-btn { background: #3b82f6; color: white; border: none; border-radius: 6px; padding: 0.55rem 1.25rem; font-size: 0.85rem; cursor: pointer; font-weight: 600; }
  button.upload-btn:hover { background: #2563eb; }
  button.upload-btn:disabled { background: #475569; cursor: default; }
  .upload-msg { margin-top: 0.75rem; font-size: 0.82rem; color: #94a3b8; }

  /* Jobs table */
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 0.5rem 0.75rem; color: #64748b; font-weight: 600; border-bottom: 1px solid #334155; }
  td { padding: 0.6rem 0.75rem; border-bottom: 1px solid #1e293b; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }

  /* Status badges */
  .badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
  .badge-queued    { background: #334155; color: #94a3b8; }
  .badge-running   { background: #1d4ed8; color: #bfdbfe; }
  .badge-completed { background: #14532d; color: #86efac; }
  .badge-failed    { background: #7f1d1d; color: #fca5a5; }

  /* Progress bar */
  .bar-wrap { background: #0f172a; border-radius: 999px; height: 6px; width: 120px; }
  .bar { background: #3b82f6; border-radius: 999px; height: 6px; transition: width 0.5s; }
  .bar-complete { background: #22c55e; }

  /* Stat pills */
  .stats { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .stat { background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 0.1rem 0.45rem; font-size: 0.72rem; color: #94a3b8; }
  .stat b { color: #e2e8f0; }

  .refresh-note { font-size: 0.75rem; color: #475569; margin-top: 0.5rem; }
  .no-jobs { color: #64748b; font-size: 0.85rem; padding: 1rem 0; }
  .job-id { font-family: monospace; font-size: 0.75rem; color: #64748b; }
</style>
</head>
<body>
<header>
  <h1>TenderScout</h1>
  <span>Ingestion Monitor</span>
</header>
<div class="container">

  <!-- Upload card -->
  <div class="card">
    <h2>Upload CSV</h2>
    <form id="uploadForm">
      <div class="upload-row">
        <div>
          <label>CSV File</label>
          <input type="file" id="csvFile" accept=".csv" required>
        </div>
        <div>
          <label>Batch size</label>
          <input type="number" id="batchSize" value="200" min="10" max="1000">
        </div>
        <button class="upload-btn" type="submit" id="uploadBtn">Upload &amp; Ingest</button>
      </div>
    </form>
    <div class="upload-msg" id="uploadMsg"></div>
  </div>

  <!-- Jobs card -->
  <div class="card">
    <h2>Recent Jobs</h2>
    <div id="jobsContainer"><div class="no-jobs">Loading...</div></div>
    <div class="refresh-note">Auto-refreshes every 5 seconds</div>
  </div>

</div>

<script>
const API = '/api/v1/ingest';

// ── Upload ──────────────────────────────────────────────────────────────────
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = document.getElementById('csvFile').files[0];
  const batchSize = document.getElementById('batchSize').value;
  const btn = document.getElementById('uploadBtn');
  const msg = document.getElementById('uploadMsg');

  if (!file) return;
  btn.disabled = true;
  btn.textContent = 'Uploading...';
  msg.textContent = '';

  const fd = new FormData();
  fd.append('file', file);

  try {
    const res = await fetch(`${API}/upload?batch_size=${batchSize}`, {
      method: 'POST', body: fd
    });
    const data = await res.json();
    if (res.ok) {
      msg.style.color = '#86efac';
      msg.textContent = `✓ Job started: ${data.job_id}`;
      loadJobs();
    } else {
      msg.style.color = '#fca5a5';
      msg.textContent = `✗ ${data.detail || 'Upload failed'}`;
    }
  } catch (err) {
    msg.style.color = '#fca5a5';
    msg.textContent = `✗ Network error: ${err.message}`;
  }

  btn.disabled = false;
  btn.textContent = 'Upload & Ingest';
});

// ── Jobs table ──────────────────────────────────────────────────────────────
function badge(status) {
  return `<span class="badge badge-${status}">${status}</span>`;
}

function bar(pct, status) {
  const cls = status === 'completed' ? 'bar bar-complete' : 'bar';
  return `<div class="bar-wrap"><div class="${cls}" style="width:${pct}%"></div></div>`;
}

function statPill(label, val) {
  return `<span class="stat">${label} <b>${val.toLocaleString()}</b></span>`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' });
}

function renderJobs(jobs) {
  const container = document.getElementById('jobsContainer');
  if (!jobs.length) {
    container.innerHTML = '<div class="no-jobs">No jobs yet. Upload a CSV to start.</div>';
    return;
  }

  const rows = jobs.map(j => {
    const s = j.stats || {};
    const pct = j.progress_pct ?? 0;
    return `<tr>
      <td>
        <div>${j.filename}</div>
        <div class="job-id">${j.job_id}</div>
      </td>
      <td>${badge(j.status)}</td>
      <td>
        ${bar(pct, j.status)}
        <div style="font-size:0.72rem;color:#64748b;margin-top:3px">${pct.toFixed(1)}%
          (${(j.rows_processed||0).toLocaleString()} / ${j.total_rows ? j.total_rows.toLocaleString() : '?'})</div>
      </td>
      <td>
        <div class="stats">
          ${statPill('t0', s.tier0||0)}
          ${statPill('t2', s.tier2||0)}
          ${statPill('idx', s.indexed||0)}
          ${statPill('dup', s.dupes||0)}
          ${(s.errors||0) > 0 ? statPill('err', s.errors) : ''}
        </div>
      </td>
      <td style="font-size:0.75rem;color:#64748b">
        <div>Started: ${fmtTime(j.started_at)}</div>
        ${j.finished_at ? `<div>Done: ${fmtTime(j.finished_at)}</div>` : ''}
        ${j.error_message ? `<div style="color:#fca5a5">${j.error_message.slice(0,60)}</div>` : ''}
      </td>
    </tr>`;
  }).join('');

  container.innerHTML = `<table>
    <thead><tr>
      <th>File</th><th>Status</th><th>Progress</th><th>Stats</th><th>Time</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadJobs() {
  try {
    const res = await fetch(`${API}/jobs`);
    const jobs = await res.json();
    renderJobs(jobs);
  } catch {
    // silent fail on network error
  }
}

loadJobs();
setInterval(loadJobs, 5000);
</script>
</body>
</html>
"""


@router.get("/ingest", response_class=HTMLResponse, include_in_schema=False)
async def admin_ingest_dashboard():
    """Ingestion monitoring dashboard."""
    return HTMLResponse(content=_PAGE)


_SEARCH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TenderScout — Search</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1rem 2rem; display: flex; align-items: center; gap: 1.5rem; border-bottom: 1px solid #334155; }
  header h1 { font-size: 1.2rem; font-weight: 600; }
  header nav a { color: #64748b; text-decoration: none; font-size: 0.85rem; padding: 0.3rem 0.75rem; border-radius: 6px; }
  header nav a:hover { background: #334155; color: #e2e8f0; }
  header nav a.active { background: #334155; color: #e2e8f0; }
  .container { max-width: 1000px; margin: 0 auto; padding: 2rem; }

  /* Search bar */
  .search-bar { display: flex; gap: 0.75rem; margin-bottom: 1rem; }
  .search-bar input[type="text"] {
    flex: 1; background: #1e293b; border: 1px solid #475569; border-radius: 8px;
    padding: 0.75rem 1rem; color: #e2e8f0; font-size: 1rem; outline: none;
  }
  .search-bar input[type="text"]:focus { border-color: #3b82f6; }
  .search-bar button {
    background: #3b82f6; color: white; border: none; border-radius: 8px;
    padding: 0.75rem 1.5rem; font-size: 0.9rem; font-weight: 600; cursor: pointer;
  }
  .search-bar button:hover { background: #2563eb; }
  .search-bar button:disabled { background: #475569; cursor: default; }

  /* Filters */
  .filters { display: flex; gap: 0.75rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
  .filters label { font-size: 0.78rem; color: #64748b; }
  .filters input, .filters select {
    background: #1e293b; border: 1px solid #334155; border-radius: 6px;
    padding: 0.4rem 0.65rem; color: #e2e8f0; font-size: 0.82rem;
  }
  .filters input { width: 130px; }
  .top-k { display: flex; align-items: center; gap: 0.4rem; }
  .top-k input { width: 60px; }

  /* Route pill */
  .meta { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1.25rem; font-size: 0.8rem; color: #64748b; }
  .route-pill { background: #1e293b; border: 1px solid #334155; border-radius: 999px; padding: 0.2rem 0.75rem; font-size: 0.75rem; }
  .route-pill b { color: #e2e8f0; }
  .weight { background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 0.15rem 0.45rem; font-size: 0.72rem; }
  .weight b { color: #e2e8f0; }

  /* Result cards */
  .result-card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 1.1rem 1.25rem; margin-bottom: 0.85rem;
    transition: border-color 0.15s;
  }
  .result-card:hover { border-color: #475569; }
  .result-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 0.5rem; }
  .result-title { font-size: 0.95rem; font-weight: 600; color: #f1f5f9; line-height: 1.4; }
  .score-badge { background: #1d4ed8; color: #bfdbfe; border-radius: 6px; padding: 0.2rem 0.55rem; font-size: 0.72rem; font-weight: 700; white-space: nowrap; }
  .result-meta { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
  .pill { background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 0.15rem 0.5rem; font-size: 0.72rem; color: #94a3b8; }
  .pill b { color: #e2e8f0; }
  .pill.cpv { border-color: #1d4ed8; color: #93c5fd; }
  .pill.type-supply  { border-color: #065f46; color: #6ee7b7; }
  .pill.type-works   { border-color: #92400e; color: #fcd34d; }
  .pill.type-services { border-color: #4c1d95; color: #c4b5fd; }
  .pill.type-consultancy { border-color: #7c2d12; color: #fdba74; }
  .result-sources { font-size: 0.72rem; color: #475569; }
  .result-sources b { color: #64748b; }
  .doc-link { color: #3b82f6; font-size: 0.75rem; text-decoration: none; }
  .doc-link:hover { text-decoration: underline; }

  /* States */
  .empty { color: #64748b; font-size: 0.9rem; padding: 2rem 0; text-align: center; }
  .error { color: #fca5a5; font-size: 0.85rem; padding: 1rem 0; }
  .spinner { color: #64748b; font-size: 0.85rem; padding: 1rem 0; }

  /* Quick queries */
  .quick { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
  .quick button {
    background: #1e293b; border: 1px solid #334155; border-radius: 999px;
    padding: 0.3rem 0.85rem; font-size: 0.78rem; color: #94a3b8; cursor: pointer;
  }
  .quick button:hover { border-color: #3b82f6; color: #e2e8f0; }
</style>
</head>
<body>
<header>
  <h1>TenderScout</h1>
  <nav>
    <a href="/admin/ingest">Ingestion</a>
    <a href="/admin/search" class="active">Search</a>
  </nav>
</header>
<div class="container">

  <div class="search-bar">
    <input type="text" id="queryInput" placeholder="Search tenders — e.g. GPS tracking system for vehicles" autofocus>
    <button id="searchBtn" onclick="doSearch()">Search</button>
  </div>

  <div class="filters">
    <span class="top-k">
      <label>Results</label>
      <input type="number" id="topK" value="10" min="1" max="50">
    </span>
    <span>
      <label>Country&nbsp;</label>
      <input type="text" id="filterCountry" placeholder="e.g. Kenya">
    </span>
    <span>
      <label>CPV&nbsp;</label>
      <input type="text" id="filterCPV" placeholder="e.g. 45000000">
    </span>
    <span>
      <label>Type&nbsp;</label>
      <select id="filterType">
        <option value="">Any</option>
        <option>Supply</option>
        <option>Works</option>
        <option>Services</option>
        <option>Consultancy</option>
      </select>
    </span>
  </div>

  <div class="quick">
    <span style="font-size:0.75rem;color:#475569;align-self:center">Try:</span>
    <button onclick="setQuery('GPS tracking system for vehicles')">GPS tracking vehicles</button>
    <button onclick="setQuery('supply of medical equipment')">medical equipment</button>
    <button onclick="setQuery('construction road works')">road construction</button>
    <button onclick="setQuery('CPV 45310000')">CPV 45310000</button>
    <button onclick="setQuery('software development platform cloud')">cloud software</button>
    <button onclick="setQuery('workwear uniforms protective clothing')">workwear uniforms</button>
  </div>

  <div id="metaBar"></div>
  <div id="results"><div class="empty">Enter a query above to search tenders.</div></div>

</div>
<script>
function setQuery(q) {
  document.getElementById('queryInput').value = q;
  doSearch();
}

document.getElementById('queryInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
});

function typePillClass(t) {
  if (!t) return '';
  const m = { supply:'type-supply', works:'type-works', services:'type-services', consultancy:'type-consultancy' };
  return m[t.toLowerCase()] || '';
}

function fmtValue(v) {
  if (!v) return null;
  return '$' + Number(v).toLocaleString(undefined, {maximumFractionDigits: 0});
}

function renderMeta(data) {
  const w = data.weights || {};
  const wPills = Object.entries(w).map(([k,v]) =>
    `<span class="weight">${k} <b>${v}</b></span>`
  ).join(' ');
  document.getElementById('metaBar').innerHTML = `
    <div class="meta">
      <span class="route-pill">Route: <b>${data.query_type}</b></span>
      ${wPills}
      <span style="margin-left:auto">${data.total_hits} result${data.total_hits !== 1 ? 's' : ''}</span>
    </div>`;
}

function renderResults(results) {
  if (!results.length) {
    document.getElementById('results').innerHTML =
      '<div class="empty">No results found. Try a different query or upload more data.</div>';
    return;
  }

  const html = results.map((r, i) => {
    const title = r.summary || r.tot_id;
    const country = r.country ? `<span class="pill"><b>${r.country}</b></span>` : '';
    const cpv = r.cpv_code ? `<span class="pill cpv">CPV ${r.cpv_code}${r.cpv_description ? ' · ' + r.cpv_description : ''}</span>` : '';
    const ptype = r.procurement_type ? `<span class="pill ${typePillClass(r.procurement_type)}">${r.procurement_type}</span>` : '';
    const val = fmtValue(r.usd_tender_value);
    const valPill = val ? `<span class="pill">USD ${val}</span>` : '';
    const closing = r.closing_date ? `<span class="pill">Closes ${r.closing_date.slice(0,10)}</span>` : '';
    const docLink = r.tender_notice_document
      ? `<a class="doc-link" href="${r.tender_notice_document}" target="_blank" rel="noopener">View document ↗</a>`
      : '';
    const sources = r.sources ? `<span class="result-sources">Sources: <b>${r.sources.join(' + ')}</b></span>` : '';

    return `<div class="result-card">
      <div class="result-header">
        <div class="result-title">${i+1}. ${escHtml(title)}</div>
        <span class="score-badge">${r.score.toFixed(4)}</span>
      </div>
      <div class="result-meta">${country}${cpv}${ptype}${valPill}${closing}</div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        ${sources}
        ${docLink}
      </div>
    </div>`;
  }).join('');

  document.getElementById('results').innerHTML = html;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function doSearch() {
  const query = document.getElementById('queryInput').value.trim();
  if (!query) return;

  const btn = document.getElementById('searchBtn');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  document.getElementById('results').innerHTML = '<div class="spinner">Searching…</div>';
  document.getElementById('metaBar').innerHTML = '';

  const body = {
    query,
    top_k: parseInt(document.getElementById('topK').value) || 10,
    country: document.getElementById('filterCountry').value.trim() || null,
    cpv_code: document.getElementById('filterCPV').value.trim() || null,
    procurement_type: document.getElementById('filterType').value || null,
  };

  try {
    const t0 = performance.now();
    const res = await fetch('/api/v1/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const elapsed = Math.round(performance.now() - t0);

    if (!res.ok) {
      const err = await res.json();
      document.getElementById('results').innerHTML =
        `<div class="error">Error: ${err.detail || res.statusText}</div>`;
      return;
    }

    const data = await res.json();
    // Attach elapsed to meta
    data._elapsed = elapsed;

    renderMeta({ ...data, total_hits: `${data.total_hits} (${elapsed}ms)` });
    renderResults(data.results);
  } catch (err) {
    document.getElementById('results').innerHTML =
      `<div class="error">Network error: ${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}
</script>
</body>
</html>
"""


@router.get("/search", response_class=HTMLResponse, include_in_schema=False)
async def admin_search_ui():
    """Search testing UI."""
    return HTMLResponse(content=_SEARCH_PAGE)
