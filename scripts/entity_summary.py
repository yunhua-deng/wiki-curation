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
from scripts.tracking import slugify_name

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
