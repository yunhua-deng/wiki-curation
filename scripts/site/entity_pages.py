#!/usr/bin/env python3
"""site/entity_pages.py — 构建实体聚合页数据（确定性，零 LLM）。

数据源：entries.entities 列（publish canonical）+ links 表 + wiki/entities/ 摘要。
输出由 build_site 写入 site/data/entity_pages.json（纯构建产物，可随时重建）。
"""
import json
from pathlib import Path

from scripts import entity_filter as EF
from scripts import wiki_index
from scripts.entity_summary import ENTITY_BUCKETS, flatten_entities, list_watched
from scripts.lib import slugify_name
from scripts.records import links as L

SUMMARY_CAP = 8000  # 嵌入 entity_pages.json 的摘要字符上限


def _load_summary(wiki_dir: Path, slug: str) -> str:
    """读取 wiki/entities/{slug}/summary.md（仅 meta.status==done 时），截断到 SUMMARY_CAP。"""
    edir = Path(wiki_dir) / "entities" / slug
    meta_path = edir / "meta.json"
    summary_path = edir / "summary.md"
    if not meta_path.is_file() or not summary_path.is_file():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception:
        return ""
    if meta.get("status") != "done":
        return ""
    return summary_path.read_text(encoding="utf-8", errors="replace")[:SUMMARY_CAP]


def build_entity_pages(db_path, wiki_dir) -> dict:
    """全部实体聚合页数据：{slug: {...}}。无记录实体不生页。"""
    wiki_dir = Path(wiki_dir)
    entries = {e["id"]: e for e in wiki_index.list_entries(db_path)}
    all_ents = L.all_entry_entities(db_path)
    links_map = L.get_links_map(db_path)
    watched = {w["name"] for w in list_watched(db_path)}

    index = {}
    suppression = EF.load_suppression()
    group_cfg = EF.load_entity_groups()
    for eid, ents in all_ents.items():
        for b in ENTITY_BUCKETS:
            for name in ents.get(b) or []:
                if EF.is_suppressed(name, suppression):
                    continue  # 抑制实体不生页（兜底老数据；新数据 publish 时已过滤）
                slot = index.setdefault(name, {"type": b, "entries": []})
                if eid not in slot["entries"]:
                    slot["entries"].append(eid)

    pages = {}
    for name, slot in sorted(index.items()):
        slug = slugify_name(name)
        records, months, co = [], {}, {}
        seen_urls, links = set(), []
        for eid in slot["entries"]:
            e = entries.get(eid) or {}
            date = e.get("date") or ""
            records.append({"id": eid, "date": date, "title": e.get("title") or eid})
            m = date[:7] or "unknown"
            months[m] = months.get(m, 0) + 1
            for other in flatten_entities(all_ents.get(eid)):
                if other != name and not EF.is_suppressed(other, suppression):
                    co[other] = co.get(other, 0) + 1
            for lk in links_map.get(eid) or []:
                if lk.get("role") != "canonical":
                    continue
                u = lk.get("url") or ""
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    links.append({"url": u, "kind": lk.get("kind") or "other"})
        records.sort(key=lambda r: r["date"] or "", reverse=True)
        pages[slug] = {
            "name": name,
            "type": slot["type"],
            "group": EF.entity_group(name, slot["type"], group_cfg),
            "slug": slug,
            "watched": name in watched,
            "record_count": len(slot["entries"]),
            "records": records,
            "timeline": [{"month": m, "count": c} for m, c in sorted(months.items(), reverse=True)],
            "co_entities": [{"name": n, "count": c}
                            for n, c in sorted(co.items(), key=lambda kv: -kv[1])[:10]],
            "links": links[:20],
            "summary": _load_summary(wiki_dir, slug),
        }
    return pages
