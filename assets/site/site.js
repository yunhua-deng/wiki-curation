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

// v3.7: render **bold** mini-headings inside summary paragraphs (already HTML-escaped)
function renderBold(escapedText) {
  return escapedText.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
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

// v3.6: survey column cell — view link (_blank) / state icon / ghost trigger
function surveyCell(e) {
  const id = escapeHtml(e.id);
  if (e.has_survey) {
    return `<a class="survey-cell" href="/site/survey.html?id=${encodeURIComponent(e.id)}" target="_blank" rel="noopener" title="查看综述">🧭</a>`;
  }
  const st = e.survey_state;
  if (st === 'collecting') return '<span class="survey-cell-state" title="综述：采集中">⏳</span>';
  if (st === 'writing') return '<span class="survey-cell-state" title="综述：写作中">✍️</span>';
  if (st === 'awaiting_agent') return '<span class="survey-cell-state" title="综述：排队中（agent 待执行）">🕐</span>';
  if (st === 'failed') return '<span class="survey-cell-state" title="综述：失败">⚠️</span>';
  if (e.has_record) return `<button class="survey-cell survey-cell-ghost" data-surveyid="${id}" title="发起综述">🧭</button>`;
  return '';
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
  // v3.5：支持 /site/?q=<kw> 预填搜索（survey.html 的 "View record" 回跳用）
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
        html += `<td class="col-survey">${surveyCell(e)}</td>`;
        html += `<td class="col-date">${escapeHtml(e.date||'—')}</td>`;
        html += '</tr>';
        // expandable detail row
        html += `<tr class="wiki-detail" id="detail-${escapeHtml(e.id)}" style="display:none">`;
        html += `<td colspan="7"><div class="detail-card">`;
        html += `<p><strong>TL;DR</strong> ${escapeHtml(tldr)}</p>`;
        const summaryText = (e.summary && e.summary.text) || '';
        if (summaryText) {
          html += `<div class="summary-block">${renderBold(escapeHtml(summaryText)).split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>`<p>${p}</p>`).join('')}</div>`;
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
        // v3.7: manual add-link (records only) — POST /api/record-links
        if (e.has_record) {
          html += `<p class="link-add" data-linkadd="${escapeHtml(e.id)}"><button class="link-add-toggle" title="把新发现的链接加入该记录的链接图谱">＋ 添加链接</button></p>`;
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
        // v3.4/v3.7: related entries as list (id + title, click-to-scroll)
        if ((e._related||[]).length) {
          html += '<p class="related-head"><strong>Related</strong></p>';
          html += '<ul class="related-list">';
          html += e._related.map(r =>
            `<li><span class="rel-id" data-relid="${escapeHtml(r.id)}" title="跳转展开">${escapeHtml(r.id)}</span>` +
            (r.title ? ` <span class="rel-title">${escapeHtml(r.title)}</span>` : '') +
            ` <span class="rel-score muted">${r.score}</span></li>`
          ).join('');
          html += '</ul>';
        }
        // v3.7: initiation preview (add-time recall list with reasons)
        const pv = e.preview && e.preview.recall;
        if (pv && (pv.matches||[]).length) {
          html += `<details class="preview-recall"><summary>🔁 发起时召回（${pv.matches.length}）· ${escapeHtml((pv.added_at||'').slice(0,10))}</summary>`;
          html += '<ul class="related-list">';
          for (const m of pv.matches) {
            const reasons = (m.reasons||[]).slice(0,2).map(x=>`${x.kind}: ${x.detail}`).join('; ');
            html += `<li><span class="rel-id" data-relid="${escapeHtml(m.id)}" title="跳转展开">${escapeHtml(m.id)}</span>` +
                    (m.title ? ` <span class="rel-title">${escapeHtml(m.title)}</span>` : '') +
                    ` <span class="rel-score muted">${m.score}</span>` +
                    (reasons ? `<div class="rel-reasons muted">${escapeHtml(reasons)}</div>` : '') +
                    `</li>`;
          }
          html += '</ul></details>';
        }
        // v3.5: survey（综述）button / link — v3.6: 新开 tab
        if (e.has_survey) {
          html += `<p><a class="survey-btn" href="/site/survey.html?id=${encodeURIComponent(e.id)}" target="_blank" rel="noopener">🧭 查看综述</a></p>`;
        } else if (e.has_record) {
          html += `<p><button class="survey-btn" data-surveyid="${escapeHtml(e.id)}">🧭 综述</button> <span class="survey-status muted" data-surveystatus="${escapeHtml(e.id)}"></span></p>`;
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

    // v3.5: survey trigger — POST /api/survey, poll status, graceful CLI fallback
    // v3.6: works from both column ghost button (inline cell feedback) and detail card button
    container.querySelectorAll('[data-surveyid]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = btn.dataset.surveyid;
        const cell = btn.closest('td.col-survey');
        const span = container.querySelector(`[data-surveystatus="${id}"]`);
        const cliCmd = `python skills/wiki-curation/scripts/cli.py --json survey --id ${id}`;
        const setMsg = (msgHtml) => {
          if (cell) cell.innerHTML = msgHtml;
          else if (span) span.innerHTML = msgHtml;
        };
        const showFallback = (prefix) => {
          setMsg(`${prefix || ''}请在终端执行：<code>${escapeHtml(cliCmd)}</code>`);
          if (!cell) btn.disabled = false;
        };
        const finish = () => {
          const href = '/site/survey.html?id=' + encodeURIComponent(id);
          if (cell) { cell.innerHTML = `<a class="survey-cell" href="${href}" target="_blank" rel="noopener" title="查看综述">🧭</a>`; return; }
          const a = document.createElement('a');
          a.className = 'survey-btn';
          a.href = href; a.target = '_blank'; a.rel = 'noopener';
          a.textContent = '🧭 查看综述';
          btn.replaceWith(a);
          if (span) span.textContent = '';
        };
        btn.disabled = true;
        try {
          const res = await fetch('/api/survey', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
          });
          if (res.status === 409) { showFallback('综述已存在或进行中。'); return; }
          if (!res.ok) throw new Error('HTTP ' + res.status);
          setMsg(cell ? '<span title="采集中">⏳</span>' : '已发起综述，正在采集材料…');
          const t0 = Date.now();
          const timer = setInterval(async () => {
            try {
              const st = await (await fetch('/api/survey/status?id=' + encodeURIComponent(id))).json();
              if (st.has_survey) { clearInterval(timer); finish(); return; }
              const state = st.status && st.status.state;
              if (state === 'writing') { setMsg(cell ? '<span title="写作中">✍️</span>' : '材料就绪，正在写作…'); }
              else if (state === 'awaiting_agent') { clearInterval(timer); showFallback('材料已就绪，等待 agent 执行。'); return; }
              else if (state === 'failed') { clearInterval(timer); showFallback('处理失败。'); return; }
              if (Date.now() - t0 > 600000) { clearInterval(timer); showFallback('仍在进行，可稍后刷新查看。'); }
            } catch (_) { /* 单次轮询失败忽略 */ }
          }, 5000);
        } catch (err) {
          showFallback('当前 wiki 服务不含在线发起接口（若刚升级，请重启 site --serve）。');
        }
      });
    });

    // v3.7: manual add-link — inline form, POST /api/record-links, optional survey regen
    container.querySelectorAll('[data-linkadd]').forEach(wrap => {
      const id = wrap.dataset.linkadd;
      const toggle = wrap.querySelector('.link-add-toggle');
      toggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (wrap.querySelector('input')) return;
        const form = document.createElement('span');
        form.className = 'link-add-form';
        form.innerHTML = ` <input type="url" placeholder="https://…" size="44">` +
          ` <select><option value="related">related</option><option value="canonical">canonical</option></select>` +
          ` <button data-act="add">添加</button>` +
          ` <button data-act="addsurvey" title="添加链接并自动重新生成综述">添加并更新综述</button>` +
          ` <span class="link-add-status muted"></span>`;
        toggle.after(form);
        const input = form.querySelector('input');
        const statusEl = form.querySelector('.link-add-status');
        input.focus();
        const injectBadge = (url, kind) => {
          const badge = document.createElement('a');
          badge.className = 'link-badge';
          badge.href = url; badge.target = '_blank'; badge.rel = 'noopener'; badge.title = url + '（manual）';
          badge.textContent = (LINK_ICONS[kind] || LINK_ICONS.other);
          const detail = wrap.closest('.detail-card');
          const domainSpan = detail && detail.querySelector('.link-domain');
          if (domainSpan && domainSpan.parentElement) {
            domainSpan.parentElement.appendChild(badge);
          } else {
            const p = document.createElement('p');
            p.innerHTML = '<strong>Links</strong> ';
            p.appendChild(badge);
            wrap.before(p);
          }
        };
        const submit = async (updateSurvey) => {
          const url = input.value.trim();
          const role = form.querySelector('select').value;
          if (!/^https?:\/\/\S+$/.test(url)) { statusEl.textContent = ' URL 需以 http(s):// 开头'; return; }
          // 记录综述基线 revision（regen 完成 = revision 自增；区分旧 survey.md 造成的 has_survey 恒真）
          let baseRev = 0;
          if (updateSurvey) {
            try {
              const cur = await (await fetch('/api/survey/status?id=' + encodeURIComponent(id))).json();
              baseRev = (cur.survey && cur.survey.revision) || 0;
            } catch (_) {}
          }
          statusEl.textContent = ' 添加中…';
          try {
            const res = await fetch('/api/record-links', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id, url, role, update_survey: updateSurvey }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) { statusEl.textContent = ' ' + ((data && data.message) || ('失败 HTTP ' + res.status)); return; }
            injectBadge(url, (data.link && data.link.kind) || 'other');
            if (!updateSurvey) { statusEl.textContent = ' ✓ 已添加（manual，站点已重建）'; return; }
            statusEl.textContent = ' ✓ 已添加，综述更新中…';
            const t0 = Date.now();
            const timer = setInterval(async () => {
              try {
                const st = await (await fetch('/api/survey/status?id=' + encodeURIComponent(id))).json();
                const rev = (st.survey && st.survey.revision) || 0;
                const state = st.status && st.status.state;
                if (rev > baseRev && state === 'done') {
                  clearInterval(timer);
                  statusEl.innerHTML = ` ✓ 综述已更新（rev ${rev}）：<a href="/site/survey.html?id=${encodeURIComponent(id)}" target="_blank" rel="noopener">🧭 查看</a>`;
                  return;
                }
                if (state === 'failed') { clearInterval(timer); statusEl.textContent = ' 综述更新失败（链接已添加）'; return; }
                if (Date.now() - t0 > 600000) { clearInterval(timer); statusEl.textContent = ' 综述仍在更新，可稍后刷新'; }
              } catch (_) {}
            }, 5000);
          } catch (err) {
            statusEl.textContent = ' 当前 wiki 服务不支持添加链接（若刚升级，请重启 site --serve）';
          }
        };
        form.querySelector('[data-act="add"]').addEventListener('click', (e2) => { e2.stopPropagation(); submit(false); });
        form.querySelector('[data-act="addsurvey"]').addEventListener('click', (e2) => { e2.stopPropagation(); submit(true); });
        input.addEventListener('keydown', (e2) => { if (e2.key === 'Enter') { e2.stopPropagation(); submit(false); } });
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
