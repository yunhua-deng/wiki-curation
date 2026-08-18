# wiki-curation

A knowledge-curation skill for AI agents. Turn fragmented clues (URLs, keywords, names) into a structured, searchable, linkable personal knowledge base.

The agent does the reading; the system does the linking. **Not an article generator.**

## How agents install this skill

### Claude Code

Place the skill directory at `skills/wiki-curation/` in your project root, alongside a `CLAUDE.md` that routes wiki requests to it:

```markdown
# CLAUDE.md (excerpt)
- wiki / 知识 / 记录 / 检索 / 分析 → 项目根 `AGENTS.md` + `skills/wiki-curation/SKILL.md`
```

Create or extend the project-root `AGENTS.md` with a wiki routing entry:

```markdown
# AGENTS.md (excerpt) — Wiki routing
- Any wiki/knowledge request → activate `wiki-curation` skill (`skills/wiki-curation/SKILL.md`).
- Workflow: `add → pop → run → publish` (record.json).
- Extraction agent: write record.json only; no git, no publish.
- `python skills/wiki-curation/scripts/cli.py site --serve --pid-file wiki/.site-serve.pid`
```

The agent discovers the skill by reading `SKILL.md`'s `name:` and `description:` frontmatter. No registration needed.

### OpenClaw

Same directory convention (`skills/wiki-curation/`). OpenClaw's agent loader reads `SKILL.md` frontmatter for routing. `sessions_spawn` can dispatch extraction sub-agents automatically if the harness is configured.

### Manual install

```bash
git clone https://github.com/yunhua-deng/wiki-curation.git skills/wiki-curation
cd skills/wiki-curation
pip install -e .          # Python ≥ 3.11, dependency: pyyaml only
```

## Quick start

```bash
export WIKI_WORKSPACE=/path/to/wiki

python scripts/cli.py --json add --input "https://arxiv.org/abs/2405.12213"
python scripts/cli.py --json pop --limit 3
python scripts/cli.py --json run --id <slug>     # emits extraction task
# agent writes wiki/artifacts/<slug>/record.json, then:
python scripts/cli.py --json publish --id <slug>

python scripts/cli.py site --serve --pid-file wiki/.site-serve.pid
# → http://localhost:8123/site/
```

## What a record looks like

```json
{
  "id": "2026-07-22_7812",
  "title": "Octo: An Open-Source Generalist Robot Policy",
  "topic_type": "project",
  "tldr": "one-sentence summary",
  "summary": "X-style digest, 2-4 short paragraphs",
  "tags": ["robotics", "VLA", "diffusion-policy"],
  "entities": {"company": ["UC Berkeley"], "author": ["..."], "product": ["Octo"]},
  "links": [
    {"url": "https://github.com/octo-models/octo", "kind": "github", "role": "canonical", "origin": "explicit"},
    {"url": "https://arxiv.org/abs/2405.12213", "kind": "arxiv", "role": "canonical", "origin": "inferred"}
  ]
}
```

Design principle: **extraction by agent, linking by system.** Similarity scoring, relation edges, URL canonilization, and record validation are all deterministic code — hallucinations can't poison the graph.

## Command reference

All commands support `--json` for agent consumption.

| Command | Purpose |
|---|---|
| `add --input X [--no-recall]` | Enqueue; auto-recalls similar past entries |
| `pop --limit N` | Dequeue pending → running |
| `run --id <slug>` | Classify + collect + emit extraction task payload |
| `publish --id <slug>` | Validate record, store links/relations, rebuild site |
| `recall --input X` | 4-layer similarity recall with reasons |
| `analyze --topic "..."` | Evidence cluster across records |
| `analyze --dedup` | Duplicate candidate pairs |
| `analyze --discover [--days N]` | Emerging hot topics (alias-aware) |
| `add-link --id X --url U [--role R]` | Add a manually-found link to a record's link graph (origin=manual) |
| `verify-links --id <slug>` | curl-HEAD reachability check |
| `star --id <slug>` | Star canonical GitHub repos (needs `GITHUB_TOKEN`) |
| `doctor [--quick]` | Health: queue/db/files/git/record-tier/entities |
| `stats` / `list` / `search` / `sync` / `requeue` / `manifest` | Store utilities |

## Workspace layout

```
wiki/
├── data/wiki.db             # SQLite: entries + links + relations + FTS5
├── artifacts/{id}/
│   ├── record.json          # THE record
│   └── raw/                 # fetched source materials
└── site/                    # built static site
```

## Optional integrations (graceful degradation)

| Integration | Used for | Fallback |
|---|---|---|
| `sessions_spawn` (OpenClaw) | Dispatching extraction sub-agents | Run task payload manually |
| `opencli` | WeChat & LinkedIn fetching | Generic HTML extraction |
| `GITHUB_TOKEN` | Auto-starring repos after publish | `star` command silently skips |
| `entity_aliases.yaml` | Entity canonicalization + cross-lingual recall | Entities stored as-is |

## Configuration

- `references/sources.yaml` — source-type classification, fetch handlers, drill policy
- `references/record_schema.json` — record.json constraints
- `references/entity_aliases.yaml` — entity canonical/alias map

## Verification design

| Layer | Command | Question | Cost |
|---|---|---|---|
| **pytest** | `python -m pytest scripts/ -q` | Are module behaviors correct? | free, ~60s, offline |
| **CLI graders** | `python eval/run_eval.py --deterministic` | Is the agent's JSON contract intact? | free, seconds, offline |
| **LLM rubric (opt-in)** | `python eval/run_eval.py --llm` | Is the generated payload *good*? (LLM-as-a-judge) | model cost, spot-check |
| **Site render (opt-in)** | `node scripts/site/verify_site.js <url>` | Does the table render in a browser VM? | Node.js (dev-only) |

The deterministic graders guard the machine interface on every commit. Content quality is a separate axis — periodic spot-check with the LLM rubric, never blocking.

## License

MIT — see [LICENSE](LICENSE).
