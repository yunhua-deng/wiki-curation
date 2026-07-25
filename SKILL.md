---
name: wiki-curation
description: "Ingest URLs into structured knowledge records (link graph + TL;DR + tags + entities), recall similar historical entries. Articles are a separate skill (article-writer). Use when the user wants to interact with the knowledge base. Trigger words: wiki, knowledge, record, recall, search, 查, 检索, 召回."
---

# wiki-curation

Ingest URLs/papers/files into **structured knowledge records** (record.json with link graph, TL;DR, tags, entities), recall similar historical entries, and query/index the growing knowledge base.

## Overview

`wiki-curation` v3.1 is record-only. One tier, one path:

- **Record**: `add → pop → run → publish` → `record.json` — link graph (explicit + inferred URLs), TL;DR, tags, canonicalized entities. Every ingestion goes through this.
- **Recall**: `add` auto-surfaces similar entries; `recall --input "..."` queries anytime (4-layer scoring: url_exact → shared_link → entity → fts).
- **Query / Index**: `list`, `search`, `recall`, `stats`, `doctor`.
- **Append**: `add --append-to <slug> --input <new_url>` merges new sources into existing records.

Core principle: **extraction by agent, linking by system**. The extraction agent reads materials and writes record.json; similarity, relations, and URL verification are deterministic code.

## When to load

Load this skill when the user mentions any of the following:

- wiki, knowledge, knowledge base, produce, consume, update, aggregate, emerge
- record, recall, 召回, 知识记录
- arxiv, paper, brief, deep, article
- search wiki, query wiki, 查 wiki, 检索 wiki, 回填 wiki

Project-level routing (`AGENTS_WIKI.md`) also mandates this skill for all wiki-related work, including both producing and consuming knowledge.

## Prerequisites

- Python 3.11+
- `curl` (general HTTP downloads)
- `opencli` (optional; only for WeChat/LinkedIn handlers)
- `GITHUB_TOKEN` (optional; classic PAT + `public_repo` scope — publish 后自动标星 GitHub 仓库用。fine-grained PAT 不支持 starring API；未设置则 `star` 自动跳过)
- A consumer `wiki/` workspace (see below)

## Workspace setup

The skill expects a consumer workspace at `$WIKI_WORKSPACE` with this layout:

```
wiki/
├── artifacts/{id}/          # record.json + articles + raw/ + audit/ (id 不可变)
├── data/wiki.db             # runtime SQLite (tracked, single source of runtime state)
├── wiki.html                # 人类可读的语义索引（自动生成）
└── README.md                # human-facing workspace docs
```

Set the workspace before running commands:

```bash
export WIKI_WORKSPACE=/path/to/project/wiki
```

If omitted, the CLI defaults to `cwd/wiki`.

## Hard constraints

1. **Always use the skill CLI.** External agents must call `skills/wiki-curation/scripts/cli.py`. Do not call `exec/`, `intake/`, `publish/`, `records/`, or `store/` scripts directly.
2. **No manual writing.** Records and articles must be produced by the pipeline (`add → pop → run → publish`). Do not hand-author `record.json` or article `.md` files, and do not construct extraction/writing prompts manually.
3. **Mandatory workflow.** `run` requires a prior `add` + `pop`. Bypassing it raises `DEPRECATED_WORKFLOW` / `WORKFLOW_BYPASSED`.
4. **User confirmation before pop.** After the first `add`, agents must ask whether to start immediately or keep adding. Once the user says "start", `pop --limit 3` is allowed. Do not spawn extraction/writing agents without user approval.
5. **Do not modify task content.** When a task payload is emitted, run it as-is.
6. **Configuration is the single source of truth.** Source classification and material checklists come from `references/sources.yaml`; record constraints from `references/record_schema.json`; article output format from `assets/output_spec_*.yaml`.
7. **Runtime state is the source of truth.** `wiki/data/wiki.db` is tracked in Git. It can be cold-started with `sync --rebuild` if corrupted, but normal workflow commits should preserve it.
8. **Sub-agents must not commit or publish.** An extraction/writing sub-agent only produces `record.json` (record mode) or article `.md` + audit JSON (article mode). It must never run `git` commands or invoke `publish`. The orchestrator runs `publish` for each entry and commits all resulting changes in one batch.

## Commit strategy

After sub-agents complete their work, the orchestrator must:

1. Run `publish --id <slug>` for records (or `publish --id <slug> --depth <depth>` for articles). This updates `wiki/data/wiki.db`, `wiki/wiki.html`, and `wiki/site/`.
2. Run `star --id <slug>`（可选但推荐）：标星该记录的 canonical GitHub 仓库（`direct_source` + `role=canonical` 的 github links）。需要 `GITHUB_TOKEN`，未设置自动跳过；单个 repo 失败只告警，不阻塞提交流程。
3. Stage all related changes together:
   - New/updated `wiki/artifacts/<slug>/` directories (record.json, article `.md`, audit JSON, raw materials)
   - `wiki/data/wiki.db`
   - `wiki/site/` index files
   - `wiki/wiki.html`
4. Commit with a message such as `wiki: publish record for <slug> and refresh index`.
5. Do not leave `wiki/data/wiki.db` or `wiki/site/` changes unstaged while the artifact files are already committed.

Rationale: `publish` rewrites the runtime index. Splitting the artifact commit from the index commit creates an inconsistent state where the artifact exists but the index does not point to it.


- `add`, `pop`, `run`, and the writing agents can run in parallel for different entries.
- `pop --limit 3` is the recommended maximum batch size for local runs; do not exceed it without explicit user approval.
- `publish` is **serialized per wiki** via a file lock. Do not invoke multiple `publish` commands concurrently on the same wiki; if you hit a `BUSY` error, wait and retry.
- Entry IDs are **immutable**. The hash-based slug generated by `add` (e.g. `2026-06-30_1234`) never changes. `publish` only refreshes the human-readable `title`/`overview` and article H1; it does not rename directories, files, events, or DB keys.
- Human readers can browse semantic titles in the auto-generated `wiki/wiki.html`; each row links to the immutable `artifacts/{id}/` directory and the published article.
- Materials links inside articles must use the canonical form `[label](artifacts/{id}/raw/...)`.

## User confirmation flow

After the first `add`, the agent must ask for user intent before any `pop`/`run`:

> “已为您添加 1 个 wiki 解读任务（`...` → `brief`）。\n\n您希望：\n1. 立即启动\n2. 继续添加更多任务后一起启动\n\n请选择。”

- If the user chooses **“立即启动”**, proceed to `pop --limit 3` → `run`.
- If the user chooses **“继续添加”**, the agent enters **batch-add mode**: subsequent `add` commands only update the pending queue count; the agent keeps replying with the current queue summary but does **not** ask again.
- When the user finally says **“启动 / 开始 / 跑”**, the agent shows the full pending list and runs `pop --limit 3`. If more than 3 tasks remain, split into multiple batches and confirm each batch.
- Example batch summary:

> “当前队列共有 5 个待解读任务：\n1. a.zip → brief\n2. b.zip → brief\n...\n\n确认启动第一批 3 个吗？”


## Quick start（记录层，默认）

```bash
export WIKI_WORKSPACE=/path/to/project/wiki

# 1. Enqueue（add 会自动召回相似历史条目；--no-recall 关闭）
python skills/wiki-curation/scripts/cli.py --json add \
  --input "https://arxiv.org/abs/2101.00027"

# 2. Confirm with user and show pending queue
python skills/wiki-curation/scripts/cli.py --json list --status pending --limit 10

# 3. Pop (only after user approval)
python skills/wiki-curation/scripts/cli.py --json pop --limit 3

# 4. Generate record extraction task payload（默认 record 模式）
python skills/wiki-curation/scripts/cli.py --json run --id <slug>

# 5. Spawn the extraction agent (harness) or run the task manually (non-harness)
#    → 产物：wiki/artifacts/<slug>/record.json

# 6. Publish record（校验 record.json、links/relations 入库、刷新站点）
python skills/wiki-curation/scripts/cli.py --json publish --id <slug>

# 7. Refresh human-readable HTML index
python skills/wiki-curation/scripts/cli.py --json index
```

## Recall（召回）

```bash
# 对任意输入做四层确定性召回（url_exact → shared_link → entity → fts）
python skills/wiki-curation/scripts/cli.py --json recall --input "https://github.com/foo/bar"

# 验证条目链接可达性（懒式，publish 不做网络请求）
python skills/wiki-curation/scripts/cli.py --json verify-links --id <slug>

# 老条目 links/entities 回填（从 metadata.json，不生成 record.json）
python skills/wiki-curation/scripts/cli.py --json backfill-records
```

## Local preview lifecycle

`site --serve` starts a long-running HTTP server for short-term local verification only. Agents must observe the following lifecycle:

1. Build the site first: `python skills/wiki-curation/scripts/cli.py site`.
2. If you need a live preview, start `site --serve` with a PID file so it can be stopped reliably:
   ```bash
   python skills/wiki-curation/scripts/cli.py site --serve --pid-file wiki/.site-serve.pid
   ```
3. Stop the preview as soon as verification is done:
   ```bash
   python skills/wiki-curation/scripts/cli.py site --stop --pid-file wiki/.site-serve.pid
   ```
4. Do not leave multiple `site --serve` instances running. The CLI detects port collisions and fails fast if the default port `8123` is already in use.
5. If you must run previews concurrently, use `--port` to assign a different port, give each one its own `--pid-file`, and stop each instance when done.
6. For the historical process-leak incident and regression notes, see `wiki/failures/2026-07-08_003_site-serve-process-leak.md`.

## CLI reference

All commands support `--json` and `--quiet`. `--workspace PATH` overrides `$WIKI_WORKSPACE`.

| Command | Purpose |
|---------|---------|
| `add --input "..." [--no-recall]` | Enqueue a new entry（add 后自动召回相似历史条目） |
| `pop --limit N` | Dequeue pending entries and mark them `running` |
| `run --id <slug>` | Generate a record extraction task payload（record 唯一模式；`--depth`/`--mode article` 已废除） |
| `publish --id <slug>` | 记录发布：校验 record.json、links/relations/entities 入库、刷新索引 |
| `recall --input "..." [--limit N]` | 四层确定性相似召回（url_exact/shared_link/entity/fts） |
| `analyze --topic "..." [--limit N] [--emit-task]` | 主题聚簇（FTS+relations）+ 可选趋势综述 agent 任务 |
| `analyze --dedup [--min-score S]` | 输出同来源/强共享链接的去重候选对 |
| `analyze --discover [--days N]` | 自动发现近 N 天热点 tag/实体（alias 感知，标记已有 trends 覆盖） |
| `verify-links --id <slug>` | curl HEAD 懒式验证链接可达性 |
| `star --id <slug>` | 标星 canonical GitHub 仓库（publish 后调用；需 `GITHUB_TOKEN`） |
| `index [--output PATH]` | Regenerate `wiki/wiki.html` semantic index |
| `doctor [--quick] [--fix-plan]` | Health check + remediation plan |
| `stats` | Queue statistics |
| `list`, `search "query"` | Query the index（list --json 含 has_record/links_count） |
| `sync [--rebuild]` | Reconcile files with `wiki.db`（record.json 视为有效产物） |
| `requeue --id <slug> [--clear-md]` | Reset an entry to `pending` |
| `manifest` | Self-describing command list |

## Query / Retrieval

The skill also provides read access to the knowledge index for agent retrieval workflows.

```bash
# FTS5 search
python skills/wiki-curation/scripts/cli.py --json search "query" --limit 10

# List done entries
python skills/wiki-curation/scripts/cli.py --json list --status done --limit 20
```

`--json` returns stable fields: `id`, `title`, `overview`, `status`, `depth`, `source_input`, `queued_at`.

When a query hits wiki entries:

1. List `id`, `title`, `overview` to the user first.
2. Only `read` the full article after the user confirms or explicitly asks for it.
3. Full text lives at `$WIKI_WORKSPACE/artifacts/{id}/{id}_{depth}.md`.

**Constraint**: query knowledge defaults to the local wiki index. Only use external search (`web_search`, etc.) when the user explicitly asks for external / web / latest information.

For retrieval policy, see `AGENTS.md` and `TOOLS.md`. For skill routing, see `AGENTS_WIKI.md`.

## Backfill from Retrieval

When a comprehensive analysis across multiple wiki entries produces new comparisons, syntheses, or findings, propose backfilling them as a new wiki record:

> "这个对比/综述有知识价值，是否生成一条新 wiki 记录？"

If the user confirms:

```bash
python skills/wiki-curation/scripts/cli.py --json add \
  --input "<topic or representative source>"
python skills/wiki-curation/scripts/cli.py --json pop
python skills/wiki-curation/scripts/cli.py --json run --id <slug>
# spawn extraction agent / run task manually → record.json
python skills/wiki-curation/scripts/cli.py --json publish --id <slug>
```

Do **not** write the record or article manually.

## Deep upgrade（文章层）

```bash
python skills/wiki-curation/scripts/cli.py --json article --id <slug> --depth deep
# 写作 agent 完成后：
python skills/wiki-curation/scripts/cli.py --json publish --id <slug> --depth deep
```

## Append / Update knowledge

Add new material to an existing knowledge entry to produce an updated version（append 属文章层）：

```bash
python skills/wiki-curation/scripts/cli.py --json add \
  --append-to <base_slug> --input "<new_url>"
python skills/wiki-curation/scripts/cli.py --json pop
python skills/wiki-curation/scripts/cli.py --json run --id <base_slug> \
  --mode article --depth brief --append-to <base_slug>
```

## Result format

When the user asks for results, respond like:

```
✅ {done} 条完成，{failed} 条失败：
1. {id} — {title} — wiki/artifacts/{id}/record.json（links={n}）
2. {id} — {failure摘要}

哪些需要生成文章？输入编号或 id + depth（如 "1 brief"），跳过回复"都不用"。
```

## Configuration

Static configuration is bundled with the skill and loaded from two tracked directories:

- `references/` — source type definitions (`sources.yaml`), model routing (`models.yaml`), and audit schema (`audit_spec.json`).
- `assets/` — output format specifications (`output_spec_brief.yaml`, `output_spec_deep.yaml`).

The consumer workspace (`wiki/`) does **not** override these files; the skill uses the bundled defaults as the single source of truth.

## Model routing

Models are selected by configuration layering:

1. **Environment variables** (highest priority): `WIKI_MODEL_BRIEF` / `WIKI_MODEL_DEEP` / `WIKI_MODEL_RECORD`
2. **Skill defaults**: `references/models.yaml`
3. **Script hardcoded defaults** (lowest priority)

Default tiers:

- record: 记录提取任务（默认与 brief 同档）
- brief: `deepseek/deepseek-v4-flash`
- deep: `kimi/kimi-for-coding`

Override with environment variables:

```bash
export WIKI_MODEL_BRIEF=...
export WIKI_MODEL_DEEP=...
```

### Fallback mechanism

Each depth supports a fallback model list. When the primary model fails during task execution, the runner or harness should attempt each fallback in order.

Example from `models.yaml`:

```yaml
deep:
  primary: kimi/kimi-for-coding
  fallback:
    - deepseek/deepseek-v4-pro
```

The `route_model.select_model()` function returns a `fallback` list alongside the selected `model`. Task payloads generated by `interpret.py` include this list in the `"fallback"` field.

## Limitations

- `sessions_spawn` / `sessions_yield` are harness primitives. In a non-OpenClaw environment, use the portable task payload manually.
- WeChat and LinkedIn sources require `opencli`; without it they degrade to generic HTML extraction.
- `curl` is required for most downloads.

## Troubleshooting

- **"raw/ 目录不存在"** → run `collect` first, or ensure `WIKI_WORKSPACE` is set.
- **"Entry status is 'done', expected pending/running"** → run `requeue` before `run`.
- **"INCOMPLETE_WORKFLOW"** → the entry lacks a `WRITE` event; the writing agent did not finish or did not record the event.
- **Doctor grade F** → inspect `doctor --fix-plan` and review suggested commands before running them.

## Bug recording & regression

Whenever you discover a `wiki-curation` bug — whether through `doctor`, failed audits, user feedback, or your own inspection — you must record it before (or alongside) fixing it.

1. **Create a failure record** in `wiki/failures/` using the template at `wiki/failures/TEMPLATE.md`.
   - File name: `YYYY-MM-DD_NNN_short-kebab-slug.md` (use the next available 3-digit sequence number).
   - Copy `TEMPLATE.md` and fill in the sections: symptoms, evidence, root cause, fix plan, verification checklist.
2. **Update `wiki/failures/MANIFEST.json`** immediately. Add a lightweight entry (id / slug / file / status / priority / title / component / discovered_at). Keep all details in the `.md` file.
3. **When checking failure status, read `wiki/failures/MANIFEST.json` first.** Only open the corresponding `.md` detail file if you need full context.
4. **Link related artifacts**: cite the affected wiki entry slug(s), audit file path(s), and source code path(s).
5. **Fix the bug** in `skills/wiki-curation/` and run the contract tests:
   ```bash
   cd skills/wiki-curation
   python -m pytest scripts/ -q
   python eval/run_eval.py --deterministic
   ```
6. **Mark the record as fixed**: update both the `.md` file and `MANIFEST.json` to `🟢 fixed` and fill in the verification table.
7. **Do not delete old records**: keep them as regression history; future agents (and users) can see what was already fixed and avoid re-diagnosing the same problem.

If a bug turns out to be a **false alarm** or **expected behavior**, update both the `.md` file and `MANIFEST.json` to `⚪ wontfix` and explain why.

## Architecture

```
用户输入
   │
   ▼
cli.py add --input "..."        ← 自动召回相似历史条目
   │
   ▼
cli.py pop
   │
   ▼
cli.py run --id <slug> --json           （默认 record 模式）
   │
   ├─ classify_source.py        → classify knowledge source
   ├─ collect_materials.py      → gather source materials（URL 下钻）
   ├─ records/interpret_record.py → generate extraction task
   │
   ▼
extraction agent（只写 record.json）
   │
   ▼
cli.py publish --id <slug>
   │
   ├─ records/schema.py         → record.json 确定性校验
   ├─ records/links.py          → links 表入库（fetched 回填）
   ├─ records/relations.py      → 关联边织入
   ▼
status = done, metadata.json v3.0, wiki/wiki.html + wiki/site/ 刷新

（按需文章层：cli.py article --id <slug> --depth brief|deep
  → exec/interpret.py 写作任务 → writing agent → publish --depth 校验）
```

## Internal file layout

```
skills/wiki-curation/
├── SKILL.md
├── scripts/
│   ├── cli.py
│   ├── exec/
│   ├── intake/
│   ├── publish/
│   ├── store/
│   └── wiki_index/
├── references/
└── assets/
```

The consumer workspace (`wiki/`) contains only artifacts and runtime data.
