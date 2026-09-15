# wiki-curation

A knowledge-curation skill for AI agents. Turn fragmented clues (URLs, keywords, names) into a structured, searchable, linkable personal knowledge base.

The agent does the reading; the system does the linking. **Not an article generator.**

## Installing the skill

The skill is loaded from **user-level directories**, not from inside a project — every agent on the machine then sees the same copy. Pick the location for your harness:

| Consumer | Path | Kind |
|---|---|---|
| OpenClaw / Kimi Code | `~/.agents/skills/wiki-curation` | clone |
| Claude Code | `~/.claude/skills/wiki-curation` | clone |
| Kimi Code (local dev) | `~/.kimi-code/skills/wiki-curation` | symlink → the working copy |

```bash
# GitHub unreachable? add a proxy:  git -c http.proxy=http://127.0.0.1:7897 clone …
git clone https://github.com/yunhua-deng/wiki-curation.git ~/.agents/skills/wiki-curation
```

The agent discovers the skill by reading `SKILL.md`'s `name:` / `description:` frontmatter — no registration, no `pip install` (`scripts/cli.py` bootstraps its own import path). The only Python dependency is `pyyaml`.

Routing: the **workspace's** `AGENTS.md` mandates this skill for wiki/knowledge work and points at `SKILL.md`. Per-agent routing entries are unnecessary.

**Updating:** `git pull` inside each clone (a symlinked dev copy needs nothing). `openclaw skills update` only touches ClawHub-installed skills and will not sync these clones.

## Quick start

```bash
# --json / --quiet / --workspace are GLOBAL options: they must come before the sub-command.
# --workspace points at the wiki/ directory ITSELF (default: cwd/wiki).
python scripts/cli.py --workspace /path/to/wiki init          # once
python scripts/cli.py --json --workspace /path/to/wiki add --input "https://arxiv.org/abs/2405.12213"
python scripts/cli.py --json --workspace /path/to/wiki list --status pending   # show the user, get approval
python scripts/cli.py --json --workspace /path/to/wiki pop --limit 3           # only after approval
python scripts/cli.py --json --workspace /path/to/wiki run --id <slug>         # emits the extraction task
# the extraction agent writes wiki/artifacts/<slug>/record.json, then:
python scripts/cli.py --json --workspace /path/to/wiki publish --id <slug>

python scripts/cli.py --workspace /path/to/wiki site --serve --pid-file wiki/.site-serve.pid
# → http://localhost:8123/
```

For a single clue **after** the user has waived the queue review, `add → pop --limit 1 → run` collapses into one call:

```bash
python scripts/cli.py --json --workspace /path/to/wiki ingest --input "https://arxiv.org/abs/2405.12213"
# → {"ok": true, "data": {"id": <slug>, "added": {...}, "popped": [...], "run": {...}}}
```

`ingest` pops inside the same call, so it can never show the queue first — it never replaces the confirmation gate, it only skips the round-trip when the user already said 「直接处理 / 不用确认」.

## What a record looks like

```json
{
  "version": "3.0",
  "id": "2026-07-22_7812",
  "title": "Octo: An Open-Source Generalist Robot Policy",
  "date": "2024-05-20",
  "topic_type": "project",
  "tldr": "one-sentence summary",
  "summary": "digest in 2-5 short paragraphs",
  "tags": ["robotics", "VLA", "diffusion-policy"],
  "entities": {"company": ["UC Berkeley"], "author": ["..."], "product": ["Octo"], "series": []},
  "links": [
    {"url": "https://github.com/octo-models/octo", "kind": "github", "role": "canonical", "origin": "explicit", "fetched": null, "verified": null},
    {"url": "https://arxiv.org/abs/2405.12213", "kind": "arxiv", "role": "canonical", "origin": "inferred", "fetched": null, "verified": null}
  ],
  "source": {"input_type": "url", "source_type": "github", "direct_source": "https://github.com/octo-models/octo", "original_source": "https://arxiv.org/abs/2405.12213"}
}
```

All four `entities` buckets are required (empty arrays are fine); `version` / `source` are required fields; `fetched` / `verified` are backfilled by `publish`. The authoritative constraints live in `references/record_schema.json`.

Design principle: **extraction by agent, linking by system.** Similarity scoring, relation edges, URL canonicalization, and record validation are all deterministic code — hallucinations can't poison the graph.

## Commands

`python scripts/cli.py manifest` prints the authoritative machine-readable command list (names, flags, descriptions) — read that instead of relying on a hand-maintained table. The core pipeline is:

`init` · `add` · `list` · `pop` · `run` · `publish` · `recall` · `search` · `analyze` · `reconcile` · `doctor` · `site`

Behaviour worth knowing before you drive the pipeline:

- **Material gate.** `run` fails with `MATERIALS_MISSING` when a declared source has no fetch evidence, instead of silently building a record from nothing. Fetch by hand, then re-run with `--accept-manual`.
- **Append.** `add --append-to <slug>` requires a published base; new material lands in `raw/append_<N>/` and never overwrites the old material.
- **Render-required sources** (WeChat, LinkedIn, SPA/`#!` pages, HF Spaces) retry the cheap path then fall back to browser rendering; a total failure is reported as `needs_browser`, never as success.
- **No blind retries.** `run` / `collect` / `ingest` run with `retries=0`: a non-zero exit code is a normal, intended failure signal.

Full details, error codes and the event list: `SKILL.md`.

## Workspace layout

```
wiki/
├── data/wiki.db             # SQLite: entries + links + relations + FTS5
├── artifacts/{id}/
│   ├── record.json          # THE record
│   └── raw/                 # fetched source materials (+ raw/append_<N>/ for appends)
├── docs/issues/             # issue registry (entries + MANIFEST.json + TEMPLATE.md)
└── site/                    # built static site (Records view)
```

`relations` holds structural edges only (`same_url` / `shared_link` / `tag_overlap`).

## Optional integrations (graceful degradation)

| Integration | Used for | Fallback |
|---|---|---|
| `sessions_spawn` (OpenClaw) | Dispatching extraction sub-agents | Run the task payload manually |
| `opencli` + a browser session | WeChat/LinkedIn and the render fallback for render-required sources | Those sources end as `needs_browser` → fetch by hand, then `run --accept-manual` |
| `GITHUB_TOKEN` | The **manual** `star --id <slug>` command (which canonical GitHub repos to star) | `star` reports it is unavailable; `publish` never stars anything |
| `entity_aliases.yaml` | Entity canonicalization + cross-lingual recall | Entities are stored as-is |

## Configuration

- `references/sources.yaml` — source-type classification + aliases, fetch handlers, the material-validity threshold (`settings.min_visible_chars`), the render-required retry count (`settings.render_retries`) and the render-required list (`render_required`)
- `references/record_schema.json` — record.json constraints (hard-validator numbers; the extraction prompt interpolates them)
- `references/entity_aliases.yaml` — entity canonical/alias map + `suppress`/`suppress_patterns` suppression lists (exact + regex; canonical keys are never suppressed; shared logic in `scripts/entity_filter.py`, also feeds recall's entity layer)
- `references/entity_groups.yaml` — canonical exemption list (`groups` keys) + academia keywords, consumed by `scripts/entity_filter.py`

## Verification design

| Layer | Command | Question | Cost |
|---|---|---|---|
| **pytest** | `python -m pytest scripts/ -q` | Are module behaviors correct? | free, ~60s, offline |
| **CLI graders** | `python eval/run_eval.py --deterministic` | Is the agent's JSON contract intact? | free, seconds, offline |
| **LLM rubric (opt-in)** | `python eval/run_eval.py --llm` | Is the generated payload *good*? (LLM-as-a-judge) | model cost, spot-check |
| **Site render (opt-in)** | `node scripts/site/verify_site.js <url>` | Does the table render in a browser VM? | Node.js (dev-only) |

The deterministic graders guard the machine interface on every commit (`core.hooksPath .githooks`). Content quality is a separate axis — periodic spot-check with the LLM rubric, never blocking.

## License

MIT — see [LICENSE](LICENSE).
