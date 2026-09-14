#!/usr/bin/env python3
"""entities.py — 实体只读查询（实体数据仅服务召回）。

实体数据源 = entries.entities 列（publish 时 alias 归一后的 canonical 实体，record.json 四桶）。
只提供确定性聚合：全库实体索引（entity_index）、精确查找（find_entity）、单实体聚合
（aggregate_entity：records / timeline / co_entities / canonical links）。
"""
import difflib

from scripts.lib import slugify_name

ENTITY_BUCKETS = ("company", "author", "product", "series")


class EntityError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def flatten_entities(entities) -> list:
    out = []
    for b in ENTITY_BUCKETS:
        out.extend((entities or {}).get(b) or [])
    return out


def entity_index(db_path) -> dict:
    """全部 canonical 实体 → {"type": 桶, "entries": [slug...]}（跳过被抑制实体）。"""
    from scripts import entity_filter as EF
    from scripts.records.links import all_entry_entities
    suppression = EF.load_suppression()
    index = {}
    for slug, ents in all_entry_entities(db_path).items():
        for b in ENTITY_BUCKETS:
            for name in ents.get(b) or []:
                if EF.is_suppressed(name, suppression):
                    continue
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
    """单实体聚合：records / timeline / co_entities / canonical links。

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
    from scripts import entity_filter as EF
    suppression = EF.load_suppression()
    co = {}
    for eid in entry_ids:
        for b in ENTITY_BUCKETS:
            for other in (all_ents.get(eid) or {}).get(b) or []:
                if other != ename and not EF.is_suppressed(other, suppression):
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

    return {"name": ename, "type": etype, "slug": slugify_name(ename),
            "records": records, "timeline": timeline,
            "co_entities": co_entities, "links": links}
