// node_verify_site.js — 用真实 server + 真实 site.js + 真实数据闭环验证渲染
// 模拟浏览器：fetch site.js → 在最小 DOM shim 里执行 init() → 断言表格行渲染
const fs = require('fs');
const vm = require('vm');

const BASE = process.argv[2] || 'http://localhost:8123';

function makeEl(id) {
  return {
    id, innerHTML: '', value: '', style: {},
    appendChild(c) { this.children = (this.children || []); this.children.push(c); },
    addEventListener() {},
    querySelectorAll() { return []; },
    insertAdjacentHTML() {},
    classList: { toggle() {}, add() {}, remove() {} },
    closest() { return null; },
    dataset: {},
  };
}

async function main() {
  const els = {};
  const ids = ['stats', 'filter-type', 'search', 'filter-status', 'table-container'];
  for (const id of ids) els[id] = makeEl(id);

  const document = {
    readyState: 'complete',
    getElementById: (id) => els[id] || (els[id] = makeEl(id)),
    createElement: () => makeEl('option'),
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const window = { document, location: { search: '', href: BASE + '/site/' } };

  const context = vm.createContext({
    window, document,
    // 模拟浏览器 URL 解析：相对/绝对路径都基于站点 origin
    fetch: (input, ...rest) => {
      const url = new URL(input, BASE + '/site/');
      return fetch(url.href, ...rest);
    },
    URL, URLSearchParams,
    console, setTimeout, Promise,
  });

  const js = await (await fetch(`${BASE}/site/assets/site.js`)).text();
  vm.runInContext(js, context, { filename: 'site.js' });

  // init() 立即执行（readyState=complete 分支）；等内部 fetch 全部完成
  await new Promise((r) => setTimeout(r, 6000));

  const html = els['table-container'].innerHTML;
  const rowCount = (html.match(/wiki-row/g) || []).length;
  const monthCount = (html.match(/month-group/g) || []).length;
  const stats = els['stats'].innerHTML;
  const typeOptions = (els['filter-type'].children || []).length;

  console.log(JSON.stringify({
    stats: stats.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
    typeOptions,
    monthCount,
    rowCount,
    htmlBytes: html.length,
    sampleRow: (html.match(/<tr class="wiki-row"[^>]*>.{0,160}/s) || [''])[0].replace(/\s+/g, ' ').slice(0, 220),
  }, null, 2));

  if (rowCount === 0) { console.error('FAIL: no rows rendered'); process.exit(1); }
  console.log('PASS: table rendered with', rowCount, 'rows');
}

main().catch((e) => { console.error('ERROR:', e.message); process.exit(2); });
