/**
 * Wiki Site v3.2 — compact table, inline expansion, month collapse.
 */
// v3.21: entities 视图五类分组（高校/公司/开源/产品/人物）+ 单次实体默认隐藏（组内 toggle）
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
  const [entries, tags, entityPages] = await Promise.all([
    loadJSON('/site/data/entries.json'),
    loadJSON('/site/data/tags.json').catch(() => ({})),
    loadJSON('/site/data/entity_pages.json').catch(() => ({})),
  ]);
  const entityPageMap = entityPages || {};

  // stats
  const withRec = entries.filter(e => e.has_record).length;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><b>${withRec}</b> records</div>
    <div class="stat"><b>${entries.filter(e => e.watched).length}</b> watching</div>
    <div class="stat"><b>${Object.keys(entityPageMap).length}</b> entities</div>
  `;

  // type filter
  const sel = document.getElementById('filter-type');
  [...new Set(entries.map(e => e.topic_type || e.type).filter(Boolean))].sort()
    .forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; sel.appendChild(o); });

  const searchInput = document.getElementById('search');
  const statusSel = document.getElementById('filter-status');
  const watchOnly = document.getElementById('filter-watch');
  const container = document.getElementById('table-container');
  // v3.5：支持 /site/?q=<kw> 预填搜索
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
        if ((e.tags||[]).length) html += `<p><strong>Tags</strong> ${e.tags.map(t=>`<span class="badge badge-tag">${escapeHtml(t)}</span>`).join(' ')}</p>`;
        if (e.entities) {
          const entBits = [];
          for (const [k,v] of Object.entries(e.entities)) {
            if (!v.length) continue;
            const chips = v.map(name =>
              `<span class="ent-chip" data-entname="${escapeHtml(name)}" title="点击跳转到实体页">${escapeHtml(name)}</span>`
            ).join(' ');
            entBits.push(`${k}: ${chips}`);
          }
          if (entBits.length) html += `<p><strong>Entities</strong> <span class="muted ent-hint">（点击名字跳转到实体页）</span> ${entBits.join(' · ')}</p>`;
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



    // v3.20: entity chip → 跳转实体页（新 tab，实体详情以弹出卡片打开）
    container.querySelectorAll('.ent-chip').forEach(chip => {
      chip.addEventListener('click', (ev) => {
        ev.stopPropagation();
        window.open('/site/?v=entities&e=' + encodeURIComponent(chip.dataset.entname), '_blank');
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
  if (watchOnly) watchOnly.addEventListener('change', render);
  render();

  // --- entities view：五组分区 + 低频（record_count==1）默认隐藏；搜索覆盖全部实体 ---
  function renderEntities(pages) {
    const list = document.getElementById('entities-list');
    const items = Object.values(pages || {});
    const searchEl = document.getElementById('ent-search');
    const watchOnly = document.getElementById('ent-filter-watch');
    if (!items.length) { list.innerHTML = '<p class="empty">No entities yet</p>'; return; }

    // 五组分区（顺序固定）；groups 由后端 entity_pages.json 给出（列表，允许重叠），缺失时按 type 兜底
    const GROUPS = [
      ['academia', '高校与研究机构'],
      ['company', '科技公司'],
      ['oss', '开源项目'],
      ['product', '商业产品'],
      ['person', '人物'],
    ];
    const groupsOf = (p) => (Array.isArray(p.groups) && p.groups.length) ? p.groups :
      [p.group || (p.type === 'author' ? 'person' : p.type === 'company' ? 'company' : 'product')];
    const expanded = {}; // group key -> 低频实体是否展开

    function cardHtml(p) {
      return `<div class="tracking-card entity-card" data-slug="${escapeHtml(p.slug)}">
        <h3>${p.watched ? '★ ' : ''}${escapeHtml(p.name)}
          ${p.summary ? `<a class="doc-link" href="/site/doc.html?kind=entity&slug=${encodeURIComponent(p.slug)}" target="_blank" rel="noopener" title="摘要独立页（新 tab）">🔗</a>` : ''}
        </h3>
        <div class="trend-meta">${escapeHtml(p.type)} · ${p.record_count} records${p.summary ? ' · 📝 摘要' : ''}</div>
      </div>`;
    }

    function bindCards() {
      list.querySelectorAll('.entity-card').forEach(card => {
        card.addEventListener('click', (ev) => {
          if (ev.target.closest('a')) return;
          renderEntityDetail(pages[card.dataset.slug], pages);
        });
      });
    }

    function renderList() {
      const q = (searchEl.value || '').toLowerCase().trim();
      const w = watchOnly.checked;
      if (q || w) {
        // 搜索/筛选：覆盖全部实体（含默认隐藏的低频实体）
        const matched = items.filter(p =>
          (!q || p.name.toLowerCase().includes(q)) && (!w || p.watched))
          .sort((a, b) => b.record_count - a.record_count);
        if (!matched.length) { list.innerHTML = '<p class="empty">No matching entities</p>'; return; }
        const shown = matched.slice(0, 100);
        list.innerHTML = shown.map(cardHtml).join('') +
          `<p class="muted ent-total">共 ${matched.length} 个匹配${matched.length > 100 ? '，显示前 100' : ''}</p>`;
      } else {
        // 默认：五组分区，一个实体在其所属的每个分组里都渲染一张卡片；
        // 组内 watched 置顶 + record_count 降序；低频（仅 1 次）默认隐藏（按组成员独立计算）
        let html = '';
        for (const [g, label] of GROUPS) {
          const inGroup = items.filter(p => groupsOf(p).includes(g));
          if (!inGroup.length) continue;
          const byCount = (a, b) => b.record_count - a.record_count;
          const watched = inGroup.filter(p => p.watched).sort(byCount);
          const rest = inGroup.filter(p => !p.watched).sort(byCount);
          const frequent = rest.filter(p => p.record_count > 1);
          const rare = rest.filter(p => p.record_count === 1);
          html += `<h3 class="ent-section">${label}（${inGroup.length}）</h3>`;
          html += watched.concat(frequent).map(cardHtml).join('');
          if (rare.length) {
            if (expanded[g]) {
              html += rare.map(cardHtml).join('') +
                `<p><button class="ent-toggle" data-group="${g}">隐藏仅出现 1 次的实体（${rare.length}）</button></p>`;
            } else {
              html += `<p><button class="ent-toggle" data-group="${g}">显示仅出现 1 次的实体（${rare.length}）</button></p>`;
            }
          }
        }
        html += `<p class="muted ent-total">共 ${items.length} 个实体，用搜索查看全部</p>`;
        list.innerHTML = html;
        list.querySelectorAll('.ent-toggle').forEach(btn => {
          btn.addEventListener('click', () => {
            expanded[btn.dataset.group] = !expanded[btn.dataset.group];
            renderList();
          });
        });
      }
      bindCards();
    }

    searchEl.addEventListener('input', renderList);
    watchOnly.addEventListener('change', renderList);
    renderList();
  }

  function renderEntityDetail(p, pages) {
    const el = document.getElementById('entity-detail');
    if (!p) { el.innerHTML = ''; return; }
    // 头部：名称 + 类型 + watched + 记录数 + 活跃区间
    const months = p.timeline.map(t => t.month).sort();
    const span = months.length ? `${months[0]} – ${months[months.length - 1]}` : '—';
    // 摘要：内联首段 + 独立页链接
    const excerpt = p.summary ? p.summary.split(/\n\s*\n/)[0].trim() : '';
    // 时间线：纯 CSS 迷你柱状图（升序：旧→新）
    const tlAsc = [...p.timeline].sort((a, b) => a.month < b.month ? -1 : 1);
    const maxC = Math.max(...tlAsc.map(t => t.count), 1);
    const bars = tlAsc.map(t =>
      `<div class="tl-bar" style="height:${Math.round(t.count / maxC * 48) + 4}px" title="${escapeHtml(t.month)}: ${t.count} records"><span class="tl-label">${escapeHtml(/^\d{4}-/.test(t.month) ? t.month.slice(2) : t.month)}</span></div>`
    ).join('');
    // 关联记录：按月份分组（≤3 组全展开，否则仅最新组展开）
    const byMonth = {};
    for (const r of p.records) { const mk = (r.date || '?').slice(0, 7); (byMonth[mk] = byMonth[mk] || []).push(r); }
    const mks = Object.keys(byMonth).sort().reverse();
    const recGroups = mks.map((mk, i) => {
      const open = mks.length <= 3 || i === 0;
      return `<details class="ent-rec-group"${open ? ' open' : ''}><summary>${escapeHtml(mk)}（${byMonth[mk].length}）</summary><ul>` +
        byMonth[mk].map(r =>
          `<li><span class="muted">${escapeHtml(r.date || '?')}</span> <a href="/site/doc.html?kind=record&id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">${escapeHtml(r.title || r.id)}</a></li>`
        ).join('') + '</ul></details>';
    }).join('');
    // 共现实体：可点击 chips，点击跳到该实体详情
    const co = p.co_entities.map(c =>
      `<span class="ent-chip co-ent" data-coname="${escapeHtml(c.name)}" title="查看该实体">${escapeHtml(c.name)} ×${c.count}</span>`
    ).join(' ');
    // canonical 链接：按域名分组 + linkBadge 图标（同 records 详情风格）
    const byDomain = {};
    for (const l of p.links) {
      try { const d = new URL(l.url).hostname.replace('www.', ''); (byDomain[d] = byDomain[d] || []).push(l); }
      catch (_) { (byDomain.other = byDomain.other || []).push(l); }
    }
    const linkBits = Object.entries(byDomain).map(([d, ls]) =>
      `<span class="link-domain">${escapeHtml(d)}</span> ` + ls.map(l => linkBadge(l)).join('')
    ).join('<br>');
    el.innerHTML = `
      <div class="ent-modal-backdrop">
        <div class="entity-detail-card ent-modal">
          <button class="ent-modal-close" title="关闭（Esc）">✕</button>
          <h2>${p.watched ? '★ ' : ''}${escapeHtml(p.name)} <span class="badge badge-other">${escapeHtml(p.type)}</span></h2>
          <p class="trend-meta">${p.record_count} records · 活跃 ${escapeHtml(span)}</p>
          ${p.summary ? `<div class="summary-block"><p>${escapeHtml(excerpt)}</p></div>
            <p><a href="/site/doc.html?kind=entity&slug=${encodeURIComponent(p.slug)}" target="_blank" rel="noopener">📝 阅读摘要全文</a></p>` : ''}
          <h4>时间线</h4>
          <div class="tl-wrap"><div class="tl-chart">${bars}</div></div>
          <h4>关联记录（${p.record_count}）</h4>${recGroups || '<p class="muted">—</p>'}
          <h4>共现实体</h4><p>${co || '<span class="muted">—</span>'}</p>
          <h4>Canonical 链接</h4><p>${linkBits || '<span class="muted">—</span>'}</p>
        </div>
      </div>`;
    // 弹卡关闭：✕ / 点击遮罩 / Esc（重渲染前清掉上一个 Esc 监听，避免累积）
    const close = () => { el.innerHTML = ''; if (el._escHandler) { document.removeEventListener('keydown', el._escHandler); el._escHandler = null; } };
    if (el._escHandler) document.removeEventListener('keydown', el._escHandler);
    el._escHandler = (ev) => { if (ev.key === 'Escape') close(); };
    document.addEventListener('keydown', el._escHandler);
    el.querySelector('.ent-modal-backdrop').addEventListener('click', (ev) => {
      if (ev.target.classList.contains('ent-modal-backdrop')) close();
    });
    el.querySelector('.ent-modal-close').addEventListener('click', close);
    el.querySelectorAll('.co-ent').forEach(chip => chip.addEventListener('click', () => {
      const target = Object.values(pages || {}).find(x => x.name === chip.dataset.coname);
      if (target) renderEntityDetail(target, pages);
    }));
  }

  renderEntities(entityPageMap);

  // --- nav（Records + Entities） ---
  document.getElementById('nav-records').addEventListener('click', () => switchView('records'));
  document.getElementById('nav-entities').addEventListener('click', () => switchView('entities'));

  function switchView(view) {
    for (const v of ['records', 'entities']) {
      const nav = document.getElementById('nav-' + v);
      if (nav) nav.classList.toggle('active', view === v);
      const viewEl = document.getElementById(v + '-view');
      if (viewEl) viewEl.style.display = view === v ? '' : 'none';
    }
  }

  const v0 = getParam('v');
  if (v0 === 'entities') switchView(v0);
  const e0 = getParam('e');
  if (v0 === 'entities' && e0) {
    // v3.20：e 参数支持 slug 或实体名（records/doc 页的 chip 跳转按名字来）
    const target = entityPageMap[e0] || Object.values(entityPageMap).find(x => x.name === e0);
    if (target) renderEntityDetail(target, entityPageMap);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
