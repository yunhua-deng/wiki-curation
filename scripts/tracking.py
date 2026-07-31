#!/usr/bin/env python3
"""
scripts/tracking.py — 实体跟踪主题（tracking topics）的确定性逻辑。

针对感兴趣的人物/实体建立长期跟踪：确定性关联已入库 records（别名展开 + entities/tags/title 匹配
+ FTS 兜底），digest.md 由 headless agent 撰写（--auto）。refresh 做增量关联 + 已知来源抓取 +
digest 重生成。系统不猜 URL——sources 只来自显式登记（CLI 参数），防止幻觉源。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import paths

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"

DEFAULT_INTERVAL_DAYS = 14
MAX_REFRESH_SOURCES = 4
SOURCE_KEYS = ("homepage", "github", "scholar", "arxiv", "blog", "linkedin")
REQUIRED_H2 = ["画像", "动态时间线", "已关联记录"]


class TrackError(Exception):
    """带机器可读码的 tracking 错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def slugify_name(name: str) -> str:
    """实体名 → 目录 slug（小写、空格转连字符；CJK 保留）。"""
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9一-鿿_-]", "", s)
    return (s or "topic").strip("-_")[:48]


# ============================================================
# topic.json 存取
# ============================================================

def topic_json_path(slug: str, ws=None) -> Path:
    return paths.tracking_topic_dir(slug, ws) / "topic.json"


def load_topic(slug: str, ws=None) -> dict:
    p = topic_json_path(slug, ws)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_topic(topic: dict, ws=None) -> Path:
    slug = topic["slug"]
    p = topic_json_path(slug, ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    topic["updated_at"] = _now_iso()
    p.write_text(json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def list_topics(ws=None, include_archived: bool = False) -> list:
    root = paths.tracking_dir(ws)
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        t = load_topic(d.name, ws)
        if not t:
            continue
        if t.get("status") == "archived" and not include_archived:
            continue
        out.append(t)
    return out


# ============================================================
# 记录关联（确定性）
# ============================================================

def _alias_variants(name: str) -> set:
    """entity_aliases.yaml 反查：name 是 canonical 或任意别名 → 返回全部写法（小写）。"""
    variants = {name.strip().lower()}
    try:
        from scripts.site.entities import load_aliases
        aliases = load_aliases() or {}
    except Exception:
        aliases = {}
    buckets = dict(aliases.get("terms") or {})
    for _et, ent_map in (aliases.get("entities") or {}).items():
        buckets.update(ent_map or {})
    for canonical, vars_ in buckets.items():
        all_forms = {str(canonical).strip().lower()} | {str(v).strip().lower() for v in (vars_ or [])}
        if name.strip().lower() in all_forms:
            variants |= all_forms
    return variants


def find_records_for_entity(db_path, name: str, limit: int = 50) -> list:
    """关联已入库 records：entities 四桶/title/tags 匹配（别名展开）+ FTS 兜底。"""
    from scripts.wiki_index import store as wiki_store

    db_path = Path(db_path)
    variants = _alias_variants(name)
    hits: dict = {}
    for e in wiki_store.list_entries(db_path):
        eid = e.get("id") or ""
        matched = None
        ents = e.get("entities")
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except Exception:
                ents = {}
        for bucket, vals in (ents or {}).items():
            for v in (vals or []):
                if str(v).strip().lower() in variants:
                    matched = f"entity:{bucket}"
                    break
            if matched:
                break
        if not matched:
            hay_title = (e.get("title") or "").lower()
            hay_tags = (e.get("tags") or "").lower()
            for v in variants:
                if v and v in hay_title:
                    matched = "title"
                    break
                if v and v in hay_tags:
                    matched = "tag"
                    break
        if matched:
            hits[eid] = {"id": eid, "title": e.get("title") or eid,
                         "date": e.get("date") or "", "match": matched}
    if not hits:
        # FTS 兜底
        try:
            rows = wiki_store.search_entries(db_path, name, limit=limit)
            for r in rows or []:
                eid = r.get("id") or ""
                if eid and eid not in hits:
                    hits[eid] = {"id": eid, "title": r.get("title") or eid,
                                 "date": r.get("date") or "", "match": "fts"}
        except Exception:
            pass
    out = sorted(hits.values(), key=lambda x: x.get("date") or "", reverse=True)
    return out[:limit]


# ============================================================
# 创建
# ============================================================

def create_topic(name: str, kind: str = "person", sources: dict = None,
                 interval_days: int = DEFAULT_INTERVAL_DAYS, ws=None, db_path=None) -> dict:
    """创建跟踪主题：slug 化 → 关联 records → topic.json 落盘。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    name = (name or "").strip()
    if not name:
        raise TrackError("MISSING_NAME", "track 需要 --name")
    slug = slugify_name(name)
    if load_topic(slug, ws):
        raise TrackError("TOPIC_EXISTS", f"跟踪主题已存在: {slug}")

    records = find_records_for_entity(db_path, name)
    src = {k: "" for k in SOURCE_KEYS}
    for k, v in (sources or {}).items():
        if k in src and v:
            src[k] = v
    topic = {
        "slug": slug,
        "name": name,
        "kind": kind or "person",
        "aliases": sorted(_alias_variants(name) - {name.strip().lower()}),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "sources": src,
        "refresh": {"interval_days": interval_days, "last_at": "", "next_due": ""},
        "records": [r["id"] for r in records],
        "records_detail": records,
        "status": "active",
    }
    save_topic(topic, ws)
    return topic


def archive_topic(slug: str, ws=None) -> dict:
    topic = load_topic(slug, ws)
    if not topic:
        raise TrackError("TOPIC_MISSING", f"跟踪主题不存在: {slug}")
    topic["status"] = "archived"
    save_topic(topic, ws)
    return topic


def update_sources(slug: str, sources: dict, ws=None) -> dict:
    """显式登记来源 URL（系统不猜 URL；agent 建议的来源经此登记后生效）。"""
    topic = load_topic(slug, ws)
    if not topic:
        raise TrackError("TOPIC_MISSING", f"跟踪主题不存在: {slug}")
    for k, v in (sources or {}).items():
        if k in SOURCE_KEYS and v:
            topic.setdefault("sources", {})[k] = v
    save_topic(topic, ws)
    return topic


def due_topics(ws=None, today: str = None) -> list:
    """到期主题：next_due <= today（含从未 refresh 的）。"""
    today = today or _today()
    out = []
    for t in list_topics(ws):
        nd = ((t.get("refresh") or {}).get("next_due") or "")
        if not nd or nd <= today:
            out.append(t)
    return out


# ============================================================
# digest 生成（agent 写作）+ refresh
# ============================================================

def digest_path(slug: str, ws=None) -> Path:
    return paths.tracking_topic_dir(slug, ws) / "digest.md"


_RECORD_ID_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}_[\w-]+")


def build_digest_task(topic: dict, ws, new_record_ids: list = None,
                      raw_listing: str = "（无新材料）") -> str:
    """构造 digest 写作 agent 的 task prompt。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    out = str(digest_path(topic["slug"], ws).resolve()).replace("digest.md", "digest.new.md")
    records = topic.get("records_detail") or []
    rec_lines = [f"- [{r['id']}]（{r.get('date') or '?'}）{r.get('title') or r['id']}（{r.get('match')}）"
                 for r in records] or ["- （暂无关联记录）"]
    sources = topic.get("sources") or {}
    src_lines = [f"- {k}: {v}" for k, v in sources.items() if v] or ["- （未登记来源）"]
    old_digest = ""
    dp = digest_path(topic["slug"], ws)
    if dp.exists():
        try:
            old_digest = dp.read_text(encoding="utf-8", errors="replace")[:1500]
        except Exception:
            old_digest = ""
    new_note = ""
    if new_record_ids:
        new_note = f"\n本次新增关联记录：{', '.join(new_record_ids)}\n"
    revision = int(topic.get("digest_revision") or 0) + 1
    rec_block = "\n".join(rec_lines)
    src_block = "\n".join(src_lines)

    return f"""你是技术情报分析 agent。为实体 **{topic['name']}**（{topic.get('kind') or 'person'}）撰写/更新跟踪页，写入唯一输出文件：
`{out}`
{new_note}
## 已关联的 wiki 记录（系统确定性匹配，{len(records)} 条；可据 id 到 wiki/artifacts/{{id}}/ 读 record.json 与 raw/ 深入）

{rec_block}

## 已知来源（系统登记的 URL）

{src_block}

## 本次 refresh 新抓材料（tracking/{topic['slug']}/raw/ 下）

{raw_listing}

## 旧版 digest（如存在，在其基础上更新，保留仍准确的内容）

{old_digest or "（无，本次为首版）"}

## 输出契约（digest.new.md）

```markdown
# {topic['name']} — 跟踪
> 更新：{_today()} · 第 {revision} 版 · 关联 {len(records)} 条记录

## 画像
这是谁、研究/事业脉络、当前重心（基于关联记录证据，3-6 句）。

## 动态时间线
按日期倒序列出关联记录：- [id]（date）标题——一句话

## 近期线索
新材料里的新信息（2-5 句）；无则写「暂无新线索」。

## 已关联记录
- id 列表

## 来源
- 已知 URL；可附「建议登记」的来源（须标注"建议"，由人工/agent 显式登记后生效）
```

## 硬性规则

1. 内容可溯源到关联记录或已抓材料；**禁止编造 URL**；建议来源必须标注"建议"。
2. 客观陈述，不评价。
3. **禁止执行任何 git 操作**；不要写 digest.new.md 以外的文件；不要运行 publish / cli 命令。
4. 完成后只返回一句话摘要。
"""


def validate_digest_md(text: str, topic: dict) -> tuple:
    """校验 digest：H1 + 必需小节 + ≥1 个关联记录 id。"""
    errors = []
    if not text or not text.strip():
        return False, ["digest 为空"]
    if not re.search(r"^#\s+\S", text, re.M):
        errors.append("missing H1 title（# ...）")
    for h2 in REQUIRED_H2:
        if not re.search(rf"^##\s+{re.escape(h2)}", text, re.M):
            errors.append(f"missing section: ## {h2}")
    ids = (topic or {}).get("records") or []
    if ids and not any(re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text) for i in ids):
        errors.append("no associated record id reference（已关联记录小节必须含至少一个关联记录 id）")
    return (len(errors) == 0), errors


def auto_write_digest(slug: str, ws=None, db_path=None, runner=None,
                      new_record_ids: list = None, raw_listing: str = "（无新材料）",
                      timeout: int = 900) -> dict:
    """headless 写 digest.new.md → 校验 → 替换 digest.md → revision+1 → 重建站点。"""
    from scripts.lib import headless_write_runner
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    topic = load_topic(slug, ws)
    if not topic:
        raise TrackError("TOPIC_MISSING", f"跟踪主题不存在: {slug}")

    task_text = build_digest_task(topic, ws, new_record_ids=new_record_ids,
                                  raw_listing=raw_listing)
    new_path = digest_path(slug, ws).parent / "digest.new.md"
    new_path.unlink(missing_ok=True)
    runner = runner or headless_write_runner
    result = runner(task_text, ws, timeout=timeout)
    if not new_path.exists():
        err = ((result or {}).get("stderr") or (result or {}).get("stdout") or "")[-300:]
        raise TrackError("WRITE_FAILED", f"digest 写作未产出文件: {err or 'no output'}")
    text = new_path.read_text(encoding="utf-8", errors="replace")
    ok, errors = validate_digest_md(text, topic)
    if not ok:
        new_path.unlink(missing_ok=True)
        raise TrackError("DIGEST_VERIFY_FAILED", f"digest 校验失败: {'; '.join(errors)}")

    with PublishLock(timeout=30):
        final = digest_path(slug, ws)
        final.write_text(text, encoding="utf-8")
        new_path.unlink(missing_ok=True)
        topic["digest_revision"] = int(topic.get("digest_revision") or 0) + 1
        save_topic(topic, ws)
        build_site(db_path, ws)
    return {"ok": True, "slug": slug, "revision": topic["digest_revision"],
            "digest": f"tracking/{slug}/digest.md"}


def _fetch_known_sources(topic: dict, ws) -> dict:
    """抓取已登记来源（≤4，max_depth=1）到 raw/{today}/。系统不猜 URL。"""
    urls = [v for k, v in (topic.get("sources") or {}).items() if v]
    if not urls:
        return {"fetched": 0, "failed": 0, "urls": []}
    from scripts.records.survey import _classify_for_collect
    from scripts.exec.collect_materials import collect_sources
    slug = topic["slug"]
    dest = paths.tracking_topic_dir(slug, ws) / "raw" / _today()
    sources = [_classify_for_collect(u) for u in urls[:MAX_REFRESH_SOURCES]]
    try:
        log = collect_sources(f"track-{slug}", sources, max_depth=1, dest_base=dest)
        s = (log or {}).get("summary", {})
        return {"fetched": s.get("success", 0), "failed": s.get("failed", 0),
                "urls": urls[:MAX_REFRESH_SOURCES]}
    except Exception as e:
        return {"fetched": 0, "failed": len(sources), "urls": urls[:MAX_REFRESH_SOURCES],
                "error": str(e)[:200]}


def _raw_listing(slug: str, ws) -> str:
    raw = paths.tracking_topic_dir(slug, ws) / "raw"
    lines = []
    if raw.exists():
        for f in sorted(raw.rglob("*")):
            if f.is_file() and not f.name.startswith("_"):
                lines.append(f"  {f.relative_to(raw)}")
    return "\n".join(lines) or "（无新材料）"


def refresh_topic(slug: str, ws=None, db_path=None, runner=None, auto: bool = True) -> dict:
    """refresh：增量关联 records → 抓已知来源 → 重生成 digest → 更新 refresh 时间戳。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    topic = load_topic(slug, ws)
    if not topic:
        raise TrackError("TOPIC_MISSING", f"跟踪主题不存在: {slug}")
    if topic.get("status") == "archived":
        raise TrackError("TOPIC_ARCHIVED", f"跟踪主题已归档: {slug}")

    records = find_records_for_entity(db_path, topic["name"])
    old_ids = set(topic.get("records") or [])
    new_ids = [r["id"] for r in records if r["id"] not in old_ids]
    topic["records"] = [r["id"] for r in records]
    topic["records_detail"] = records

    fetch = _fetch_known_sources(topic, ws)
    digest_result = None
    if auto:
        digest_result = auto_write_digest(slug, ws, db_path, runner=runner,
                                          new_record_ids=new_ids,
                                          raw_listing=_raw_listing(slug, ws))

    interval = int((topic.get("refresh") or {}).get("interval_days") or DEFAULT_INTERVAL_DAYS)
    next_due = (datetime.now(timezone.utc) + timedelta(days=interval)).strftime("%Y-%m-%d")
    topic["refresh"] = {"interval_days": interval, "last_at": _today(), "next_due": next_due}
    save_topic(topic, ws)

    if not auto:
        from scripts.publish.lock import PublishLock
        from scripts.site.build import build_site
        with PublishLock(timeout=30):
            build_site(db_path, ws)
    return {"ok": True, "slug": slug, "new_records": new_ids, "fetch": fetch,
            "digest": digest_result, "next_due": next_due}
