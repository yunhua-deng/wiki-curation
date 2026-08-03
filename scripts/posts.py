#!/usr/bin/env python3
"""
scripts/posts.py — 技术 post（原 trends 改造）的确定性逻辑。

定位：面向课题/问题/主题的技术 blog/post，**默认基于 wiki 已有记录取证**，
必要时才补充外部材料。三个触发情景：--topic（主题聚簇取证）、--records（多记录融合）、
--suggest（hub 检测的确定性建议）。写作由 headless agent 完成（--auto），系统负责取证、
任务生成、校验、落位与站点索引。
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import paths
from scripts.records import schema as RS

POST_MIN_CHARS = 800
POST_MAX_CHARS = 20000
SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"

_RECORD_ID_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}_[\w-]+")
_URL_RE = re.compile(r"https?://[^\s)\]]+")


class PostError(Exception):
    """带机器可读码的 post 错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def slugify(text: str, max_len: int = 40) -> str:
    """标题/topic → 文件名安全 slug（保留中日韩字符）。"""
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9一-鿿_-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return (s or "post")[:max_len].rstrip("-_")


# ============================================================
# 取证
# ============================================================

def _record_evidence(slug: str, ws) -> dict:
    """单条记录的取证视图：record.json 优先，entry 兜底。"""
    record = RS.load_record(slug, ws) or {}
    return {
        "id": slug,
        "title": record.get("title") or slug,
        "date": record.get("date") or "",
        "tldr": record.get("tldr") or "",
        "summary": record.get("summary") or "",
        "tags": record.get("tags") or [],
        "links": [l.get("url") for l in (record.get("links") or []) if l.get("url")][:6],
    }


def gather_topic_evidence(db_path, ws, topic: str, limit: int = 8) -> list:
    """主题取证：analyze.cluster 聚簇 → 每条的 record 级证据。"""
    from scripts.records import analyze as AZ
    cl = AZ.cluster(db_path, topic, limit=limit)
    out = []
    for e in (cl.get("entries") or [])[:limit]:
        ev = _record_evidence(e["id"], ws)
        ev["hit"] = e.get("hit") or e.get("reason") or ""
        out.append(ev)
    return out


def gather_records_evidence(db_path, ws, ids: list) -> list:
    """多记录融合取证：按 id 显式取。"""
    out = []
    missing = []
    for slug in ids:
        slug = slug.strip()
        if not slug:
            continue
        if not RS.load_record(slug, ws):
            missing.append(slug)
            continue
        out.append(_record_evidence(slug, ws))
    if missing:
        raise PostError("RECORD_MISSING", f"以下记录无 record.json: {', '.join(missing)}")
    if not out:
        raise PostError("NO_RECORDS", "未指定有效记录")
    return out


# ============================================================
# 任务生成
# ============================================================

def build_post_task(trigger: dict, evidence: list, staging_path: Path) -> str:
    """构造 post 写作 agent 的 task prompt（blog 风格契约）。"""
    if trigger.get("kind") == "topic":
        trigger_desc = f"主题直接发起：**{trigger['topic']}**\n（默认基于 wiki 已有记录写作；证据不足且确有必要时才补充外部材料，且必须真实可引用）"
    else:
        trigger_desc = f"多记录融合分析：{', '.join(trigger['ids'])}\n（围绕这些记录的关联与张力做融合分析）"
    ev_lines = []
    for ev in evidence:
        links_str = "; ".join(ev.get("links") or []) or "（无）"
        ev_lines.append(
            f"- [{ev['id']}]（{ev.get('date') or '?'}）{ev.get('title') or ev['id']}\n"
            f"  TL;DR: {ev.get('tldr') or '—'}\n"
            + (f"  摘要: {ev['summary'][:400]}\n" if ev.get("summary") else "")
            + f"  tags: {', '.join(ev.get('tags') or []) or '—'}  links: {links_str}"
        )
    ev_block = "\n".join(ev_lines) or "- （无证据记录）"
    return f"""你是技术 blog 作者。基于 wiki 知识库的记录证据，写一篇**技术 post**，写入唯一输出文件：
`{staging_path}`

## 触发方式

{trigger_desc}

## 证据（来自 wiki 记录，含已提取的 TL;DR/摘要/links；可据 id 到 wiki/artifacts/{{id}}/ 下读 record.json 与 raw/ 材料深入）

{ev_block}

## 输出契约（blog post 风格）

1. H1 标题（吸引人但准确，不标题党）。
2. 开头一段 hook：为什么值得读（问题/现象/张力）。
3. 主体 2-5 节（## 小节）：叙事性论述；**证据行内引用**——wiki 记录用 `[记录 id]` 形式，外部材料用完整 URL。
4. 结尾：open questions 或值得跟踪的点（1-3 条）。
5. 篇幅 {POST_MIN_CHARS}-{POST_MAX_CHARS} 字符。

## 硬性规则

1. **默认基于 wiki 证据**；外部补充仅限确有必要，禁止编造 URL/数据。
2. 允许分析与观点，但每个论断必须能对应到证据（record id 或 URL）。
3. **禁止执行任何 git 操作**；不要写目标文件以外的任何文件；不要运行 publish / cli 命令。
4. 完成后只返回一句话摘要。
"""


def generate_post_task(trigger: dict, ws=None, db_path=None, limit: int = 8) -> dict:
    """生成 post 任务 envelope。trigger: {"kind":"topic","topic":X} 或 {"kind":"records","ids":[...]}。"""
    from scripts.route_model import select_model

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    if trigger.get("kind") == "topic":
        if not (trigger.get("topic") or "").strip():
            raise PostError("MISSING_TOPIC", "post --topic 需要主题")
        evidence = gather_topic_evidence(db_path, ws, trigger["topic"].strip(), limit=limit)
        if not evidence:
            raise PostError("NO_EVIDENCE", f"主题在 wiki 内无关联记录: {trigger['topic']}")
        slug = slugify(trigger["topic"])
    elif trigger.get("kind") == "records":
        evidence = gather_records_evidence(db_path, ws, trigger.get("ids") or [])
        slug = slugify((evidence[0].get("title") or "fusion")[:30])
    else:
        raise PostError("INVALID_TRIGGER", "trigger.kind 必须是 topic 或 records")

    staging_dir = paths.post_staging_dir(ws)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_path = staging_dir / f"{_today()}-{slug}.md"
    task_text = build_post_task(trigger, evidence, staging_path)
    model_info = select_model("post")
    return {
        "task": task_text,
        "taskName": f"post-{slug}",
        "model": model_info["model"],
        "fallback": model_info.get("fallback", []),
        "mode": "run",
        "task_mode": "post",
        "cleanup": "keep",
        "context": "isolated",
        "trigger": trigger,
        "evidence_count": len(evidence),
        "staging_path": str(staging_path.resolve()),
    }


# ============================================================
# 校验 + 落位
# ============================================================

def validate_post_md(text: str) -> tuple:
    """校验 post：H1 + 篇幅 + 至少一个证据引用（record id 或 URL）。"""
    errors = []
    if not text or not text.strip():
        return False, ["post 为空"]
    n = len(text)
    if n < POST_MIN_CHARS:
        errors.append(f"post 过短（{n} < {POST_MIN_CHARS} 字符）")
    if n > POST_MAX_CHARS:
        errors.append(f"post 过长（{n} > {POST_MAX_CHARS} 字符）")
    if not re.search(r"^#\s+\S", text, re.M):
        errors.append("missing H1 title（# ...）")
    if not (_RECORD_ID_RE.search(text) or _URL_RE.search(text)):
        errors.append("no evidence reference（至少引用一个 record id 或 http(s) 链接）")
    return (len(errors) == 0), errors


def _next_stem(posts_dir: Path, date_str: str, slug: str) -> str:
    """当日下一个序号：{date}_{NN}-{slug}。"""
    nn = 1
    if posts_dir.exists():
        for p in posts_dir.glob(f"{date_str}_*.md"):
            m = re.match(rf"{re.escape(date_str)}_(\d+)-", p.name)
            if m:
                nn = max(nn, int(m.group(1)) + 1)
    return f"{date_str}_{nn:02d}-{slug}"


def publish_post_file(staging_path, ws, trigger: dict, model: str = "") -> dict:
    """校验 staging 文件 → 落位 posts/ + meta.json → 重建站点。返回 {file, stem}。"""
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site

    ws = Path(ws) if ws is not None else paths.get_workspace()
    staging_path = Path(staging_path)
    if not staging_path.exists():
        raise PostError("FILE_MISSING", f"staging file missing: {staging_path}")
    text = staging_path.read_text(encoding="utf-8", errors="replace")
    ok, errors = validate_post_md(text)
    if not ok:
        raise PostError("POST_VERIFY_FAILED", f"post 校验失败: {'; '.join(errors)}")

    slug = slugify(trigger.get("topic") or "-".join(trigger.get("ids") or []) or "post")
    with PublishLock(timeout=30):
        posts_dir = paths.posts_dir(ws)
        posts_dir.mkdir(parents=True, exist_ok=True)
        stem = _next_stem(posts_dir, _today(), slug)
        final = posts_dir / f"{stem}.md"
        final.write_text(text, encoding="utf-8")
        staging_path.unlink(missing_ok=True)
        meta = {"stem": stem, "trigger": trigger, "model": model, "created_at": _now_iso()}
        (posts_dir / f"{stem}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        build_site(paths.db_path(ws), ws)
    return {"ok": True, "file": f"posts/{stem}.md", "stem": stem}


def auto_write_post(trigger: dict, ws=None, db_path=None, runner=None,
                    timeout: int = 900) -> dict:
    """端到端：取证 → task → headless 写作 → 校验 → 落位 → 站点重建。"""
    from scripts.lib import headless_write_runner

    ws = Path(ws) if ws is not None else paths.get_workspace()
    task = generate_post_task(trigger, ws, db_path)
    runner = runner or headless_write_runner
    result = runner(task["task"], ws, timeout=timeout)
    staging = Path(task["staging_path"])
    if not staging.exists():
        err = ((result or {}).get("stderr") or (result or {}).get("stdout") or "")[-300:]
        raise PostError("WRITE_FAILED", f"headless 写作未产出文件: {err or 'no output'}")
    pub = publish_post_file(staging, ws, trigger, model=task.get("model") or "")
    return {"ok": True, **pub, "evidence_count": task["evidence_count"]}


# ============================================================
# 建议（hub 检测，确定性）
# ============================================================

def suggest_post_topics(db_path, min_degree: int = 3, top_n: int = 5, ws=None) -> list:
    """高内部关联 record → 建议的融合分析主题。"""
    from scripts.records.links import get_all_relations
    from scripts.wiki_index import store as wiki_store
    from scripts.wiki_index.store import _tags_to_list

    db_path = Path(db_path)
    entries = {e["id"]: e for e in wiki_store.list_entries(db_path)}
    deg: dict = {}
    neighbors: dict = {}
    for r in get_all_relations(db_path):
        a, b, s = r["entry_a"], r["entry_b"], (r.get("score") or 0)
        for eid, other in ((a, b), (b, a)):
            d = deg.setdefault(eid, {"degree": 0, "score": 0.0})
            d["degree"] += 1
            d["score"] += s
            neighbors.setdefault(eid, {})[other] = neighbors.setdefault(eid, {}).get(other, 0) + s

    covered = set()
    if ws is not None:
        posts_dir = paths.posts_dir(ws)
        if posts_dir.exists():
            for p in posts_dir.glob("*.md"):
                try:
                    covered.update(_RECORD_ID_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
                except Exception:
                    pass

    hubs = sorted(((eid, d) for eid, d in deg.items() if d["degree"] >= min_degree),
                  key=lambda kv: -kv[1]["score"])
    ignored = set(load_ignored(ws) if ws is not None else [])
    out = []
    for eid, d in hubs[:top_n + len(ignored)]:
        if eid in ignored:
            continue
        e = entries.get(eid) or {}
        top_neighbors = sorted(neighbors.get(eid, {}).items(), key=lambda kv: -kv[1])[:4]
        ids = [eid] + [nid for nid, _ in top_neighbors]
        out.append({
            "anchor": eid,
            "title": e.get("title") or eid,
            "degree": d["degree"],
            "score": round(d["score"], 1),
            "tags": _tags_to_list(e.get("tags"))[:4],
            "records": ids,
            "covered": eid in covered,
            "suggested_cmd": f"{CLI_CMD} --json post --records {','.join(ids)} --auto",
        })
    return out


# ============================================================
# 建议忽略清单 + 系列整合（merge）
# ============================================================

def _ignored_path(ws) -> Path:
    return paths.posts_dir(ws) / ".ignored_suggestions.json"


def load_ignored(ws) -> list:
    p = _ignored_path(ws)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [x for x in (data if isinstance(data, list) else []) if isinstance(x, str)]


def ignore_suggestion(anchor: str, ws=None) -> dict:
    """把一条建议永久忽略（写入忽略清单，重建站点）。"""
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site

    ws = Path(ws) if ws is not None else paths.get_workspace()
    ignored = load_ignored(ws)
    if anchor not in ignored:
        ignored.append(anchor)
        _ignored_path(ws).parent.mkdir(parents=True, exist_ok=True)
        _ignored_path(ws).write_text(json.dumps(ignored, ensure_ascii=False, indent=2), encoding="utf-8")
    with PublishLock(timeout=30):
        build_site(paths.db_path(ws), ws)
    return {"ok": True, "anchor": anchor, "ignored_count": len(ignored)}


def merge_posts(stems: list, ws=None, db_path=None, runner=None, timeout: int = 900) -> dict:
    """整合多篇 post 为一篇：收集原文 + 相关 record 证据 → headless 写作 →
    校验落位 → 原 post 移入 posts/_merged/（保留历史，站点不再列出）。"""
    from scripts.lib import headless_write_runner
    from scripts.records import schema as RS

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    posts_dir = paths.posts_dir(ws)
    if len(stems) < 2:
        raise PostError("MERGE_NEEDS_2", "post --merge 至少需要两篇")

    originals = []
    record_ids = []
    for stem in stems:
        md = posts_dir / f"{stem}.md"
        if not md.exists():
            raise PostError("POST_MISSING", f"post 不存在: {stem}")
        originals.append(md.read_text(encoding="utf-8", errors="replace"))
        try:
            meta = json.loads((posts_dir / f"{stem}.meta.json").read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        tr = meta.get("trigger") or {}
        record_ids += tr.get("ids", []) or ([tr["topic"]] if tr.get("kind") == "topic" else [])
    # 关联 record 证据（按 id 取；topic 触发则回退标题取证）
    evidence = []
    seen = set()
    for rid in record_ids:
        if not rid or rid in seen:
            continue
        seen.add(rid)
        ev = _record_evidence(rid, ws)
        if ev.get("tldr") or ev.get("title"):
            evidence.append(ev)
    merged_title = "整合：" + "｜".join(
        re.sub(r"^#+\s*", "", p.splitlines()[0]).strip()[:24] for p in originals)[:80]
    slug = slugify(merged_title.replace("整合：", ""))[:36]

    staging = paths.post_staging_dir(ws) / f"{_today()}-{slug}.md"
    staging.parent.mkdir(parents=True, exist_ok=True)
    task = build_merge_task(merged_title, originals, evidence, staging)
    runner = runner or headless_write_runner
    result = runner(task, ws, timeout=timeout)
    if not staging.exists():
        err = ((result or {}).get("stderr") or (result or {}).get("stdout") or "")[-300:]
        raise PostError("WRITE_FAILED", f"merge 写作未产出文件: {err or 'no output'}")
    pub = publish_post_file(staging, ws, {"kind": "merge", "merged": stems},
                            model="")
    # 归档被合并的 post
    merged_dir = posts_dir / "_merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        for suf in (".md", ".meta.json"):
            src = posts_dir / f"{stem}{suf}"
            if src.exists():
                src.replace(merged_dir / f"{stem}{suf}")
    return {"ok": True, **pub, "merged_stems": stems, "evidence_count": len(evidence)}


def build_merge_task(title: str, originals: list, evidence: list, staging_path: Path) -> str:
    """构造 merge 写作 agent 的 task prompt。"""
    orig_block = "\n\n---\n\n".join(f"### 原文 {i + 1}\n\n{o[:6000]}" for i, o in enumerate(originals))
    ev_lines = [f"- [{e['id']}]（{e.get('date') or '?'}）{e.get('title') or e['id']}——{e.get('tldr') or ''}"
                for e in evidence[:20]]
    ev_block = "\n".join(ev_lines) or "- （无独立证据）"
    return f"""你是技术 blog 作者。把下面 {len(originals)} 篇**同主题 post**整合为**一篇**更完整的文章，写入任务指定的输出文件（staging 路径）。

## 任务

1. 识别这些 post 的共同主线（它们属于同一系列/主题）。
2. 写一篇整合文：H1 标题（点明共同主题，非"整合："拼接）+ hook + 按主线组织的 3-6 个小节 + 收尾展望。
3. 消除重复论述；保留每篇的独特视角与关键证据；证据行内引用 record id（如 [2026-07-25_01-data-closed-loop-architecture] 所在记录）或 URL。
4. 篇幅 1200-6000 字符。

## 原文材料

{orig_block}

## 关联记录证据（wiki 内，可深入 artifacts/ 阅读）

{ev_block}

## 硬性规则

1. 内容可溯源到原文或关联记录；禁止编造 URL/数据。
2. **禁止执行任何 git 操作**；不要写目标文件以外的文件；不要运行 publish / cli 命令。
3. 完成后只返回一句话摘要。
"""
