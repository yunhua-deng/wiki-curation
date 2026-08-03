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

// v3.10: watch star cell — toggle 特别关注
function watchCell(e) {
  const on = !!e.watched;
  return `<button class="watch-star${on ? ' on' : ''}" data-watchid="${escapeHtml(e.id)}" title="${on ? '取消特别关注' : '设为特别关注'}">${on ? '★' : '☆'}</button>`;
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
  const [entries, tags, postsData, trackingData] = await Promise.all([
    loadJSON('/site/data/entries.json'),
    loadJSON('/site/data/tags.json').catch(() => ({})),
    loadJSON('/site/data/posts.json').catch(() => ({ items: [], suggestions: [] })),
    loadJSON('/site/data/tracking.json').catch(() => []),
  ]);

  // stats
  const withRec = entries.filter(e => e.has_record).length;
  const postCount = ((postsData && postsData.items) || []).length;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><b>${withRec}</b> records</div>
    <div class="stat"><b>${entries.filter(e => e.watched).length}</b> watching</div>
    <div class="stat"><b>${postCount}</b> posts</div>
  `;

  // type filter
  const sel = document.getElementById('filter-type');
  [...new Set(entries.map(e => e.topic_type || e.type).filter(Boolean))].sort()
    .forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; sel.appendChild(o); });

  const searchInput = document.getElementById('search');
  const statusSel = document.getElementById('filter-status');
  const watchOnly = document.getElementById('filter-watch');
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
        if (watchOnly && watchOnly.checked && !e.watched) return false;
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
        html += `<tr class="wiki-row" data-id="${escapeHtml(e.id)}" title="点击展开详情">`;
        html += `<td class="col-watch">${watchCell(e)}</td>`;
        html += `<td class="col-id"><span class="row-toggle-id">${escapeHtml(e.id)}</span></td>`;
        html += `<td class="col-type"><span class="badge badge-other">${escapeHtml(e.topic_type||e.type||'—')}</span></td>`;
        html += `<td class="col-title">${escapeHtml(e.title||e.id)}</td>`;
        html += `<td class="col-tldr"><span class="tldr-trunc">${escapeHtml(tldr).substring(0,100)}</span></td>`;
        html += `<td class="col-links">${linksHtml}</td>`;
        html += `<td class="col-survey">${surveyCell(e)}</td>`;
        html += `<td class="col-date">${escapeHtml(e.date||'—')}</td>`;
        html += '</tr>';
        // expandable detail row
        html += `<tr class="wiki-detail" id="detail-${escapeHtml(e.id)}" style="display:none">`;
        html += `<td colspan="8"><div class="detail-card">`;
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
          for (const [k,v] of Object.entries(e.entities)) {
            if (!v.length) continue;
            const chips = v.map(name =>
              `<span class="ent-chip" data-entname="${escapeHtml(name)}" data-entkind="${escapeHtml(k === 'author' ? 'person' : k)}" title="🎯 点击发起跟踪（tracking topic）">${escapeHtml(name)}</span>`
            ).join(' ');
            entBits.push(`${k}: ${chips}`);
          }
          if (entBits.length) html += `<p><strong>Entities</strong> <span class="muted ent-hint">（点名字可发起跟踪）</span> ${entBits.join(' · ')}</p>`;
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

    // v3.9: whole-row click toggles detail (interactive elements excluded)
    container.querySelectorAll('tr.wiki-row').forEach(row => {
      row.addEventListener('click', (ev) => {
        if (ev.target.closest('a, button, input, select, textarea, label')) return;
        const detail = document.getElementById('detail-' + row.dataset.id);
        if (detail) detail.style.display = detail.style.display === 'none' ? '' : 'none';
      });
    });

    // v3.7: entity chip → create tracking topic via POST /api/track
    container.querySelectorAll('.ent-chip').forEach(chip => {
      chip.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const name = chip.dataset.entname;
        const kind = chip.dataset.entkind || 'person';
        const oldTitle = chip.title;
        chip.classList.add('pending');
        try {
          const res = await fetch('/api/track', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, kind }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error((data && data.message) || ('HTTP ' + res.status));
          chip.classList.remove('pending');
          chip.classList.add('tracked');
          chip.title = data.exists ? `已有跟踪主题：${data.slug}` : `已创建跟踪主题：${data.slug}（digest 生成中，见 Tracking 页）`;
        } catch (err) {
          chip.classList.remove('pending');
          chip.title = '发起失败（服务不支持？请重启 site --serve）：' + err.message;
        }
      });
    });

    // v3.10: watch star toggle — POST /api/watch, optimistic with revert
    container.querySelectorAll('[data-watchid]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = btn.dataset.watchid;
        const wasOn = btn.classList.contains('on');
        btn.classList.toggle('on', !wasOn);
        btn.textContent = wasOn ? '☆' : '★';
        try {
          const res = await fetch('/api/watch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error((data && data.message) || ('HTTP ' + res.status));
          btn.classList.toggle('on', !!data.watched);
          btn.textContent = data.watched ? '★' : '☆';
          btn.title = data.watched ? '取消特别关注' : '设为特别关注';
          const ent = entries.find(x => x.id === id);
          if (ent) ent.watched = !!data.watched;
          if (watchOnly && watchOnly.checked) render();
        } catch (err) {
          btn.classList.toggle('on', wasOn);
          btn.textContent = wasOn ? '★' : '☆';
          btn.title = '关注失败（服务不支持？请重启 site --serve）：' + err.message;
        }
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
  if (watchOnly) watchOnly.addEventListener('change', render);
  render();

  // ============ v3.7: Posts 视图（trends 改造）+ Tracking 视图 ============
  const postItems = (postsData && postsData.items) || [];
  const suggestions = (postsData && postsData.suggestions) || [];
  const trackingItems = trackingData || [];

  function renderMd(container, md) {
    const html = window.marked ? marked.parse(md) : `<pre>${escapeHtml(md)}</pre>`;
    // record id → 可点击链接（跳回 records 视图搜索）
    container.innerHTML = html.replace(/(?<![\w"=/])(20\d\d-\d\d-\d\d_[A-Za-z0-9_-]+)/g,
      '<a href="/site/?q=$1" target="_blank" rel="noopener" class="rec-link">$1</a>');
  }

  function attachReader(listEl, articleEl, bodyEl, backEl, items, fileOf) {
    listEl.querySelectorAll('.post-card, .tracking-card').forEach(card => {
      card.addEventListener('click', async () => {
        const it = items.find(x => x.slug === card.dataset.slug);
        if (!it) return;
        listEl.style.display = 'none';
        const sg = document.getElementById('post-suggest');
        if (sg) sg.style.display = 'none';
        articleEl.style.display = '';
        bodyEl.innerHTML = '<p class="muted">Loading...</p>';
        try {
          const md = await (await fetch('/' + fileOf(it))).text();
          renderMd(bodyEl, md);
        } catch (e) {
          bodyEl.innerHTML = `<p class="muted">Load failed: ${escapeHtml(e.message)}</p>`;
        }
        window.scrollTo(0, 0);
      });
    });
    backEl.addEventListener('click', () => {
      articleEl.style.display = 'none';
      listEl.style.display = '';
      const sg = document.getElementById('post-suggest');
      if (sg) sg.style.display = '';
    });
  }

  // --- posts view: trigger bar + suggestions + month-grouped list ---
  const postList = document.getElementById('post-list');
  const sgBox = document.getElementById('post-suggest');
  const trigStatus = document.getElementById('post-trigger-status');

  async function startPost(payload) {
    if (trigStatus) trigStatus.textContent = ' ⏳ 已发起，写作中（约 2-4 分钟）…';
    try {
      const res = await fetch('/api/post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        if (trigStatus) trigStatus.textContent = ' ' + ((data && data.message) || ('失败 HTTP ' + res.status));
        return;
      }
      if (data.exists) {
        if (trigStatus) trigStatus.textContent = ` 已存在同主题 post：${data.stem}`;
        return;
      }
      const before = postItems.length;
      const t0 = Date.now();
      const timer = setInterval(async () => {
        try {
          const fresh = await loadJSON('/site/data/posts.json?ts=' + Date.now()).catch(() => null);
          const items = (fresh && fresh.items) || [];
          if (items.length > before) {
            clearInterval(timer);
            if (trigStatus) trigStatus.textContent = ' ✓ 已完成（已刷新列表）';
            renderPosts(items);
            return;
          }
          if (Date.now() - t0 > 480000) {
            clearInterval(timer);
            if (trigStatus) trigStatus.textContent = ' 仍在写作，请稍后手动刷新页面';
          }
        } catch (_) {}
      }, 15000);
    } catch (err) {
      if (trigStatus) trigStatus.textContent = ' 当前 wiki 服务不支持在线发起（请重启 site --serve），或改用 CLI/agent';
    }
  }

  const topicInput = document.getElementById('post-topic-input');
  const topicBtn = document.getElementById('post-topic-btn');
  if (topicBtn && topicInput) {
    topicBtn.addEventListener('click', () => {
      const t = (topicInput.value || '').trim();
      if (!t) { if (trigStatus) trigStatus.textContent = ' 请先输入主题'; return; }
      startPost({ topic: t });
    });
    topicInput.addEventListener('keydown', (e2) => { if (e2.key === 'Enter') topicBtn.click(); });
  }

  const freshSuggestions = suggestions.filter(s => !s.covered);
  if (freshSuggestions.length) {
    sgBox.innerHTML = '<h3 class="suggest-head">💡 分析建议（高关联记录）</h3>' + freshSuggestions.map((s, i) => `
      <div class="suggest-card">
        <div class="suggest-title">${escapeHtml(s.title || s.anchor)}</div>
        <div class="muted suggest-meta">${escapeHtml(s.anchor)} · ${s.degree} 条关联 · score ${s.score}</div>
        <div class="suggest-actions">
          <button class="suggest-go" data-sg="${i}">✍️ 一键写作</button>
          <button class="suggest-dismiss" data-sg="${i}" title="我不需要这个分析建议">✕ 忽略</button>
          <code class="suggest-cmd" title="点击复制">${escapeHtml(s.suggested_cmd)}</code>
        </div>
      </div>
    `).join('');
    sgBox.querySelectorAll('.suggest-cmd').forEach(c => {
      c.addEventListener('click', () => {
        navigator.clipboard && navigator.clipboard.writeText(c.textContent);
        c.classList.add('copied');
        setTimeout(() => c.classList.remove('copied'), 800);
      });
    });
    sgBox.querySelectorAll('.suggest-go').forEach(b => {
      b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const s = freshSuggestions[Number(b.dataset.sg)];
        b.disabled = true;
        startPost({ records: s.records });
      });
    });
    sgBox.querySelectorAll('.suggest-dismiss').forEach(b => {
      b.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const s = freshSuggestions[Number(b.dataset.sg)];
        b.disabled = true;
        try {
          const res = await fetch('/api/post-ignore', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ anchor: s.anchor }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) { b.disabled = false; b.title = '忽略失败：' + ((data && data.message) || res.status); return; }
          b.closest('.suggest-card').remove();
        } catch (err) {
          b.disabled = false;
          b.title = '忽略失败（服务不支持？请重启 site --serve）';
        }
      });
    });
  } else {
    sgBox.style.display = 'none';
  }

  function renderPosts(items) {
    if (!items.length) { postList.innerHTML = '<p class="empty">No posts yet</p>'; return; }
    const groups = {};
    for (const p of items) {
      const mk = (p.date || 'unknown').slice(0, 7) || 'unknown';
      (groups[mk] = groups[mk] || []).push(p);
    }
    const mks = Object.keys(groups).sort().reverse();
    postList.innerHTML = mks.map(mk => {
      const gid = `pm-${mk.replace('-', '')}`;
      return `<div class="month-group" data-month="${mk}">
        <h3 class="month-header" data-target="${gid}">${mk} · ${groups[mk].length} posts ▾</h3>
        <div class="month-body" id="${gid}">
        ${groups[mk].map(t => `
          <div class="post-card" data-slug="${escapeHtml(t.slug)}">
            <h3>${escapeHtml(t.title)} <a class="doc-link" href="/site/doc.html?kind=post&slug=${encodeURIComponent(t.slug)}" target="_blank" rel="noopener" title="独立页面（新 tab）">🔗</a></h3>
            <div class="trend-meta">${escapeHtml(t.date || '')}${t.trigger ? ' · ' + escapeHtml(t.trigger) : ''}</div>
            <p class="muted">${escapeHtml(t.excerpt || '')}</p>
          </div>`).join('')}
        </div>
      </div>`;
    }).join('');
    postList.querySelectorAll('.month-header').forEach(h => {
      h.addEventListener('click', () => {
        const g = h.parentElement;
        g.classList.toggle('collapsed');
        h.textContent = h.textContent.replace('▸', '▾').replace('▾', g.classList.contains('collapsed') ? '▸' : '▾');
      });
    });
    bindPostCards(items);
  }

  function bindPostCards(items) {
    postList.querySelectorAll('.doc-link').forEach(a => {
      a.addEventListener('click', (ev) => ev.stopPropagation());
    });
    postList.querySelectorAll('.post-card').forEach(card => {
      card.addEventListener('click', async () => {
        const it = items.find(x => x.slug === card.dataset.slug);
        if (!it) return;
        postList.style.display = 'none';
        sgBox.style.display = 'none';
        const tb = document.querySelector('.post-trigger');
        if (tb) tb.style.display = 'none';
        const articleEl = document.getElementById('post-article');
        const bodyEl = document.getElementById('post-body');
        articleEl.style.display = '';
        bodyEl.innerHTML = '<p class="muted">Loading...</p>';
        try {
          const md = await (await fetch('/' + it.file)).text();
          renderMd(bodyEl, md);
        } catch (e) {
          bodyEl.innerHTML = `<p class="muted">Load failed: ${escapeHtml(e.message)}</p>`;
        }
        window.scrollTo(0, 0);
      });
    });
  }

  renderPosts(postItems);

  document.getElementById('post-back').addEventListener('click', () => {
    document.getElementById('post-article').style.display = 'none';
    postList.style.display = '';
    sgBox.style.display = '';
    const tb = document.querySelector('.post-trigger');
    if (tb) tb.style.display = '';
  });

  // --- tracking list ---
  const trackingList = document.getElementById('tracking-list');
  if (trackingItems.length) {
    trackingList.innerHTML = trackingItems.map(t => {
      const due = t.next_due && t.next_due <= new Date().toISOString().slice(0, 10);
      return `
      <div class="tracking-card" data-slug="${escapeHtml(t.slug)}">
        <h3>🎯 ${escapeHtml(t.name)} <a class="doc-link" href="/site/doc.html?kind=tracking&slug=${encodeURIComponent(t.slug)}" target="_blank" rel="noopener" title="独立页面（新 tab）">🔗</a></h3>
        <div class="trend-meta">${escapeHtml(t.kind)} · ${t.record_count} records · 上次刷新 ${escapeHtml(t.last_at || '—')}${due ? ' · <span class="badge badge-pending">到期</span>' : ''}${t.has_digest ? '' : ' · <span class="badge badge-running">生成中</span>'}</div>
        <p class="muted">${escapeHtml(t.excerpt || '')}</p>
      </div>`;
    }).join('');
  } else {
    trackingList.innerHTML = '<p class="empty">No tracking topics yet</p>';
  }
  attachReader(trackingList, document.getElementById('tracking-article'),
               document.getElementById('tracking-body'), document.getElementById('tracking-back'),
               trackingItems, it => `tracking/${it.slug}/digest.md`);

  // --- nav ---
  document.getElementById('nav-records').addEventListener('click', () => switchView('records'));
  document.getElementById('nav-posts').addEventListener('click', () => switchView('posts'));
  document.getElementById('nav-tracking').addEventListener('click', () => switchView('tracking'));

  function switchView(view) {
    for (const v of ['records', 'posts', 'tracking']) {
      document.getElementById('nav-' + v).classList.toggle('active', view === v);
      document.getElementById(v + '-view').style.display = view === v ? '' : 'none';
    }
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
