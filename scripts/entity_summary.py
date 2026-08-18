#!/usr/bin/env python3
"""entity_summary.py — 实体综合层：watch 清单 + 实体聚合 + 可选 LLM 摘要管线。

实体数据源 = entries.entities 列（publish 时 alias 归一后的 canonical 实体，record.json 四桶）。
LLM 摘要部分（build_summary_task / validate_summary_md / auto_write_summary）在文件后半部分。
"""
import difflib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts import paths
from scripts.lib import slugify_name

ENTITY_BUCKETS = ("company", "author", "product", "series")


class EntityError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# watch 清单（wiki.db entity_watch 表）
# ---------------------------------------------------------------------------
def watch_entity(db_path, name, type="", note="") -> dict:
    name = (name or "").strip()
    if not name:
        raise EntityError("MISSING_NAME", "实体名不能为空")
    conn = _connect(db_path)
    row = conn.execute("SELECT name FROM entity_watch WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return {"name": name, "already_watched": True}
    conn.execute("INSERT INTO entity_watch (name, type, note, created_at) VALUES (?,?,?,?)",
                 (name, type or "", note or "", _now_iso()))
    conn.commit()
    conn.close()
    return {"name": name, "already_watched": False}


def unwatch_entity(db_path, name) -> bool:
    conn = _connect(db_path)
    cur = conn.execute("DELETE FROM entity_watch WHERE name = ?", ((name or "").strip(),))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_watched(db_path) -> list:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT name, type, note, created_at FROM entity_watch ORDER BY created_at").fetchall()
    conn.close()
    return [{"name": r["name"], "type": r["type"], "note": r["note"],
             "created_at": r["created_at"]} for r in rows]


def watched_hits(db_path, names) -> list:
    """names（canonical 实体名列表）中命中 watch 清单的子集（保持输入顺序）。"""
    if not names:
        return []
    conn = _connect(db_path)
    watched = {r[0] for r in conn.execute("SELECT name FROM entity_watch")}
    conn.close()
    return [n for n in names if n in watched]


# ---------------------------------------------------------------------------
# 实体索引与聚合（数据源：entries.entities 列，canonical）
# ---------------------------------------------------------------------------
def flatten_entities(entities) -> list:
    out = []
    for b in ENTITY_BUCKETS:
        out.extend((entities or {}).get(b) or [])
    return out


def entity_index(db_path) -> dict:
    """全部 canonical 实体 → {"type": 桶, "entries": [slug...]}。"""
    from scripts.records.links import all_entry_entities
    index = {}
    for slug, ents in all_entry_entities(db_path).items():
        for b in ENTITY_BUCKETS:
            for name in ents.get(b) or []:
                slot = index.setdefault(name, {"type": b, "entries": []})
                if slug not in slot["entries"]:
                    slot["entries"].append(slug)
    return index


def find_entity(db_path, name):
    """精确（大小写不敏感）匹配实体名；找不到返回 None。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    for ename, slot in entity_index(db_path).items():
        if ename.lower() == key:
            return {"name": ename, **slot}
    return None


def aggregate_entity(db_path, name, ws=None) -> dict:
    """单实体聚合：records / timeline / co_entities / canonical links / watched。

    未找到时抛 EntityError("ENTITY_NOT_FOUND")，message 含 difflib 相近建议。
    """
    from scripts import wiki_index
    from scripts.records import links as L

    hit = find_entity(db_path, name)
    if not hit:
        known = list(entity_index(db_path))
        near = difflib.get_close_matches((name or "").strip(), known, n=3, cutoff=0.4)
        msg = f"实体不存在: {name}" + (f"；相近实体: {', '.join(near)}" if near else "")
        raise EntityError("ENTITY_NOT_FOUND", msg)

    ename, etype, entry_ids = hit["name"], hit["type"], hit["entries"]
    entries = {e["id"]: e for e in wiki_index.list_entries(db_path)}
    records = []
    months = {}
    for eid in entry_ids:
        e = entries.get(eid) or {}
        date = e.get("date") or ""
        records.append({"id": eid, "date": date, "title": e.get("title") or eid,
                        "overview": e.get("overview") or "", "tags": e.get("tags") or ""})
        m = date[:7] or "unknown"
        months[m] = months.get(m, 0) + 1
    records.sort(key=lambda r: r["date"] or "", reverse=True)
    timeline = [{"month": m, "count": c} for m, c in sorted(months.items(), reverse=True)]

    all_ents = L.all_entry_entities(db_path)
    co = {}
    for eid in entry_ids:
        for b in ENTITY_BUCKETS:
            for other in (all_ents.get(eid) or {}).get(b) or []:
                if other != ename:
                    slot = co.setdefault(other, {"name": other, "type": b, "count": 0})
                    slot["count"] += 1
    co_entities = sorted(co.values(), key=lambda x: -x["count"])[:10]

    links_map = L.get_links_map(db_path)
    seen_urls, links = set(), []
    for eid in entry_ids:
        for lk in links_map.get(eid) or []:
            if lk.get("role") != "canonical":
                continue
            u = lk.get("url") or ""
            if u and u not in seen_urls:
                seen_urls.add(u)
                links.append({"url": u, "kind": lk.get("kind") or "other"})

    watched = any(w["name"] == ename for w in list_watched(db_path))
    return {"name": ename, "type": etype, "slug": slugify_name(ename), "watched": watched,
            "records": records, "timeline": timeline,
            "co_entities": co_entities, "links": links}


# ---------------------------------------------------------------------------
# LLM 摘要管线（可选，显式触发）
# ---------------------------------------------------------------------------
_RECORD_ID_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}_[\w-]+")


def build_summary_task(agg: dict, ws) -> str:
    """构造实体摘要写作 agent 的 task prompt。"""
    ws = Path(ws) if ws is not None else paths.get_workspace()
    slug = agg["slug"]
    out = str((paths.entity_dir(slug, ws) / "summary.new.md").resolve())
    rec_lines = [f"- [{r['id']}]（{r.get('date') or '?'}）{r.get('title') or r['id']}——{(r.get('overview') or '')[:120]}"
                 for r in agg.get("records") or []]
    rec_block = "\n".join(rec_lines) or "- （暂无关联记录）"
    old = ""
    sp = paths.entity_dir(slug, ws) / "summary.md"
    if sp.exists():
        old = sp.read_text(encoding="utf-8", errors="replace")[:1500]
    revision = 1
    meta_path = paths.entity_dir(slug, ws) / "meta.json"
    if meta_path.exists():
        try:
            revision = int((json.loads(meta_path.read_text(encoding="utf-8")) or {}).get("revision") or 0) + 1
        except Exception:
            revision = 1

    return f"""你是技术情报分析 agent。为实体 **{agg['name']}**（{agg.get('type') or 'unknown'}）撰写/更新 wiki 摘要页，写入唯一输出文件：
`{out}`

## 关联的 wiki 记录（系统确定性匹配，{len(agg.get('records') or [])} 条；可据 id 到 wiki/artifacts/{{id}}/ 读 record.json 与 raw/ 深入）

{rec_block}

## 旧版摘要（如存在，在其基础上更新，保留仍准确的内容）

{old or "（无，本次为首版）"}

## 输出契约（summary.new.md）

```markdown
# {agg['name']}

> {agg.get('type') or 'unknown'} · 关联 {len(agg.get('records') or [])} 条记录 · 第 {revision} 版

## 概述
这个实体是谁/是什么、当前重心（基于关联记录证据，3-6 句）。

## 动态时间线
按日期倒序列出关联记录：- [id]（date）标题——一句话
```

## 硬性规则

1. 内容可溯源到关联记录；**禁止编造 URL 与事实**。
2. 至少引用一个关联记录 id（形如 2026-08-01_xxxx）。
3. 客观陈述，不评价。
4. **禁止执行任何 git 操作**；不要写 summary.new.md 以外的文件；不要运行 publish / cli 命令。
5. 完成后只返回一句话摘要。
"""


def validate_summary_md(text: str, agg: dict) -> tuple:
    """校验摘要：H1 含实体名 + ≥1 个关联记录 id + 大小上限。"""
    errors = []
    if not text or not text.strip():
        return False, ["摘要为空"]
    if not re.search(r"^#\s+\S", text, re.M):
        errors.append("missing H1 title（# ...）")
    if agg.get("name") and agg["name"] not in text:
        errors.append(f"未含实体名: {agg['name']}")
    ids = [r["id"] for r in agg.get("records") or []]
    if ids and not any(re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text) for i in ids):
        errors.append("未引用任何关联记录 id")
    if len(text) > 20000:
        errors.append("超长（>20000 字符）")
    return (len(errors) == 0), errors


def auto_write_summary(name, ws=None, db_path=None, runner=None, timeout: int = 900) -> dict:
    """headless 写 summary.new.md → 校验 → PublishLock 内落盘 summary.md + meta.json（revision+1）→ 重建站点。"""
    from scripts.lib import headless_write_runner
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)
    agg = aggregate_entity(db_path, name, ws)
    slug = agg["slug"]
    edir = paths.entity_dir(slug, ws)
    edir.mkdir(parents=True, exist_ok=True)

    task_text = build_summary_task(agg, ws)
    new_path = edir / "summary.new.md"
    new_path.unlink(missing_ok=True)
    runner = runner or headless_write_runner
    result = runner(task_text, ws, timeout=timeout)
    if not new_path.exists():
        err = ((result or {}).get("stderr") or (result or {}).get("stdout") or "")[-300:]
        raise EntityError("WRITE_FAILED", f"摘要写作未产出文件: {err or 'no output'}")
    text = new_path.read_text(encoding="utf-8", errors="replace")
    ok, errors = validate_summary_md(text, agg)

    meta_path = edir / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) or {}
        except Exception:
            meta = {}

    if not ok:
        new_path.unlink(missing_ok=True)
        meta.update({"status": "failed", "name": agg["name"], "slug": slug,
                     "error": "; ".join(errors), "updated_at": _now_iso()})
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        raise EntityError("SUMMARY_VERIFY_FAILED", f"摘要校验失败: {'; '.join(errors)}")

    with PublishLock(timeout=30):
        (edir / "summary.md").write_text(text, encoding="utf-8")
        new_path.unlink(missing_ok=True)
        meta.update({"status": "done", "name": agg["name"], "slug": slug,
                     "revision": int(meta.get("revision") or 0) + 1, "updated_at": _now_iso()})
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        build_site(db_path, ws)
    return {"ok": True, "slug": slug, "revision": meta["revision"],
            "summary": f"entities/{slug}/summary.md"}
