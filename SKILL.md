---
name: wiki-curation
description: "Ingest URLs into structured knowledge records (link graph + TL;DR + tags + entities), recall similar entries, and analyze trends. Trigger words: wiki, knowledge, record, recall, search, analyze, 查, 检索, 召回, 分析."
---

# wiki-curation

Ingest URLs/papers/files into **structured knowledge records** — link graph, TL;DR, tags, canonicalized entities — with deterministic recall, similarity scoring, and trend analysis. **Not an article generator.**

## Overview

Single tier, single path:

- **Record**: `add → pop → run → publish` → `record.json` — link graph (explicit + inferred URLs), TL;DR, X-style summary, tags, entities. Every ingestion goes through this.
- **Recall**: `add` auto-surfaces similar past entries; `recall --input "..."` queries anytime (4-layer: url_exact → shared_link → entity → fts).
- **Analyze**: `analyze --topic "X"` clusters evidence across records (feeds posts); `--discover` finds emerging hot topics.
- **Post**: `post --topic X / --records a,b / --suggest` — blog-style technical posts grounded in wiki evidence; `--auto` runs headless writing + validation + publish. Shown in the site Posts view with hub-based suggestions.
- **Track**: `track --name "X" [--auto]` — entity (person) tracking topics: deterministic record association, headless digest, periodic `track --refresh` (`--due` for cron). Site Tracking view + entity-chip trigger.
- **Survey**: `survey --id X --auto` end-to-end — the system fetches the record's links, a headless agent writes `artifacts/{slug}/survey/survey.md`, and publish happens automatically; step-by-step also supported (`survey --id X` collect+queue → agent writes → `--publish`). The site records table has a 🧭 column: click to trigger (auto pipeline) / view (new tab), with live state icons.
- **Site**: built static HTML served locally — compact table, timeline view, inline detail expansion, trends reader.

Core principle: **extraction by agent, linking by system.** The LLM reads materials and writes `record.json`; similarity, relations, URL verification are deterministic code.

## When to load

Load this skill when the user mentions: wiki, knowledge, record, recall, analyze, search, 查, 检索, 召回, 分析.

Project-level routing (`AGENTS_WIKI.md`) mandates this skill for all knowledge-base work.

## Prerequisites

- Python 3.11+ + `pyyaml` + `curl`
- `opencli` (optional; WeChat/LinkedIn handlers)
- `GITHUB_TOKEN` (optional; `public_repo` scope — auto-star canonical GitHub repos after publish)
- A consumer `wiki/` workspace

## Workspace setup

```
wiki/
├── data/wiki.db             # SQLite: entries + links + relations + FTS5
├── artifacts/{id}/
│   ├── record.json          # THE record (only artifact the agent writes)
│   ├── raw/                 # fetched source materials
│   └── survey/                # deep-survey: survey.md (agent) + survey.json/status.json/task.json (system) + raw/
├── posts/                   # blog-style posts (markdown, auto-listed on site)
├── tracking/{slug}/         # entity tracking topics: topic.json + digest.md + raw/
├── site/                    # built static site
└── wiki.html                # semantic index
```

Set `WIKI_WORKSPACE` or default to `cwd/wiki`.

## Hard constraints

1. **Always use the skill CLI.** Call `skills/wiki-curation/scripts/cli.py`. Do not call sub-scripts directly.
2. **No manual writing.** Records must go through `add → pop → run → publish`. Do not hand-author `record.json`.
3. **Mandatory workflow.** `run` requires prior `add` + `pop`.
4. **User confirmation before pop.** After first `add`, agent asks "start now or keep adding". User confirms before `pop --limit 3`.
5. **Do not modify task content.** Run task payload as-is.
6. **Configuration is single source of truth.** `references/sources.yaml` for classification; `references/record_schema.json` for record constraints.
7. **wiki.db is tracked.** Normal workflow commits preserve it.
8. **Sub-agents must not commit or publish.** Extraction agents only write `record.json`. The orchestrator runs `publish` and commits.

## Quick start

```bash
export WIKI_WORKSPACE=/path/to/project/wiki

# 1. Enqueue (auto-recalls similar entries)
python skills/wiki-curation/scripts/cli.py --json add --input "https://arxiv.org/abs/2101.00027"

# 2. Confirm with user, show queue
python skills/wiki-curation/scripts/cli.py --json list --status pending

# 3. Pop (only after user approval)
python skills/wiki-curation/scripts/cli.py --json pop --limit 3

# 4. Generate extraction task payload
python skills/wiki-curation/scripts/cli.py --json run --id <slug>

# 5. Spawn extraction agent → produces wiki/artifacts/<slug>/record.json

# 6. Publish (validate, store links/relations, rebuild site)
python skills/wiki-curation/scripts/cli.py --json publish --id <slug>
```

## CLI reference

All commands support `--json`. `--workspace PATH` overrides `$WIKI_WORKSPACE`.

| Command | Purpose |
|---|---|
| `add --input "..." [--no-recall]` | Enqueue; auto-recalls similar past entries |
| `pop --limit N` | Dequeue pending → running |
| `run --id <slug>` | Classify + collect + emit record extraction task |
| `publish --id <slug>` | Validate record.json, store links/relations/entities, rebuild site |
| `recall --input "..." [--limit N]` | 4-layer similarity recall with reasons |
| `search "query"` | FTS5 full-text search |
| `analyze --topic "..." [--emit-task]` | Evidence cluster across records; optional post task |
| `analyze --dedup` | Duplicate candidate pairs (same_url / shared_link) |
| `analyze --discover [--days N]` | Emerging hot tags/entities (alias-aware, marks existing coverage) |
| `survey --id X [--force] [--task\|--publish\|--status] [--queue]` | Deep-survey a record: fetch links + emit survey task / publish / status / agent queue |
| `add-link --id X --url U [--role R] [--update-survey]` | Add a manually-found link to a record's link graph (origin=manual); optionally regenerate its survey |
| `post --topic X \| --records a,b \| --suggest [--auto]` | Blog-style post from wiki evidence (fusion/topic/suggest) |
| `track --name X [--kind] [--refresh S] [--due] [--auto]` | Entity tracking topics: create/refresh/due/archive |
| `verify-links --id <slug>` | Lazy curl-HEAD link reachability |
| `star --id <slug>` | Star canonical GitHub repos (needs `GITHUB_TOKEN`) |
| `doctor [--quick] [--fix-plan]` | Health: queue/db/files/git/record-tier/entities |
| `stats` / `list` / `sync` / `requeue` / `delete` / `update` / `manifest` | Store utilities |

## Architecture

```
add --input "..."          ← classify source + auto-recall
   │
pop
   │
run --id <slug>
   ├─ collect_materials    ← fetch + recursive drill (3 levels)
   ├─ interpret_record     ← generate extraction task prompt
   │
[orchestrator writes raw/agent_notes.md]  ← optional: pre-reading analysis notes
   │
extraction agent           ← reads raw/ + agent_notes.md → writes record.json
   │
publish --id <slug>
   ├─ schema.validate      ← deterministic record validation
   ├─ links.replace        ← links table (fetched backfill)
   ├─ relations.rewire     ← same_url/shared_link/shared_entity/tag_overlap edges
   ├─ site.build           ← entries.json + timeline + graph + trends
   ▼
done: record + site + wiki.html refreshed

survey --id X                ← record deep-survey (site button or CLI)
   ├─ select_survey_links    ← canonical first, skip already-fetched
   ├─ collect_sources      ← survey/raw/ (max_depth=1)
   ├─ generate_survey_task   ← task_mode=survey, status=awaiting_agent
   │
survey agent                 ← hand-writes survey.md (TL;DR/核心内容/分来源摘要/原始出处)
   │
survey --id X --publish      ← structure validation + survey.json + site surveys.json
```

## Orchestrator notes (agent_notes.md)

When the orchestrating agent reads raw materials before spawning the extraction agent
(e.g., giving the user a quick interpretation of a WeChat article), it should save
its analysis notes as `raw/agent_notes.md` in the artifact directory. The extraction
task generator (`interpret_record.py`) auto-detects this file and includes a
"补充参考" section in the extraction task prompt, allowing the extraction agent to
use the orchestrator's analytical framing as reference.

- The orchestrator writes `raw/agent_notes.md` **after** `run` (which creates the
  artifact directory) and **before** spawning the extraction agent.
- The extraction agent reads `agent_notes.md` as **analysis reference only**;
  all factual claims in record.json must still be anchored in raw source materials.

## Model routing

Priority: `WIKI_MODEL_RECORD` env var → `references/models.yaml` record tier → hardcoded default.

Default: `kimi/kimi-for-coding`, fallback `deepseek/deepseek-v4-flash`.

## Configuration

- `references/sources.yaml` — source-type classification, fetch handlers, drill policy
- `references/models.yaml` — model routing
- `references/record_schema.json` — record.json constraints
- `references/entity_aliases.yaml` — entity canonical/alias map

## Bug recording

Wiki workflow bugs must be recorded in `wiki/failures/` (template: `wiki/failures/TEMPLATE.md`), with `MANIFEST.json` updated. Fix → mark `🟢 fixed`; don't delete history. False alarms → `⚪ wontfix`.

## Limitations

- `sessions_spawn` (OpenClaw harness) is optional; task payloads can be run manually.
- WeChat/LinkedIn require `opencli`; degrade to generic HTML extraction without it.
- `curl` required for most downloads.
