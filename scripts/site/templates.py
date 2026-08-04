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
<script src="assets/site.js?v=3.17"></script>
</body>
</html>
"""

_INDEX_CONTENT = """
<div class="hero">
  <h1>Wiki</h1>
  <div class="stats" id="stats"></div>
  <div class="nav-pills">
    <button id="nav-records" class="active">📋 Records</button>
    <button id="nav-posts">📝 Posts</button>
    <button id="nav-tracking">🎯 Tracking</button>
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
  <label class="watch-only" title="只看特别关注"><input type="checkbox" id="filter-watch"> ★ 关注</label>
</div>
<div id="table-container"></div>
</div>
<div id="posts-view" style="display:none">
  <div class="post-trigger">
    <input type="text" id="post-topic-input" placeholder="输入主题，基于 wiki 证据写一篇 post…">
    <button id="post-topic-btn">✍️ 发起写作</button>
    <span id="post-trigger-status" class="muted"></span>
  </div>
  <div id="post-suggest"></div>
  <div id="post-list"></div>
</div>
<div id="tracking-view" style="display:none">
  <div id="tracking-list"></div>
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


_DOC_READER = r"""
<div id="doc-loading" class="muted">Loading...</div>
<div id="doc-view" style="display:none">
  <p class="dive-nav"><a href="/site/">← Wiki</a> · <a id="doc-back-list" href="#">返回列表</a></p>
  <article id="doc-body" class="markdown-body"></article>
</div>
<script>
function esc(text){ const d=document.createElement('div'); d.textContent=text||''; return d.innerHTML; }
function renderBoldD(t){ return t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>'); }
const ICONS={github:'⌥',arxiv:'📄',huggingface:'🤗',homepage:'🏠',weixin:'💬',linkedin:'💼',docs:'📚',other:'🔗'};

function linkBadgeD(l){
  const dot=l.verified===1?'<span class="dot ok"></span>':l.verified===0?'<span class="dot dead"></span>':'';
  return `<a class="link-badge${l.origin==='inferred'?' inferred':''}" href="${esc(l.url)}" target="_blank" rel="noopener" title="${esc(l.url)}">${ICONS[l.kind]||ICONS.other}</a>${dot}`;
}

function statusBadgeD(s){ const c={done:'done',pending:'pending',running:'running',failed:'failed'}[s]||'other';
  return `<span class="badge badge-${c}">${esc(s||'—')}</span>`; }

function surveyCellD(e){
  if(e.has_survey) return `<a class="survey-btn" href="/site/survey.html?id=${encodeURIComponent(e.id)}" target="_blank" rel="noopener">🧭 查看综述</a>`;
  const st=e.survey_state;
  if(st==='collecting'||st==='writing'||st==='awaiting_agent'||st==='failed')
    return `<span class="badge badge-${st==='failed'?'failed':st==='awaiting_agent'?'pending':'running'}">综述 ${st==='failed'?'失败':st==='awaiting_agent'?'排队中':'进行中'}</span>`;
  if(e.has_record) return `<button class="survey-btn" id="survey-go">🧭 发起综述</button> <span id="survey-status" class="muted"></span>`;
  return '';
}

function recLink(id){ return `<a class="rec-link" href="/site/doc.html?kind=record&id=${encodeURIComponent(id)}">${esc(id)}</a>`; }

async function initDoc() {
  const q = new URLSearchParams(window.location.search);
  const kind = q.get('kind') || 'post';
  const slug = q.get('slug') || '';
  const id = q.get('id') || '';
  const el = (x) => document.getElementById(x);
  if (!slug && !id) { el('doc-loading').textContent = 'Missing slug/id'; return; }

  if (kind === 'record') {
    if (!id) { el('doc-loading').textContent = 'Missing id'; return; }
    el('doc-back-list').href = '/site/';
    try {
      const entries = await fetch('/site/data/entries.json').then(r=>r.json());
      const e = entries.find(x => x.id === id);
      let rec = {};
      try { rec = await (await fetch('/artifacts/' + encodeURIComponent(id) + '/record.json')).json(); } catch(_) {}
      if (!e && !rec.title) throw new Error('record not found: ' + id);
      const title = rec.title || e.title || id;
      const tldr = (rec.tldr || (e.summary && e.summary.tldr) || e.overview || '');
      const summary = rec.summary || '';
      const links = (rec.links && rec.links.length ? rec.links : (e.links || []));
      const entities = rec.entities || e.entities || {};
      const tags = rec.tags || e.tags || [];
      el('doc-loading').style.display='none'; el('doc-view').style.display='';
      let html = `<h1>${esc(title)}</h1>
        <p class="trend-meta">${esc(e ? e.id : id)} · ${esc(e ? e.date : (rec.date||''))} ${e ? statusBadgeD(e.status) : ''} · ${esc(e ? (e.topic_type||'') : (rec.topic_type||''))}</p>`;
      if(tldr) html += `<p><strong>TL;DR</strong> ${esc(tldr)}</p>`;
      if(summary) html += `<div class="summary-block">${renderBoldD(esc(summary)).split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>`<p>${p}</p>`).join('')}</div>`;
      if(links.length){ const seen={}; let lh='<p><strong>Links</strong><br>';
        for(const l of links){ try{ const u=new URL(l.url); const d=u.hostname.replace('www.',''); (seen[d]=seen[d]||[]).push(l);}catch(_){ (seen.other=seen.other||[]).push(l);} }
        for(const [d,ls] of Object.entries(seen)) lh+=`<span class="link-domain">${esc(d)}</span> `+ls.map(linkBadgeD).join('')+'<br>';
        lh+='</p>'; html+=lh; }
      if(tags.length) html += `<p><strong>Tags</strong> ${tags.map(t=>`<span class="badge badge-tag">${esc(t)}</span>`).join(' ')}</p>`;
      const entBits=[];
      for(const [k,v] of Object.entries(entities)) if(v&&v.length) entBits.push(`${k}: ${v.map(n=>`<span class="ent-chip" data-entname="${esc(n)}" data-entkind="${esc(k==='author'?'person':k)}" title="🎯 发起跟踪">${esc(n)}</span>`).join(' ')}`);
      if(entBits.length) html += `<p><strong>Entities</strong> ${entBits.join(' · ')}</p>`;
      if((e._related||[]).length){ html += `<p class="related-head"><strong>Related</strong></p><ul class="related-list">`;
        html += e._related.map(r=>`<li>${recLink(r.id)} <span class="rel-title">${esc(r.title||'')}</span> <span class="rel-score muted">${r.score}</span></li>`).join('');
        html += '</ul>'; }
      const pv=e && e.preview && e.preview.recall;
      if(pv && (pv.matches||[]).length){ html += `<details class="preview-recall"><summary>🔁 发起时召回（${pv.matches.length}）</summary><ul class="related-list">`;
        html += pv.matches.map(m=>`<li>${recLink(m.id)} ${m.title?`<span class="rel-title">${esc(m.title)}</span>`:''} <span class="rel-score muted">${m.score}</span></li>`).join('');
        html += '</ul></details>'; }
      html += `<p>${surveyCellD(e||{})}</p>`;
      if(e && e.has_record) html += `<p class="link-add" data-linkadd="${esc(e.id)}"><button class="link-add-toggle">＋ 添加链接</button></p>`;
      html += `<p><a href="/site/raw.html?id=${encodeURIComponent(id)}" target="_blank" rel="noopener">📁 Raw materials</a></p>`;
      el('doc-body').innerHTML = html;
      // entity chips → tracking
      document.querySelectorAll('.ent-chip').forEach(chip=>chip.addEventListener('click', async (ev)=>{
        ev.stopPropagation();
        if(!window.confirm('为「'+chip.dataset.entname+'」创建跟踪主题？将自动关联已入库记录并生成跟踪页（可能发起一次 headless 写作）。')) return;
        try{
          const r=await fetch('/api/track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:chip.dataset.entname,kind:chip.dataset.entkind})});
          const d=await r.json().catch(()=>({}));
          chip.title=(r.ok&&d.ok)?(d.exists?('已有跟踪：'+d.slug):('已创建：'+d.slug)):((d.message)||'失败');
          chip.classList.add('tracked');
        }catch(_){ chip.title='发起失败（请重启 site --serve）'; }
      }));
      // survey trigger
      const sg=document.getElementById('survey-go');
      if(sg) sg.addEventListener('click', async ()=>{
        sg.disabled=true; const st=document.getElementById('survey-status');
        st.textContent=' ⏳ 已发起，采集+写作中…';
        try{
          const r=await fetch('/api/survey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
          const d=await r.json().catch(()=>({}));
          if(!r.ok||!d.ok){ st.textContent=' '+(d.message||('失败 HTTP '+r.status)); sg.disabled=false; return; }
          const t0=Date.now();
          const timer=setInterval(async()=>{
            try{
              const s2=await (await fetch('/api/survey/status?id='+encodeURIComponent(id))).json();
              if(s2.has_survey){ clearInterval(timer); st.innerHTML=` ✓ <a href="/site/survey.html?id=${encodeURIComponent(id)}" target="_blank">查看综述</a>`; return; }
              const state=s2.status&&s2.status.state;
              if(state==='failed'){ clearInterval(timer); st.textContent=' 综述失败'; sg.disabled=false; return; }
              if(Date.now()-t0>600000){ clearInterval(timer); st.textContent=' 仍在进行，可稍后刷新'; }
            }catch(_){}
          },5000);
        }catch(err){ st.textContent=' 服务不支持（请重启 site --serve）'; sg.disabled=false; }
      });
      // add-link form
      document.querySelectorAll('[data-linkadd]').forEach(wrap=>{
        wrap.querySelector('.link-add-toggle').addEventListener('click', ()=>{
          if(wrap.querySelector('input')) return;
          const form=document.createElement('span'); form.className='link-add-form';
          form.innerHTML=` <input type="url" placeholder="https://…" size="40"> <select><option value="related">related</option><option value="canonical">canonical</option></select> <button data-act="add">添加</button> <button data-act="addsurvey" title="添加并自动更新综述">添加并更新综述</button> <span class="link-add-status muted"></span>`;
          wrap.appendChild(form);
          const input=form.querySelector('input'); const statusEl=form.querySelector('.link-add-status'); input.focus();
          const submit=async(updateSurvey)=>{
            const url=input.value.trim(); const role=form.querySelector('select').value;
            if(!/^https?:\/\/\S+$/.test(url)){ statusEl.textContent=' URL 需以 http(s) 开头'; return; }
            statusEl.textContent=' 添加中…';
            try{
              const r=await fetch('/api/record-links',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,url,role,update_survey:updateSurvey})});
              const d=await r.json().catch(()=>({}));
              if(!r.ok||!d.ok){ statusEl.textContent=' '+(d.message||('失败 HTTP '+r.status)); return; }
              const badge=document.createElement('a'); badge.className='link-badge'; badge.href=url; badge.target='_blank'; badge.rel='noopener'; badge.title=url+'（manual）'; badge.textContent=ICONS[d.link&&d.link.kind]||ICONS.other;
              const lp=document.querySelector('.markdown-body p strong');
              (lp?lp.parentElement:wrap).appendChild(badge);
              statusEl.textContent= updateSurvey?' ✓ 已添加，综述更新中（稍后刷新查看）':' ✓ 已添加';
            }catch(err){ statusEl.textContent=' 服务不支持（请重启 site --serve）'; }
          };
          form.querySelector('[data-act="add"]').addEventListener('click',()=>submit(false));
          form.querySelector('[data-act="addsurvey"]').addEventListener('click',()=>submit(true));
        });
      });
    } catch (err) { el('doc-loading').textContent = 'Load failed: ' + err.message; }
    return;
  }

  // post / tracking markdown
  el('doc-back-list').href = '/site/' + (kind === 'tracking' ? '?v=tracking' : '?v=posts');
  const src = kind === 'tracking' ? 'tracking/' + slug + '/digest.md' : 'posts/' + slug + '.md';
  try {
    const res = await fetch('/' + src);
    if (!res.ok) throw new Error(src + ': HTTP ' + res.status);
    const md = await res.text();
    el('doc-loading').style.display = 'none';
    el('doc-view').style.display = '';
    const body = el('doc-body');
    const html = window.marked ? marked.parse(md) : '<pre>' + md.replace(/</g, '&lt;') + '</pre>';
    body.innerHTML = html.replace(/(?<![\w"=/])(20\d\d-\d\d-\d\d_[A-Za-z0-9_-]+)/g,
      '<a href="/site/doc.html?kind=record&id=$1" class="rec-link">$1</a>');
  } catch (err) { el('doc-loading').textContent = 'Load failed: ' + err.message; }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initDoc); else initDoc();
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
    (out_dir / "doc.html").write_text(
        _render_page("Doc", _DOC_READER, generated_at), encoding="utf-8"
    )


if __name__ == "__main__":
    from scripts import paths
    out = paths.get_workspace() / "site"
    render_pages([], {}, {}, out)
    print(f"rendered pages to {out}")
