/**
 * Wiki Site v3.2 — compact table, inline expansion, month collapse.
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

const LINK_ICONS = {
  github: '⌥', arxiv: '📄', huggingface: '🤗', homepage: '🏠',
  weixin: '💬', linkedin: '💼', docs: '📚', other: '🔗',
};

function linkBadge(l) {
  const icon = LINK_ICONS[l.kind] || LINK_ICONS.other;
  const dot = l.verified === 1 ? '<span class="dot ok"></span>'
    : l.verified === 0 ? '<span class="dot dead"></span>' : '';
  const cls = l.origin === 'inferred' ? ' inferred' : '';
  return `<a class="link-badge${cls}" href="${escapeHtml(l.url)}" target="_blank" rel="noopener" title="${escapeHtml(l.url)}">${icon}</a>${dot}`;
}

async function loadJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function statusBadge(s) {
  const cls = { done: 'done', pending: 'pending', running: 'running', failed: 'failed' }[s] || 'other';
  return `<span class="badge badge-${cls}">${escapeHtml(s||'—')}</span>`;
}

function getParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

// ================= init =================
async function init() {
  const [entries, tags, trends] = await Promise.all([
    loadJSON('/site/data/entries.json'),
    loadJSON('/site/data/tags.json').catch(() => ({})),
    loadJSON('/site/data/trends.json').catch(() => []),
  ]);

  // stats
  const total = entries.length;
  const done = entries.filter(e => e.status === 'done').length;
  const withRec = entries.filter(e => e.has_record).length;
  const trendCount = (trends || []).length;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><b>${total}</b> entries</div>
    <div class="stat"><b>${done}</b> done</div>
    <div class="stat"><b>${withRec}</b> records</div>
    <div class="stat"><b>${trendCount}</b> trends</div>
  `;

  // type filter
  const sel = document.getElementById('filter-type');
  [...new Set(entries.map(e => e.topic_type || e.type).filter(Boolean))].sort()
    .forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; sel.appendChild(o); });

  const searchInput = document.getElementById('search');
  const statusSel = document.getElementById('filter-status');
  const container = document.getElementById('table-container');
  // v3.5：支持 /site/?q=<kw> 预填搜索（dive.html 的 "View record" 回跳用）
  const q0 = getParam('q');
  if (q0) searchInput.value = q0;

  // group by month
  function monthKey(e) {
    // v3.3：按采集日期（id 前缀 YYYY-MM-DD）分组，不是内容日期
    const d = e.id || '';
    return d.length >= 7 ? d.substring(0, 7) : 'unknown';
  }
  const months = {};
  for (const e of entries) {
    const mk = monthKey(e);
    if (!months[mk]) months[mk] = [];
    months[mk].push(e);
  }
  const monthList = Object.keys(months).sort().reverse(); // newest first
  const now = new Date();
  const cutoff = new Date(now.getFullYear(), now.getMonth() - 1, 1).toISOString().substring(0, 7);

  function render() {
    const q = (searchInput.value || '').toLowerCase().trim();
    const type = sel.value;
    const status = statusSel.value;

    let html = '';
    for (const mk of monthList) {
      const visible = months[mk].filter(e => {
        if (type && (e.topic_type || e.type) !== type) return false;
        if (status && e.status !== status) return false;
        if (!q) return true;
        const links = (e.links||[]).map(l=>l.url||'').join(' ').toLowerCase();
        const ents = (e.entities ? Object.values(e.entities).flat().join(' ') : '').toLowerCase();
        const aliases = (e._search_aliases||[]).join(' ').toLowerCase();
        return `${e.title} ${e.overview} ${(e.tags||[]).join(' ')} ${links} ${ents} ${aliases}`.toLowerCase().includes(q);
      });
      if (!visible.length) continue;

      const collapsed = mk < cutoff;
      const id = `m-${mk.replace('-','')}`;
      html += `<div class="month-group${collapsed ? ' collapsed' : ''}" data-month="${mk}">`;
      html += `<h3 class="month-header" data-target="${id}">${mk} · ${visible.length} entries ${collapsed ? '▸' : '▾'}</h3>`;
      html += `<div class="month-body" id="${id}">`;
      html += '<table class="wiki-table"><tbody>';
      for (const e of visible) {
        const tldr = (e.summary && e.summary.tldr) || e.overview || '';
        const linksHtml = (e.links||[]).slice(0,6).map(l => linkBadge(l)).join('');
        html += `<tr class="wiki-row" data-id="${escapeHtml(e.id)}">`;
        html += `<td class="col-id"><a href="javascript:void(0)" class="row-toggle">${escapeHtml(e.id)}</a></td>`;
        html += `<td class="col-type"><span class="badge badge-other">${escapeHtml(e.topic_type||e.type||'—')}</span></td>`;
        html += `<td class="col-title">${escapeHtml(e.title||e.id)}</td>`;
        html += `<td class="col-tldr"><span class="tldr-trunc">${escapeHtml(tldr).substring(0,100)}</span></td>`;
        html += `<td class="col-links">${linksHtml}</td>`;
        html += `<td class="col-date">${escapeHtml(e.date||'—')}</td>`;
        html += '</tr>';
        // expandable detail row
        html += `<tr class="wiki-detail" id="detail-${escapeHtml(e.id)}" style="display:none">`;
        html += `<td colspan="6"><div class="detail-card">`;
        html += `<p><strong>TL;DR</strong> ${escapeHtml(tldr)}</p>`;
        const summaryText = (e.summary && e.summary.text) || '';
        if (summaryText) {
          html += `<div class="summary-block">${escapeHtml(summaryText).split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>`<p>${p}</p>`).join('')}</div>`;
        }
        // group links by domain
        const domainLinks = {};
        for (const l of (e.links||[])) {
          try { const u = new URL(l.url); const d = u.hostname.replace('www.','');
            if (!domainLinks[d]) domainLinks[d] = [];
            domainLinks[d].push(l);
          } catch(_) { if (!domainLinks['other']) domainLinks['other'] = []; domainLinks['other'].push(l); }
        }
        if (Object.keys(domainLinks).length) {
          let lh = '<p><strong>Links</strong><br>';
          for (const [d, ls] of Object.entries(domainLinks)) {
            lh += `<span class="link-domain">${escapeHtml(d)}</span> `;
            lh += ls.map(l=>linkBadge(l)).join('') + '<br>';
          }
          lh += '</p>';
          html += lh;
        }
        if ((e.tags||[]).length) html += `<p><strong>Tags</strong> ${e.tags.map(t=>`<span class="badge badge-tag">${escapeHtml(t)}</span>`).join(' ')}</p>`;
        if (e.entities) {
          const entBits = [];
          for (const [k,v] of Object.entries(e.entities)) if (v.length) entBits.push(`${k}: ${v.join(', ')}`);
          if (entBits.length) html += `<p><strong>Entities</strong> ${entBits.join(' · ')}</p>`;
        }
        if (e.source && e.source.direct_source) {
          const ds = String(e.source.direct_source);
          html += `<p><strong>Source</strong> <a href="${escapeHtml(ds)}" target="_blank" rel="noopener">${escapeHtml(ds.substring(0,80))}</a></p>`;
        }
        // v3.4: related entries from relations table
        if ((e._related||[]).length) {
          html += '<p><strong>Related</strong> ';
          html += e._related.map(r =>
            `<span class="badge badge-tag" style="cursor:pointer" data-relid="${escapeHtml(r.id)}">${escapeHtml(r.id)}</span>`
          ).join(' ');
          html += '</p>';
        }
        // v3.5: deep-dive button / link
        if (e.has_dive) {
          html += `<p><a class="dive-btn" href="/site/dive.html?id=${encodeURIComponent(e.id)}">🔍 查看深度解读</a></p>`;
        } else if (e.has_record) {
          html += `<p><button class="dive-btn" data-diveid="${escapeHtml(e.id)}">🔍 深度解读</button> <span class="dive-status muted" data-divestatus="${escapeHtml(e.id)}"></span></p>`;
        }
        html += `<p><a href="/site/raw.html?id=${encodeURIComponent(e.id)}">📁 Raw materials</a></p>`;
        html += '</div></td></tr>';
      }
      html += '</tbody></table></div></div>';
    }
    container.innerHTML = html || '<p class="empty">No matches</p>';

    // collapse toggle
    container.querySelectorAll('.month-header').forEach(h => {
      h.addEventListener('click', () => {
        const g = h.parentElement;
        g.classList.toggle('collapsed');
        h.textContent = h.textContent.replace('▸','▾').replace('▾', g.classList.contains('collapsed') ? '▸' : '▾');
      });
    });

    // row expansion
    container.querySelectorAll('.row-toggle').forEach(a => {
      a.addEventListener('click', (ev) => {
        ev.preventDefault();
        const row = a.closest('tr');
        const detail = document.getElementById('detail-' + row.dataset.id);
        if (detail) detail.style.display = detail.style.display === 'none' ? '' : 'none';
      });
    });

    // v3.5: dive trigger — POST /api/dive, poll status, graceful CLI fallback
    container.querySelectorAll('[data-diveid]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = btn.dataset.diveid;
        const statusEl = container.querySelector(`[data-divestatus="${id}"]`);
        const cliCmd = `python skills/wiki-curation/scripts/cli.py --json dive --id ${id}`;
        const showFallback = (prefix) => {
          if (statusEl) statusEl.innerHTML = `${prefix || ''}请在终端执行：<code>${escapeHtml(cliCmd)}</code>`;
          btn.disabled = false;
        };
        btn.disabled = true;
        try {
          const res = await fetch('/api/dive', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
          });
          if (res.status === 409) { showFallback('解读已存在或进行中。'); return; }
          if (!res.ok) throw new Error('HTTP ' + res.status);
          if (statusEl) statusEl.textContent = '已发起解读，正在采集材料…';
          const t0 = Date.now();
          const timer = setInterval(async () => {
            try {
              const st = await (await fetch('/api/dive/status?id=' + encodeURIComponent(id))).json();
              if (st.has_dive) {
                clearInterval(timer);
                const a = document.createElement('a');
                a.className = 'dive-btn';
                a.href = '/site/dive.html?id=' + encodeURIComponent(id);
                a.textContent = '🔍 查看深度解读';
                btn.replaceWith(a);
                if (statusEl) statusEl.textContent = '';
                return;
              }
              const state = st.status && st.status.state;
              if (state === 'awaiting_agent') { clearInterval(timer); showFallback('材料已就绪，等待 agent 执行。'); return; }
              if (state === 'failed') { clearInterval(timer); showFallback('采集失败。'); return; }
              if (Date.now() - t0 > 120000) { clearInterval(timer); showFallback('仍在进行，可稍后刷新查看。'); }
            } catch (_) { /* 单次轮询失败忽略 */ }
          }, 5000);
        } catch (err) {
          showFallback('本地服务不支持在线发起。');
        }
      });
    });

    // v3.4: related-entry badges — scroll to and expand the target row
    container.querySelectorAll('[data-relid]').forEach(badge => {
      badge.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const targetId = badge.dataset.relid;
        const targetRow = container.querySelector(`tr[data-id="${targetId}"]`);
        if (targetRow) {
          // expand the target's detail
          const detail = document.getElementById('detail-' + targetId);
          if (detail) detail.style.display = '';
          // open collapsed month if needed
          const monthGroup = targetRow.closest('.month-group');
          if (monthGroup && monthGroup.classList.contains('collapsed')) {
            monthGroup.classList.remove('collapsed');
            const h = monthGroup.querySelector('.month-header');
            if (h) h.textContent = h.textContent.replace('▸', '▾');
          }
          targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    });
  }

  searchInput.addEventListener('input', render);
  sel.addEventListener('change', render);
  statusSel.addEventListener('change', render);
  render();

  // ============ v3.4: Trends 视图 ============
  const trendList = document.getElementById('trend-list');
  if (trends.length) {
    trendList.innerHTML = trends.map(t => `
      <div class="trend-card" data-slug="${escapeHtml(t.slug)}">
        <h3>${escapeHtml(t.title)}</h3>
        <div class="trend-meta">${escapeHtml(t.date || '')}</div>
        <p class="muted">${escapeHtml(t.excerpt || '')}</p>
      </div>
    `).join('');
  } else {
    trendList.innerHTML = '<p class="empty">No trend articles yet</p>';
  }

  document.getElementById('nav-records').addEventListener('click', () => switchView('records'));
  document.getElementById('nav-trends').addEventListener('click', () => switchView('trends'));

  function switchView(view) {
    document.getElementById('nav-records').classList.toggle('active', view === 'records');
    document.getElementById('nav-trends').classList.toggle('active', view === 'trends');
    document.getElementById('records-view').style.display = view === 'records' ? '' : 'none';
    document.getElementById('trends-view').style.display = view === 'trends' ? '' : 'none';
  }

  trendList.querySelectorAll('.trend-card').forEach(card => {
    card.addEventListener('click', () => openTrend(card.dataset.slug));
  });

  async function openTrend(slug) {
    const t = (trends || []).find(x => x.slug === slug);
    if (!t) return;
    document.getElementById('trend-list').style.display = 'none';
    document.getElementById('trend-article').style.display = '';
    const body = document.getElementById('trend-body');
    body.innerHTML = '<p class="muted">Loading...</p>';
    try {
      const md = await (await fetch('/' + t.file)).text();
      body.innerHTML = window.marked ? marked.parse(md) : `<pre>${escapeHtml(md)}</pre>`;
    } catch (e) {
      body.innerHTML = `<p class="muted">Load failed: ${escapeHtml(e.message)}</p>`;
    }
    window.scrollTo(0, 0);
  }

  document.getElementById('trend-back').addEventListener('click', () => {
    document.getElementById('trend-article').style.display = 'none';
    document.getElementById('trend-list').style.display = '';
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
