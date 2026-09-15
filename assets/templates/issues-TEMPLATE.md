# wiki/docs/issues/<YYYY-MM-DD>_<NNN>_<short-kebab-slug>.md

---

## 元信息

| 字段 | 值 |
|------|-----|
| **Issue ID** | `<YYYY-MM-DD>_<NNN>` |
| **kind** | `bug` / `feature` / `docs` / `chore` |
| **标题** | 一句话概括问题或需求 |
| **状态** | 🔴 open / 🟡 in_progress / 🟢 fixed / ⚪ wontfix |
| **优先级** | P0 / P1 / P2 |
| **发现时间** | YYYY-MM-DD HH:MM (Asia/Shanghai) |
| **目标仓库** | `D:/wiki-curation`（或 `D:/wiki-workspace` / 插件仓库） |
| **涉及组件** | `wiki-curation/scripts/...` |
| **涉及工具 / API** | e.g. `opencli weixin download`, `openclaw browser` |
| **影响范围** | 所有 xxx 类型的 wiki 收录/解读任务 |

---

## 一、问题

一段话说明发生了什么、为什么是问题。**只描述问题与需求，不给实现方案**（方案由修复方设计）。

### 最小复现

```bash
# 可直接复制执行的命令序列
cd D:/wiki-curation
python scripts/cli.py --json add -i <url>
python scripts/cli.py --json pop --limit 1
python scripts/cli.py --json run --id <slug>
```

---

## 二、实测现象与证据

1. **现象 1**：...
2. **现象 2**：...

### 证据 1：...

```json
// _fetch_results.json / _drill_log.json / 事件流片段
```

→ 推断：...

---

## 三、需求（期望行为）

1. ...
2. ...

---

## 四、验收标准

1. ...
2. 目标仓库自带契约测试全绿（`python -m pytest scripts/ -q`、`python eval/run_eval.py --deterministic`）。

---

## 五、定位线索（仅事实，不含方案）

- `wiki-curation/scripts/...`：...

---

## 六、相关引用

- 涉及 wiki 条目：`YYYY-MM-DD_xxxx`
- 相关工单：`YYYY-MM-DD_NNN`
- 相关代码：`wiki-curation/scripts/...`

---

## 七、验证记录

| 验证项 | 状态 | 时间 | 备注 |
|--------|------|------|------|
| 根因确认 | ⬜ | — | 待验证 |
| 修复实施 | ⬜ | — | commit `<sha>` |
| 回归测试 | ⬜ | — | `pytest scripts/ -q` / `eval --deterministic` |

---

## 八、状态维护

- 新建：把本文件放到 `wiki/docs/issues/`，文件名 `<YYYY-MM-DD>_<NNN>_<slug>.md`。
- **不要手改 `wiki/docs/issues/MANIFEST.json`**：跑 `python wiki/docs/issues/regenerate_manifest.py` 重建。
- 修复后把 **状态** 改为 `🟢 fixed`，并在下面补验证记录；不要删除历史条目。
