#!/usr/bin/env python3
"""
scripts/records/survey.py — record 综述（survey）的核心确定性逻辑。

职责边界（对齐 record 提取）：系统负责链接选择、素材采集、任务生成、
结构校验、发布与状态机；survey.md 由执行 task 的 agent 手写，脚本永不调 LLM。

状态机（artifacts/{slug}/survey/status.json）：
  collecting → awaiting_agent → writing → done | failed
  awaiting_agent 即队列（网页/CLI 触发统一表达）；
  writing 由 --auto 端到端流程写入（headless agent 写作中）。
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import paths
from scripts.records import schema as RS
from scripts.records.schema import normalize_url

SURVEY_MD_MAX_BYTES = 40 * 1024
REQUIRED_H2 = ["TL;DR", "核心内容", "分来源摘要", "原始出处"]
DEFAULT_MAX_LINKS = 5

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"


class SurveyError(Exception):
    """带机器可读码的 survey 错误。code 供 CLI/API 输出；next_cmd 指引下一步。"""

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
    """读取 survey 状态；不存在/损坏返回 {}。"""
    path = paths.survey_status_path(slug, ws)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_status(slug: str, ws, state: str, detail: dict = None, error: str = None) -> None:
    """写 survey 状态机。error 为 None 时清除旧 error 字段。"""
    path = paths.survey_status_path(slug, ws)
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

def select_survey_links(record_links: list, max_links: int = DEFAULT_MAX_LINKS) -> list:
    """从 record links 选出本次 survey 要抓的链接。

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


def build_survey_task(slug: str, record: dict, survey_files: list, survey_summary: str,
                    raw_files: list, raw_summary: str, related: list, ws) -> str:
    """构造 survey agent 的 task prompt。"""
    survey_md_out = str(paths.survey_md_path(slug, ws).resolve())
    record_abs = str(paths.record_path(slug, ws).resolve())
    title = record.get("title") or slug
    links_block = "\n".join(
        f"- [{l.get('kind', 'other')}/{l.get('role', 'related')}] {l.get('url')}"
        for l in (record.get("links") or [])
    ) or "- （record 无 links）"
    related_block = "\n".join(f"- {r['id']} (score={r['score']})" for r in related) or "- （无）"
    survey_listing = "\n".join(survey_files) if survey_files else "  (survey/raw/ 为空)"
    raw_listing = "\n".join(raw_files) if raw_files else "  (record raw/ 为空或不存在)"

    return f"""你是知识综述 agent。基于给定材料，为记录 {slug} 写一页**综述**，写入唯一输出文件：
`{survey_md_out}`

## 背景记录（已提取的结构化信息，作为骨架与链接来源）

record.json: `{record_abs}`
标题：{title}
TL;DR：{record.get('tldr') or ''}

record 已提取的 links：
{links_block}

## 可用材料

1. survey 新采集材料（`artifacts/{slug}/survey/raw/`，{survey_summary or '空'}）：
{survey_listing}
2. record 原始材料（`artifacts/{slug}/raw/`，{raw_summary or '空'}）：
{raw_listing}

## 输出契约（survey.md）

写一个 Markdown 文件，结构严格如下：

```markdown
# {title} — 综述
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
5. **禁止执行任何 git 操作**；禁止运行 publish / cli 命令；不要修改 survey.md 以外的任何文件。
6. 完成后只返回简洁摘要（引用了几个来源、总字数）。

## 相关记录（系统提供；如非空，可在文末加一行「相关记录」）

{related_block}
"""


def generate_survey_task(slug: str, ws=None) -> dict:
    """生成 survey 任务 envelope（与 record task envelope 对齐 + task_mode=survey）。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    record = RS.load_record(slug, ws)
    if not record:
        raise SurveyError("RECORD_MISSING", f"record.json not found: {slug}",
                        next_cmd=f"{CLI_CMD} run --id {slug}")
    survey_files, survey_summary = _summarize_dir(paths.survey_raw_dir(slug, ws), f"artifacts/{slug}/survey/raw/")
    raw_files, raw_summary = _summarize_dir(paths.raw_dir(slug, ws), f"artifacts/{slug}/raw/")
    related = _related_entries(paths.db_path(ws), slug)
    task_text = build_survey_task(slug, record, survey_files, survey_summary,
                                raw_files, raw_summary, related, ws)
    # 模型跟随调用方 agent，skill 不配置
    return {
        "task": task_text,
        "taskName": f"survey-{slug}",
        "model": None,
        "fallback": [],
        "mode": "run",
        "task_mode": "survey",
        "cleanup": "keep",
        "context": "isolated",
        "slug": slug,
        "output_path": str(paths.survey_md_path(slug, ws).resolve()),
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


def collect_survey(slug: str, ws=None, max_links: int = DEFAULT_MAX_LINKS, force: bool = False) -> dict:
    """采集 survey 材料 + 生成任务 payload；状态推进到 awaiting_agent。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    record = RS.load_record(slug, ws)
    if not record:
        raise SurveyError("RECORD_MISSING", f"record.json not found: {slug}",
                        next_cmd=f"{CLI_CMD} run --id {slug}")
    if paths.survey_md_path(slug, ws).exists() and not force:
        raise SurveyError("SURVEY_EXISTS", f"survey already exists: {slug}（--force 重新生成）")
    current = read_status(slug, ws)
    if current.get("state") == "collecting" and not force:
        raise SurveyError("SURVEY_RUNNING", f"survey is collecting: {slug}（--force 强制重跑）")

    write_status(slug, ws, "collecting")
    try:
        links = select_survey_links(record.get("links"), max_links=max_links)
        if not links and not _raw_has_material(slug, ws):
            raise SurveyError("NO_MATERIAL", "record 无可抓取的 links，且原 raw/ 无材料")
        summary = {"selected": [l["url"] for l in links], "collected": 0, "failed": 0}
        if links:
            sources = [_classify_for_collect(l["url"]) for l in links]
            log = _collect_sources(slug, sources, max_depth=1,
                                   dest_base=paths.survey_raw_dir(slug, ws))
            s = (log or {}).get("summary", {})
            summary["collected"] = s.get("success", 0)
            summary["failed"] = s.get("failed", 0)
        task = generate_survey_task(slug, ws)
        task_path = paths.survey_task_path(slug, ws)
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        write_status(slug, ws, "awaiting_agent", detail=summary)
        return task
    except SurveyError as e:
        write_status(slug, ws, "failed", error=str(e)[:200])
        raise
    except Exception as e:
        write_status(slug, ws, "failed", error=str(e)[:200])
        raise


def spawn_survey_agent_if_possible(slug: str, ws=None) -> dict:
    """sessions_spawn 可用时自动派发 survey agent；否则保持 awaiting_agent。"""
    import shutil
    from scripts.lib import run_cmd

    ws = Path(ws) if ws is not None else paths.get_workspace()
    exe = shutil.which("sessions_spawn")
    if not exe:
        return {"spawned": False, "reason": "sessions_spawn not found"}
    task_path = paths.survey_task_path(slug, ws)
    if not task_path.exists():
        return {"spawned": False, "reason": "task.json missing"}
    task = json.loads(task_path.read_text(encoding="utf-8"))
    cmd = [exe, "--taskName", task["taskName"],
           "--mode", "run", "--task", task["task"]]
    r = run_cmd(cmd, timeout=60)
    return {"spawned": bool(r["ok"]), "reason": "" if r["ok"] else (r.get("stderr") or "spawn failed")[:200]}


# ============================================================
# 校验 + 发布
# ============================================================

def validate_survey_md(text: str) -> tuple:
    """校验 survey.md 结构契约，返回 (ok, errors)。"""
    import re
    errors = []
    if not text or not text.strip():
        return False, ["survey.md 为空"]
    size = len(text.encode("utf-8"))
    if size > SURVEY_MD_MAX_BYTES:
        errors.append(f"survey.md 过大（{size} > {SURVEY_MD_MAX_BYTES} bytes，即 {SURVEY_MD_MAX_BYTES // 1024}KB）")
    if not re.search(r"^#\s+\S", text, re.M):
        errors.append("missing H1 title（# ...）")
    for h2 in REQUIRED_H2:
        if not re.search(rf"^##\s+{re.escape(h2)}", text, re.M):
            errors.append(f"missing section: ## {h2}")
    if not re.search(r"https?://[^\s)\]]+", text):
        errors.append("no source URL found（原始出处/正文必须含 http(s) 链接）")
    return (len(errors) == 0), errors


def _collect_survey_fetch_sources(slug: str, ws) -> list:
    """从 survey/raw/**/_fetch_results.json 汇总抓取来源（url + status）。"""
    raw = paths.survey_raw_dir(slug, ws)
    sources = []
    seen = set()
    if not raw.exists():
        return sources
    for fr in sorted(raw.rglob("_fetch_results.json")):
        try:
            data = json.loads(fr.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for r in data.get("results", []):
            url = r.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({"url": url, "status": r.get("status") or "unknown"})
    return sources


def publish_survey(slug: str, ws=None, db_path=None) -> dict:
    """发布 survey：结构校验 → survey.json（revision 自增）→ 重建站点 → status=done。"""
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site
    from scripts import wiki_index

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    md_path = paths.survey_md_path(slug, ws)

    if not md_path.exists():
        raise SurveyError("SURVEY_MD_MISSING",
                        f"survey.md missing: {md_path}（先执行 survey task 写页面）")
    text = md_path.read_text(encoding="utf-8", errors="replace")
    ok, errors = validate_survey_md(text)
    if not ok:
        write_status(slug, ws, "failed", error="; ".join(errors)[:300])
        raise SurveyError("SURVEY_VERIFY_FAILED", f"survey.md 校验失败: {'; '.join(errors)}")

    with PublishLock(timeout=30):
        record = RS.load_record(slug, ws) or {}
        meta_path = paths.survey_json_path(slug, ws)
        prev = {}
        if meta_path.exists():
            try:
                prev = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        now = _now_iso()
        meta = {
            "slug": slug,
            "title": record.get("title") or slug,
            "created_at": prev.get("created_at") or now,
            "updated_at": now,
            "revision": int(prev.get("revision") or 0) + 1,
            "sources": _collect_survey_fetch_sources(slug, ws),
            "md_bytes": md_path.stat().st_size,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            wiki_index.record_event(db_path, slug, "SURVEY", {"revision": meta["revision"]})
        except Exception:
            pass
        build_site(db_path, ws)
        write_status(slug, ws, "done", detail={"revision": meta["revision"]})
    return {"ok": True, "id": slug, "revision": meta["revision"],
            "survey": f"artifacts/{slug}/survey/survey.md"}


def _read_survey_meta(slug: str, ws) -> dict:
    path = paths.survey_json_path(slug, ws)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def survey_status(slug: str, ws=None) -> dict:
    """CLI/API 共用的状态视图。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    return {
        "id": slug,
        "status": read_status(slug, ws),
        "has_survey": paths.survey_md_path(slug, ws).exists(),
        "survey": _read_survey_meta(slug, ws),
    }


def list_survey_queue(ws=None) -> list:
    """全部 awaiting_agent 的 survey（agent 的工作清单）。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    adir = paths.artifacts_dir(ws)
    items = []
    if not adir.exists():
        return items
    for d in sorted(adir.iterdir()):
        if not d.is_dir():
            continue
        st = read_status(d.name, ws)
        if st.get("state") == "awaiting_agent":
            items.append({"id": d.name, "updated_at": st.get("updated_at"),
                          "detail": st.get("detail")})
    return items


# ============================================================
# 端到端自动写作（--auto）
# ============================================================

AUTO_WRITE_TIMEOUT = 900  # headless 写作超时（秒）


def _find_writer() -> str:
    """选择写作执行器：sessions_spawn（OpenClaw）→ claude headless → None。"""
    if shutil.which("sessions_spawn"):
        return "sessions_spawn"
    if shutil.which("claude"):
        return "claude"
    return ""


def _claude_headless_runner(prompt: str, ws, timeout: int = AUTO_WRITE_TIMEOUT) -> dict:
    """headless `claude -p` 执行写作：acceptEdits 权限，工作目录=wiki 工作区。

    prompt 已约束 agent 只读材料、只写 survey.md；acceptEdits 放行读写，
    其他需要授权的操作在 headless 下自动拒绝（无害失败）。
    """
    from scripts.lib import headless_write_runner
    return headless_write_runner(prompt, ws, timeout=timeout)


def auto_write_survey(slug: str, ws=None, runner=None, timeout: int = AUTO_WRITE_TIMEOUT,
                      force: bool = False) -> dict:
    """headless agent 写 survey.md 并自动发布。

    runner 可注入（测试）。状态：awaiting_agent → writing → done；
    未产出 survey.md 时落回 awaiting_agent（可重试），校验失败落 failed。
    force=True 时跳过「已有 survey.md 直接发布」捷径（强制重写）。
    """
    ws = Path(ws) if ws is not None else paths.get_workspace()
    md_path = paths.survey_md_path(slug, ws)
    if md_path.exists() and not force:
        # 已写好（例如上次写作成功但发布失败）——直接发布，幂等
        pub = publish_survey(slug, ws, paths.db_path(ws))
        return {"ok": True, "written": False, "published": pub}
    state = read_status(slug, ws).get("state")
    if state == "writing":
        raise SurveyError("SURVEY_RUNNING", f"survey is being written: {slug}")
    task_path = paths.survey_task_path(slug, ws)
    if not task_path.exists():
        raise SurveyError("TASK_MISSING", f"task.json missing: {slug}",
                          next_cmd=f"{CLI_CMD} survey --id {slug}")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    write_status(slug, ws, "writing")
    runner = runner or _claude_headless_runner
    result = runner(task["task"], ws, timeout=timeout)
    if not md_path.exists():
        err = ((result or {}).get("stderr") or (result or {}).get("stdout") or "")[-300:]
        write_status(slug, ws, "awaiting_agent",
                     error=f"auto-write 未产出 survey.md: {err or 'no output'}")
        return {"ok": False, "written": False, "reason": "no_survey_md", "detail": err}
    pub = publish_survey(slug, ws, paths.db_path(ws))
    return {"ok": True, "written": True, "published": pub}


def auto_execute_survey(slug: str, ws=None, runner=None, force: bool = False) -> dict:
    """端到端自动综述：必要时采集 → 写作（sessions_spawn/claude headless）→ 发布。

    - survey.md 不存在（或 force）且状态非 awaiting_agent：先 collect（awaiting_agent 表示材料就绪，跳过）
    - sessions_spawn 可用：异步派发，停在 awaiting_agent 由 OpenClaw 编排方收尾
    - claude 可用：headless 同步写作 + 自动发布（真正端到端）
    - 都不可用：停在 awaiting_agent（队列，等 agent 手动认领）
    """
    ws = Path(ws) if ws is not None else paths.get_workspace()
    if force or not paths.survey_md_path(slug, ws).exists():
        state = read_status(slug, ws).get("state")
        if state == "writing" and not force:
            raise SurveyError("SURVEY_RUNNING", f"survey is being written: {slug}")
        if force or state != "awaiting_agent":
            collect_survey(slug, ws, force=force)
    writer = _find_writer()
    if writer == "sessions_spawn":
        spawn = spawn_survey_agent_if_possible(slug, ws)
        return {"ok": True, "mode": "spawned", "spawn": spawn,
                "note": "sessions_spawn 异步执行；完成后需 survey --publish 入站"}
    if writer != "claude":
        return {"ok": False, "mode": "queued",
                "reason": "no writer available（sessions_spawn / claude 均未找到）"}
    result = auto_write_survey(slug, ws, runner=runner, force=force)
    result["mode"] = "headless-claude"
    return result
