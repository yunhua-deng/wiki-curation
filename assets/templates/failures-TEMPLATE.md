# wiki/failures/<YYYY-MM-DD>_<NNN>_<short-kebab-slug>.md

---

## 元信息

| 字段 | 值 |
|------|-----|
| **Issue ID** | `<YYYY-MM-DD>_<NNN>` |
| **标题** | 一句话概括问题 |
| **状态** | 🔴 open / 🟡 in_progress / 🟢 fixed / ⚪ wontfix |
| **优先级** | P0 / P1 / P2 |
| **发现时间** | YYYY-MM-DD HH:MM |
| **涉及组件** | `scripts/...` |
| **涉及工具 / API** | e.g. `opencli weixin download`, `hf-papers`, `github-code` |
| **影响范围** | 所有 xxx 类型的 wiki 解读任务 |

---

## 问题现象

1. **现象 1**：...
2. **现象 2**：...
3. **现象 3**：...

---

## 线索证据

### 证据 1：...

```text
# 目录 / 日志 / 命令输出
```

→ 推断：...

### 证据 2：...

```json
// _fetch_results.json / _drill_log.json / audit 片段
```

→ 推断：...

---

## 根因分析

```text
问题 A ──→ 问题 B ──→ 问题 C
```

### 根因 A：...

- ...

### 根因 B：...

- ...

---

## 修复方案

### 方案 1：...（P0）

**文件**: `scripts/...`

```python
# 修复前
...

# 修复后
...
```

**验证方式**：
1. ...
2. ...

### 方案 2：...（P1）

...

---

## 验证记录

| 验证项 | 状态 | 时间 | 备注 |
|--------|------|------|------|
| 根因确认 | ⬜ | — | 待验证 |
| 方案 1 实施 | ⬜ | — | 待实施 |
| 回归测试 | ⬜ | — | 待实施 |

---

## 相关引用

- 涉及 wiki 条目：`YYYY-MM-DD_xxxx`
- 涉及 audit 文件：`wiki/artifacts/YYYY-MM-DD_xxxx/audit/...`
- 相关代码：`scripts/...`

---

## 备注

- 此 Issue 为 ... 类型故障
- 修复后请把 **状态** 改为 `🟢 fixed` 并补全验证记录
- **每次创建或更新本文件时，必须同步更新 `wiki/failures/MANIFEST.json`**：
  - 新建时追加轻量条目（id / slug / file / status / priority / title / component / discovered_at）。
  - 修复后更新 `status`、`fixed_at` 和 `stats`。
- `MANIFEST.json` 只存清单，所有细节保留在本 `.md` 文件中。
