#!/usr/bin/env python3
"""
scripts/records/dive.py — record 深度解读（dive）的核心确定性逻辑。

职责边界（对齐 record 提取）：系统负责链接选择、素材采集、任务生成、
结构校验、发布与状态机；dive.md 由执行 task 的 agent 手写，脚本永不调 LLM。

状态机（artifacts/{slug}/dive/status.json）：
  collecting → awaiting_agent → done | failed
  awaiting_agent 即队列（网页/CLI 触发统一表达）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import paths
from scripts.records import schema as RS
from scripts.records.schema import normalize_url

DIVE_MD_MAX_BYTES = 40 * 1024
REQUIRED_H2 = ["TL;DR", "核心内容", "分来源摘要", "原始出处"]
DEFAULT_MAX_LINKS = 5

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"


class DiveError(Exception):
    """带机器可读码的 dive 错误。code 供 CLI/API 输出；next_cmd 指引下一步。"""

    def __init__(self, code: str, message: str, next_cmd: str = None):
        super().__init__(message)
        self.code = code
        self.next_cmd = next_cmd


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================
# 状态机
# ============================================================

def read_status(slug: str, ws=None) -> dict:
    """读取 dive 状态；不存在/损坏返回 {}。"""
    path = paths.dive_status_path(slug, ws)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_status(slug: str, ws, state: str, detail: dict = None, error: str = None) -> None:
    """写 dive 状态机。error 为 None 时清除旧 error 字段。"""
    path = paths.dive_status_path(slug, ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "updated_at": _now_iso()}
    if detail is not None:
        payload["detail"] = detail
    if error:
        payload["error"] = error
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 链接选择
# ============================================================

def select_dive_links(record_links: list, max_links: int = DEFAULT_MAX_LINKS) -> list:
    """从 record links 选出本次 dive 要抓的链接。

    canonical 优先、related 次之（各组内保持原顺序）；跳过 fetched==1
    （record 原 raw/ 已有材料，不重复抓取）；normalize_url 去重；封顶 max_links。
    """
    canonical, related = [], []
    seen = set()
    for link in record_links or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not url or link.get("fetched") == 1:
            continue
        nu = normalize_url(url)
        if not nu or nu in seen:
            continue
        seen.add(nu)
        (canonical if link.get("role") == "canonical" else related).append(dict(link))
    return (canonical + related)[:max_links]


# ============================================================
# 采集 + 任务生成
# ============================================================

_RAW_META_NAMES = {"_drill_log.json", "_fetch_results.json", "source.txt", "source_info.md", "prompt.md"}


def _raw_has_material(slug: str, ws) -> bool:
    raw = paths.raw_dir(slug, ws)
    if not raw.exists():
        return False
    for f in raw.rglob("*"):
        if f.is_file() and f.name not in _RAW_META_NAMES:
            return True
    return False


def _summarize_dir(directory: Path, display_prefix: str, max_entries: int = 30):
    """返回 (文件行列表, 扩展名摘要)。display_prefix 仅用于 prompt 展示。"""
    files = []
    if directory.exists():
        for f in sorted(directory.rglob("*")):
            if f.is_file() and f.name not in _RAW_META_NAMES:
                files.append((f.relative_to(directory), f.stat().st_size // 1024))
    lines = []
    for rel, size_kb in files[:max_entries]:
        lines.append(f"  {display_prefix}{str(rel).replace(chr(92), '/')}" + (f" ({size_kb}KB)" if size_kb else ""))
    if len(files) > max_entries:
        lines.append(f"  ... 还有 {len(files) - max_entries} 个文件")
    summary = {}
    for rel, _ in files:
        ext = Path(rel).suffix.lower() or "(无后缀)"
        summary[ext] = summary.get(ext, 0) + 1
    summary_str = ", ".join(f"{c}×{e}" for e, c in sorted(summary.items(), key=lambda x: -x[1])[:6])
    return lines, summary_str


def _related_entries(db_path, slug: str, limit: int = 5) -> list:
    """relations 表中该条目的 top 关联（确定性，供 prompt 引用）。"""
    try:
        from scripts.records.links import get_all_relations
        rels = get_all_relations(db_path)
    except Exception:
        return []
    scored = []
    for r in rels:
        if r.get("entry_a") == slug:
            scored.append((r.get("entry_b"), r.get("score") or 0))
        elif r.get("entry_b") == slug:
            scored.append((r.get("entry_a"), r.get("score") or 0))
    scored.sort(key=lambda kv: -kv[1])
    return [{"id": k, "score": round(s, 1)} for k, s in scored[:limit] if k]


def build_dive_task(slug: str, record: dict, dive_files: list, dive_summary: str,
                    raw_files: list, raw_summary: str, related: list, ws) -> str:
    """构造 dive agent 的 task prompt。"""
    dive_md_out = str(paths.dive_md_path(slug, ws).resolve())
    record_abs = str(paths.record_path(slug, ws).resolve())
    title = record.get("title") or slug
    links_block = "\n".join(
        f"- [{l.get('kind', 'other')}/{l.get('role', 'related')}] {l.get('url')}"
        for l in (record.get("links") or [])
    ) or "- （record 无 links）"
    related_block = "\n".join(f"- {r['id']} (score={r['score']})" for r in related) or "- （无）"
    dive_listing = "\n".join(dive_files) if dive_files else "  (dive/raw/ 为空)"
    raw_listing = "\n".join(raw_files) if raw_files else "  (record raw/ 为空或不存在)"

    return f"""你是知识深度解读 agent。基于给定材料，为记录 {slug} 写一页**深度解读**，写入唯一输出文件：
`{dive_md_out}`

## 背景记录（已提取的结构化信息，作为骨架与链接来源）

record.json: `{record_abs}`
标题：{title}
TL;DR：{record.get('tldr') or ''}

record 已提取的 links：
{links_block}

## 可用材料

1. dive 新采集材料（`artifacts/{slug}/dive/raw/`，{dive_summary or '空'}）：
{dive_listing}
2. record 原始材料（`artifacts/{slug}/raw/`，{raw_summary or '空'}）：
{raw_listing}

## 输出契约（dive.md）

写一个 Markdown 文件，结构严格如下：

```markdown
# {title} — 深度解读
> 生成：YYYY-MM-DD · 基于 N 个来源 · 记录：{slug}

## TL;DR
3-5 句整合性陈述（这是什么、核心内容、关键数据/结论）。

## 核心内容
按主题/问题组织 2-5 个小节（### 小节标题），跨来源整合事实；关键事实行内附 [来源](url)。

## 分来源摘要
### 来源标题或域名
每个来源 5-8 句客观摘要。更多内容请看：<url>

## 原始出处
- <url1>
- <url2>
```

## 硬性规则

1. **客观整合，不评论**：不写"我认为/优于/最强"这类评价。
2. **不整段搬运**：单源连续引用 ≤ 80 字；总结为主，细节让读者跳原始出处。
3. 「分来源摘要」每个来源小节必须以 `更多内容请看：<url>` 收尾。
4. **可溯源**：所有内容必须来自上面列出的材料文件或 record.json；禁止联网检索补充；禁止编造 URL。
5. **禁止执行任何 git 操作**；禁止运行 publish / cli 命令；不要修改 dive.md 以外的任何文件。
6. 完成后只返回简洁摘要（引用了几个来源、总字数）。

## 相关记录（系统提供；如非空，可在文末加一行「相关记录」）

{related_block}
"""


def generate_dive_task(slug: str, ws=None) -> dict:
    """生成 dive 任务 envelope（与 record task envelope 对齐 + task_mode=dive）。"""
    from scripts.route_model import select_model

    ws = Path(ws) if ws is not None else paths.get_workspace()
    record = RS.load_record(slug, ws)
    if not record:
        raise DiveError("RECORD_MISSING", f"record.json not found: {slug}",
                        next_cmd=f"{CLI_CMD} run --id {slug}")
    dive_files, dive_summary = _summarize_dir(paths.dive_raw_dir(slug, ws), f"artifacts/{slug}/dive/raw/")
    raw_files, raw_summary = _summarize_dir(paths.raw_dir(slug, ws), f"artifacts/{slug}/raw/")
    related = _related_entries(paths.db_path(ws), slug)
    task_text = build_dive_task(slug, record, dive_files, dive_summary,
                                raw_files, raw_summary, related, ws)
    model_info = select_model("dive")
    return {
        "task": task_text,
        "taskName": f"dive-{slug}",
        "model": model_info["model"],
        "fallback": model_info.get("fallback", []),
        "mode": "run",
        "task_mode": "dive",
        "cleanup": "keep",
        "context": "isolated",
        "slug": slug,
        "output_path": str(paths.dive_md_path(slug, ws).resolve()),
    }


def _classify_for_collect(url: str) -> dict:
    """链接 → collect_sources 元素；分类失败降级 generic_web。"""
    try:
        from scripts import intake
        c = intake.classify_one(url)
        return {"input_type": c.get("input_type", "url"),
                "source_type": c.get("source_type", "generic_web"),
                "input": url}
    except Exception:
        return {"input_type": "url", "source_type": "generic_web", "input": url}


def _collect_sources(slug, sources, max_depth=None, dest_base=None):
    """collect_sources 的薄封装（便于测试 mock）。"""
    from scripts.exec.collect_materials import collect_sources
    return collect_sources(slug, sources, max_depth=max_depth, dest_base=dest_base)


def collect_dive(slug: str, ws=None, max_links: int = DEFAULT_MAX_LINKS, force: bool = False) -> dict:
    """采集 dive 材料 + 生成任务 payload；状态推进到 awaiting_agent。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    record = RS.load_record(slug, ws)
    if not record:
        raise DiveError("RECORD_MISSING", f"record.json not found: {slug}",
                        next_cmd=f"{CLI_CMD} run --id {slug}")
    if paths.dive_md_path(slug, ws).exists() and not force:
        raise DiveError("DIVE_EXISTS", f"dive already exists: {slug}（--force 重新生成）")
    current = read_status(slug, ws)
    if current.get("state") == "collecting" and not force:
        raise DiveError("DIVE_RUNNING", f"dive is collecting: {slug}（--force 强制重跑）")

    write_status(slug, ws, "collecting")
    try:
        links = select_dive_links(record.get("links"), max_links=max_links)
        if not links and not _raw_has_material(slug, ws):
            raise DiveError("NO_MATERIAL", "record 无可抓取的 links，且原 raw/ 无材料")
        summary = {"selected": [l["url"] for l in links], "collected": 0, "failed": 0}
        if links:
            sources = [_classify_for_collect(l["url"]) for l in links]
            log = _collect_sources(slug, sources, max_depth=1,
                                   dest_base=paths.dive_raw_dir(slug, ws))
            s = (log or {}).get("summary", {})
            summary["collected"] = s.get("success", 0)
            summary["failed"] = s.get("failed", 0)
        task = generate_dive_task(slug, ws)
        task_path = paths.dive_task_path(slug, ws)
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        write_status(slug, ws, "awaiting_agent", detail=summary)
        return task
    except DiveError as e:
        write_status(slug, ws, "failed", error=str(e)[:200])
        raise
    except Exception as e:
        write_status(slug, ws, "failed", error=str(e)[:200])
        raise


def spawn_dive_agent_if_possible(slug: str, ws=None) -> dict:
    """sessions_spawn 可用时自动派发 dive agent；否则保持 awaiting_agent。"""
    import shutil
    from scripts.lib import run_cmd

    ws = Path(ws) if ws is not None else paths.get_workspace()
    exe = shutil.which("sessions_spawn")
    if not exe:
        return {"spawned": False, "reason": "sessions_spawn not found"}
    task_path = paths.dive_task_path(slug, ws)
    if not task_path.exists():
        return {"spawned": False, "reason": "task.json missing"}
    task = json.loads(task_path.read_text(encoding="utf-8"))
    cmd = [exe, "--taskName", task["taskName"], "--model", task["model"],
           "--mode", "run", "--task", task["task"]]
    r = run_cmd(cmd, timeout=60)
    return {"spawned": bool(r["ok"]), "reason": "" if r["ok"] else (r.get("stderr") or "spawn failed")[:200]}
