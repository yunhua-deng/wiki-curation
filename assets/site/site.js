/**
 * Wiki Site v3.2 — compact table, inline expansion, month collapse.
 */
// v3.19: 转义引号——实体名可含双引号（如 "Data Pyramid"），属性上下文（data-coname 等）需要
function escapeHtml(text) {
  return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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

function getParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

// ================= init =================
async function init() {
  const [entries, tags] = await Promise.all([
    loadJSON('/site/data/entries.json'),
    loadJSON('/site/data/tags.json').catch(() => ({})),
  ]);

  // stats
  const withRec = entries.filter(e => e.has_record).length;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><b>${withRec}</b> records</div>
    <div class="stat"><b>${entries.filter(e => e.watched).length}</b> watching</div>
  `;

  // type filter
  const sel = document.getElementById('filter-type');
  [...new Set(entries.map(e => e.topic_type || e.type).filter(Boolean))].sort()
    .forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; sel.appendChild(o); });

  const searchInput = document.getElementById('search');
  const statusSel = document.getElementById('filter-status');
  const watchOnly = document.getElementById('filter-watch');
  const tagSel = document.getElementById('filter-tag');
  // tag filter：tags.json 已按出现次数倒序，沿用该顺序并附上计数
  for (const [name, ids] of Object.entries(tags || {})) {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = `${name} (${ids.length})`;
    tagSel.appendChild(o);
  }
  const container = document.getElementById('table-container');
  // v3.5：支持 /site/?q=<kw> 预填搜索；v3.22：支持 /site/?tag=<tag> 预选标签
  const q0 = getParam('q');
  if (q0) searchInput.value = q0;
  const tag0 = getParam('tag');
  if (tag0) tagSel.value = tag0;

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
    const tag = tagSel.value;

    let html = '';
    for (const mk of monthList) {
      const visible = months[mk].filter(e => {
        if (watchOnly && watchOnly.checked && !e.watched) return false;
        if (type && (e.topic_type || e.type) !== type) return false;
        if (tag && !(e.tags || []).includes(tag)) return false;
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
        html += `<tr class="wiki-row" data-id="${escapeHtml(e.id)}" title="点击查看详情">`;
        html += `<td class="col-watch">${watchCell(e)}</td>`;
        html += `<td class="col-id"><span class="row-toggle-id">${escapeHtml(e.id)}</span></td>`;
        html += `<td class="col-type"><span class="badge badge-other">${escapeHtml(e.topic_type||e.type||'—')}</span></td>`;
        html += `<td class="col-title">${escapeHtml(e.title||e.id)}</td>`;
        html += `<td class="col-tldr"><span class="tldr-trunc">${escapeHtml(tldr).substring(0,100)}</span></td>`;
        html += `<td class="col-links">${linksHtml}</td>`;
        html += `<td class="col-date">${escapeHtml(e.date||'—')}</td>`;
        html += '</tr>';
        // expandable detail row
        html += `<tr class="wiki-detail" id="detail-${escapeHtml(e.id)}" style="display:none">`;
        html += `<td colspan="7"><div class="detail-card">`;
        // v3.11: detail toolbar — 独立页 / 添加链接 全部置顶
        html += `<p class="detail-tools">`;
        html += `<a class="doc-link" href="/site/doc.html?kind=record&id=${encodeURIComponent(e.id)}" target="_blank" rel="noopener" title="独立页浏览（新 tab）">🔗 独立页</a>`;
        if (e.has_record) {
          html += ` <span class="tool-sep">·</span> <span class="link-add" data-linkadd="${escapeHtml(e.id)}"><button class="link-add-toggle" title="把新发现的链接加入该记录的链接图谱">＋ 添加链接</button></span>`;
        }
        html += `</p>`;
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
        if ((e.tags||[]).length) html += `<p><strong>Tags</strong> ${e.tags.map(t=>`<span class="badge badge-tag" data-tag="${escapeHtml(t)}" title="按此标签过滤">${escapeHtml(t)}</span>`).join(' ')}</p>`;
        if (e.entities) {
          const entBits = [];
          for (const [k,v] of Object.entries(e.entities)) {
            if (!v.length) continue;
            entBits.push(`${k}: ${v.map(name => escapeHtml(name)).join(', ')}`);
          }
          if (entBits.length) html += `<p><strong>Entities</strong> ${entBits.join(' · ')}</p>`;
        }
        if (e.source && e.source.direct_source) {
          const ds = String(e.source.direct_source);
          html += `<p><strong>Source</strong> <a href="${escapeHtml(ds)}" target="_blank" rel="noopener">${escapeHtml(ds.substring(0,80))}</a></p>`;
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
        html += `<p><a href="/site/raw.html?id=${encodeURIComponent(e.id)}">📁 Raw materials</a></p>`;
        html += '</div></td></tr>';
      }
      html += '</tbody></table></div></div>';
    }
    container.innerHTML = html || '<p class="empty">No matches</p>';

    // v3.22: 点击标签即按该标签过滤
    container.querySelectorAll('.badge-tag[data-tag]').forEach(b => {
      b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        tagSel.value = b.dataset.tag;
        render();
      });
    });

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

    // v3.7: manual add-link — inline form, POST /api/record-links
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
        const submit = async () => {
          const url = input.value.trim();
          const role = form.querySelector('select').value;
          if (!/^https?:\/\/\S+$/.test(url)) { statusEl.textContent = ' URL 需以 http(s):// 开头'; return; }
          statusEl.textContent = ' 添加中…';
          try {
            const res = await fetch('/api/record-links', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id, url, role }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) { statusEl.textContent = ' ' + ((data && data.message) || ('失败 HTTP ' + res.status)); return; }
            injectBadge(url, (data.link && data.link.kind) || 'other');
            statusEl.textContent = ' ✓ 已添加（manual，站点已重建）';
          } catch (err) {
            statusEl.innerHTML = ' 服务不支持在线添加，请在终端执行：<code>' +
              escapeHtml(`__WIKI_CLI_CMD__ --json add-link --id ${id} --url ${url}`) + '</code>';
          }
        };
        form.querySelector('[data-act="add"]').addEventListener('click', (e2) => { e2.stopPropagation(); submit(); });
        input.addEventListener('keydown', (e2) => { if (e2.key === 'Enter') { e2.stopPropagation(); submit(); } });
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
  } // end render

  searchInput.addEventListener('input', render);
  sel.addEventListener('change', render);
  statusSel.addEventListener('change', render);
  tagSel.addEventListener('change', render);
  if (watchOnly) watchOnly.addEventListener('change', render);
  render();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
