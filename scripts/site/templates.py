#!/usr/bin/env python3
"""templates.py — v3.2 single-page wiki site (compact table, inline expansion)."""
from datetime import datetime
from pathlib import Path


_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | Wiki</title>
<link rel="stylesheet" href="assets/site.css">
</head>
<body>
<div class="page-wrapper">
  <main class="container">{content}</main>
  <footer class="site-footer"><p>Wiki · {generated_at}</p></footer>
</div>
<script src="assets/marked.min.js"></script>
<script src="assets/site.js?v=3.5"></script>
</body>
</html>
"""

_INDEX_CONTENT = """
<div class="hero">
  <h1>Wiki</h1>
  <div class="stats" id="stats"></div>
  <div class="nav-pills">
    <button id="nav-records" class="active">📋 Records</button>
    <button id="nav-trends">📈 Trends</button>
  </div>
</div>
<div id="records-view">
<div class="controls">
  <input type="search" id="search" placeholder="Search title, TL;DR, tags, URLs, entities..." autocomplete="off">
  <select id="filter-type"><option value="">All types</option></select>
  <select id="filter-status">
    <option value="">All status</option>
    <option value="done">done</option>
    <option value="pending">pending</option>
  </select>
</div>
<div id="table-container"></div>
</div>
<div id="trends-view" style="display:none">
  <div id="trend-list"></div>
  <div id="trend-article" style="display:none">
    <button id="trend-back">← Back to trends</button>
    <article id="trend-body" class="markdown-body"></article>
  </div>
</div>
"""


def _render_page(title, content, generated_at):
    return _BASE_TEMPLATE.format(title=title, content=content, generated_at=generated_at)


_RAW_CONTENT = r"""
<div id="raw-loading" class="muted">Loading...</div>
<div id="raw-view" style="display:none">
  <h1 id="raw-title"></h1>
  <p class="muted" id="raw-meta"></p>
  <ul id="raw-list" class="raw-tree"></ul>
</div>
<script>
async function initRaw() {
  const id = new URLSearchParams(window.location.search).get('id');
  if (!id) { document.getElementById('raw-loading').textContent = 'Missing id'; return; }
  try {
    const entries = await fetch('data/entries.json').then(r=>r.json());
    const entry = entries.find(e=>e.id===id);
    if (!entry) { document.getElementById('raw-loading').textContent = 'Entry not found: '+id; return; }
    const res = await fetch('../artifacts/'+id+'/raw/');
    const text = await res.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(text,'text/html');
    const links = [...doc.querySelectorAll('a')].map(a=>({href:a.getAttribute('href'),text:a.textContent}));
    document.getElementById('raw-loading').style.display='none';
    document.getElementById('raw-view').style.display='';
    document.getElementById('raw-title').textContent = entry.title||id;
    document.getElementById('raw-meta').innerHTML = 'ID: '+id;
    const list = document.getElementById('raw-list');
    if (!links.length) { list.innerHTML='<li class=\"muted\">(empty)</li>'; return; }
    list.innerHTML = links.filter(l=>l.href&&!l.href.endsWith('/')).map(l=>{
      const icon = l.href.endsWith('.pdf')?'📄':l.href.endsWith('.html')?'🌐':l.href.match(/\.(png|jpe?g|gif|webp|svg)/i)?'🖼️':'📎';
      return '<li><a href="../artifacts/'+id+'/raw/'+l.href+'" target="_blank">'+icon+' '+l.href.split('/').pop()+'</a></li>';
    }).join('');
  } catch(err) { document.getElementById('raw-loading').textContent = 'Load failed: '+err.message; }
}
if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',initRaw); else initRaw();
</script>
"""


_SURVEY_CONTENT = r"""
<div id="survey-loading" class="muted">Loading...</div>
<div id="survey-view" style="display:none">
  <p class="survey-nav"><a href="/site/">← Wiki</a> · <a id="survey-record-link" href="#">View record</a></p>
  <article id="survey-body" class="markdown-body"></article>
  <p class="muted" id="survey-meta"></p>
</div>
<script>
async function initSurvey() {
  const id = new URLSearchParams(window.location.search).get('id');
  if (!id) { document.getElementById('survey-loading').textContent = 'Missing id'; return; }
  try {
    const res = await fetch('/artifacts/' + encodeURIComponent(id) + '/survey/survey.md');
    if (!res.ok) throw new Error('survey.md: HTTP ' + res.status);
    const md = await res.text();
    document.getElementById('survey-loading').style.display = 'none';
    document.getElementById('survey-view').style.display = '';
    document.getElementById('survey-record-link').href = '/site/?q=' + encodeURIComponent(id);
    const body = document.getElementById('survey-body');
    body.innerHTML = window.marked ? marked.parse(md) : '<pre>' + md.replace(/</g,'&lt;') + '</pre>';
    try {
      const meta = await (await fetch('/artifacts/' + encodeURIComponent(id) + '/survey/survey.json')).json();
      document.getElementById('survey-meta').textContent =
        'revision ' + (meta.revision || 1) + ' · updated ' + String(meta.updated_at || '').slice(0, 10) +
        ' · sources ' + ((meta.sources || []).length);
    } catch (_) {}
  } catch (err) { document.getElementById('survey-loading').textContent = 'Load failed: ' + err.message; }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSurvey); else initSurvey();
</script>
"""


def render_pages(entries, tags, sources, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "index.html").write_text(
        _render_page("Home", _INDEX_CONTENT, generated_at), encoding="utf-8"
    )
    (out_dir / "raw.html").write_text(
        _render_page("Raw", _RAW_CONTENT, generated_at), encoding="utf-8"
    )
    (out_dir / "survey.html").write_text(
        _render_page("Survey", _SURVEY_CONTENT, generated_at), encoding="utf-8"
    )


if __name__ == "__main__":
    from scripts import paths
    out = paths.get_workspace() / "site"
    render_pages([], {}, {}, out)
    print(f"rendered pages to {out}")
