# wiki-curation

Turn fragmented clues (URLs, keywords, names) into a structured, searchable, linkable personal knowledge base — automatically.

`wiki-curation` is a personal knowledge-curation pipeline for AI agents (Claude Code, OpenClaw, or any agent harness that can run shell commands). You throw a URL or a name at it; it sniffs related links, extracts a structured record (link graph + TL;DR + tags + entities), and files it into a queryable store with deterministic recall and a lightweight static site.

**Not an article generator.** It produces structured *records*, not prose. Long-form interpretation is a separate concern.

## What it does

```
you:  "看看这个 https://github.com/octo-models/octo"
agent:
  add      → classify source (github) + recall similar past entries
  pop      → dequeue the task
  run      → fetch README + drill into arxiv/hf/homepage links (3 levels)
           → extraction agent writes record.json
  publish  → validate, store links/entities/relations in SQLite, rebuild site
```

Each record:

```json
{
  "id": "2026-07-22_7812",
  "title": "Octo: An Open-Source Generalist Robot Policy",
  "topic_type": "project",
  "tldr": "one-sentence summary",
  "summary": "X/LinkedIn-style digest, 2-4 short paragraphs",
  "tags": ["robotics", "VLA", "diffusion-policy"],
  "entities": {"company": ["UC Berkeley"], "author": ["..."], "product": ["Octo"], "series": []},
  "links": [
    {"url": "https://github.com/octo-models/octo", "kind": "github", "role": "canonical", "origin": "explicit"},
    {"url": "https://arxiv.org/abs/2405.12213", "kind": "arxiv", "role": "canonical", "origin": "inferred"}
  ]
}
```

Design principle: **extraction by agent, linking by system.** The LLM only reads materials and writes `record.json`. Similarity scoring, relation edges, URL normalization/verification are all deterministic SQL/regex — no hallucination can pollute the graph.

## Install

```bash
git clone <repo>
cd wiki-curation
pip install -e .          # Python ≥ 3.11, only dependency: pyyaml (+ pytest for dev)
# curl required for fetching; that's it.
```

Point the CLI at a workspace (created on first use):

```bash
export WIKI_WORKSPACE=/path/to/wiki
python scripts/cli.py --json add --input "https://arxiv.org/abs/2405.12213"
python scripts/cli.py --json pop
python scripts/cli.py --json run --id <slug>     # prints an extraction task payload
# run the payload with any capable agent, then:
python scripts/cli.py --json publish --id <slug>
```

Browse the result:

```bash
python scripts/cli.py site --serve --pid-file wiki/.site-serve.pid
# → http://localhost:8123  (auto-redirects to /site/)
python scripts/cli.py site --stop --pid-file wiki/.site-serve.pid
```

## Command reference

| Command | Purpose |
|---|---|
| `add --input X [--no-recall]` | Enqueue a clue; auto-recalls similar past entries |
| `pop --limit N` | Dequeue pending tasks (max 3 recommended) |
| `run --id <slug>` | Classify + collect + emit extraction task payload |
| `publish --id <slug>` | Validate record.json, store links/relations, rebuild site |
| `recall --input X` | 4-layer deterministic similarity recall with reasons |
| `search "query"` | FTS5 full-text search |
| `list [--status S]` | List entries (JSON: has_record/links_count) |
| `verify-links --id <slug>` | Lazy curl-HEAD reachability check for links |
| `doctor [--quick] [--fix-plan]` | Health checks (queue/db/record-tier/entities/git) |
| `stats`, `sync`, `requeue`, `delete`, `update`, `events`, `manifest` | Store utilities |

All commands support `--json` for agent consumption.

## Workspace layout

```
wiki/
├── data/wiki.db          # SQLite: entries + links + relations + FTS5 (single source of truth)
├── artifacts/{id}/
│   ├── record.json       # THE record (only artifact the agent writes)
│   └── raw/              # fetched source materials (kept for evidence/analysis)
├── trends/               # optional long-form trend articles (markdown, auto-listed on site)
├── site/                 # built static site (served at /site/)
└── wiki.html             # static semantic index
```

## Optional integrations (graceful degradation)

Everything below is **optional**; the pipeline degrades cleanly without them:

| Integration | Used for | Fallback |
|---|---|---|
| `sessions_spawn` (OpenClaw) | Dispatching extraction sub-agents | Run the emitted task payload with any agent manually |
| `opencli` | WeChat (`mp.weixin.qq.com`) & LinkedIn fetching | Generic HTML extraction |
| `~/.openclaw/openclaw.json` | Model routing inheritance | `WIKI_MODEL_RECORD` env var or `references/models.yaml` defaults |
| `references/entity_aliases.yaml` | Entity canonicalization + cross-lingual recall (李飞飞 ↔ Fei-Fei Li) | Entities stored as-is |

## Configuration

- `references/sources.yaml` — source-type classification rules, fetch handlers, drill-down policy
- `references/models.yaml` — model routing for extraction (`WIKI_MODEL_RECORD` env overrides)
- `references/record_schema.json` — record.json constraints (validator consumes)
- `references/entity_aliases.yaml` — entity canonical/alias map

## Tests

```bash
cd wiki-curation
python -m pytest scripts/ -q          # 146 contract tests (requires pytest)
python eval/run_eval.py --deterministic   # 8 CLI-contract graders
```

Optional dev-only site check (verifies the rendered table end-to-end via a
Node VM browser shim; **Node.js is NOT a runtime dependency** — the site
itself is pre-rendered static files served by Python's `http.server`):

```bash
node scripts/site/verify_site.js http://localhost:8123
```

## License

MIT — see [LICENSE](LICENSE).
