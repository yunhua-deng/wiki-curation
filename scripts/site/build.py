#!/usr/bin/env python3
"""
scripts/site/build.py — 从 wiki.db 构建轻量静态 wiki 站点。

站点为单页结构：index.html 融合主题浏览与标签过滤；entry.html / raw.html 为条目子页面。

输出结构（默认 wiki/site/）：
  index.html
  entry.html
  raw.html
  assets/       <- 复制 assets/site/
  data/
    entries.json
    tags.json
    sources.json
    entities.json
    themes.json
    timeline.json
"""
import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path

import yaml

from scripts import paths
from scripts.records.schema import load_record
from scripts.wiki_index.store import list_entries
from scripts.site.templates import render_pages
from scripts.site.entities import build_entity_index
from scripts.site.entity_pages import build_entity_pages


REFERENCES_DIR = Path(__file__).resolve().parent.parent.parent / "references"
_ASSET_DIR = paths.assets_dir() / "site"

# v3.2: entity alias lookup table for search expansion
_ALIAS_MAP = None
def _get_alias_map():
    global _ALIAS_MAP
    if _ALIAS_MAP is not None: return _ALIAS_MAP
    path = REFERENCES_DIR / "entity_aliases.yaml"
    if not path.exists(): _ALIAS_MAP = {}; return _ALIAS_MAP
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    m = {}
    for canonical, variants in (data.get("terms") or {}).items():
        for v in [canonical] + (variants or []):
            m[str(v).strip().lower()] = canonical
    for _et, ent_map in (data.get("entities") or {}).items():
        for canonical, variants in (ent_map or {}).items():
            for v in [canonical] + (variants or []):
                m[str(v).strip().lower()] = canonical
    _ALIAS_MAP = m
    return _ALIAS_MAP


def _to_list(value):
    """把 tags 字符串转成列表。"""
    if isinstance(value, list):
        return [v.strip() for v in value if v and v.strip()]
    if not value or value == "—":
        return []
    return [v.strip() for v in str(value).split(",") if v.strip() and v.strip() != "—"]


def _article_url(entry):
    """返回条目的文章相对链接。"""
    slug = entry.get("id") or ""
    depth = entry.get("depth") or ""
    file_field = entry.get("file") or ""
    if file_field:
        return f"artifacts/{slug}/{file_field}"
    if slug and depth in ("brief", "deep"):
        return f"artifacts/{slug}/{slug}_{depth}.md"
    return ""


def _raw_url(entry):
    """返回 raw 材料目录链接。"""
    slug = entry.get("id") or ""
    raw = entry.get("raw") or ""
    if raw:
        return raw
    if slug:
        return f"artifacts/{slug}/"
    return ""


VALID_DISPLAY_TYPES = {
    "paper", "project", "tool", "company", "institution",
    "researcher", "concept", "whitepaper", "best_practice",
    "comparison", "trend", "article", "observation", "product"
}


def _normalize_type(topic_type, source_type):
    """返回站点展示用的知识主题类型。"""
    if topic_type in VALID_DISPLAY_TYPES:
        return topic_type
    mapping = {
        "arxiv_paper": "paper",
        "arxiv": "paper",
        "github": "project",
        "project_name": "project",
        "project": "project",
        "weixin": "article",
        "zhihu": "article",
        "reddit": "article",
        "twitter_x": "article",
        "youtube": "article",
        "bilibili": "article",
        "podcast": "article",
        "tech_blog": "article",
        "blog": "article",
        "news_article": "article",
        "news": "article",
        "huggingface": "tool",
        "company_site": "company",
        "startup_name": "company",
        "company": "company",
        "product_name": "product",
        "product": "product",
        "institution": "institution",
        "researcher": "researcher",
        "concept_query": "concept",
        "concept": "concept",
        "whitepaper": "whitepaper",
        "best_practice": "best_practice",
        "comparison": "comparison",
        "trend_question": "trend",
        "trend": "trend",
        "local_file": "observation",
        "local": "observation",
        "multi_source": "observation",
        "generic_web": "observation",
        "webpage": "observation",
    }
    inferred = mapping.get(topic_type) or mapping.get(source_type)
    return inferred or "observation"


def _export_entries(db_path, wiki_dir):
    """读取 wiki.db，返回用于前端展示的 entries 列表。"""
    from scripts.records.links import get_links_map
    rows = list_entries(db_path, order_by="id DESC")
    wiki_dir = Path(wiki_dir)
    links_map = get_links_map(db_path)
    entries = []
    for row in rows:
        slug = row.get("id") or ""
        topic_type = _normalize_type(row.get("topic_type") or "unknown", row.get("source_type") or "unknown")
        entry = {
            "id": slug,
            "date": row.get("date") or "—",
            "title": row.get("title") or slug,
            "overview": row.get("overview") or "",
            "topic_type": topic_type,
            "status": row.get("status") or "",
            "tags": _to_list(row.get("tags")),
            "sources": row.get("sources") or "—",
            "input_type": row.get("input_type") or "unknown",
            "source_type": row.get("source_type") or "unknown",
            "source_input": row.get("source_input") or "",
            "file": row.get("file") or "",
            "raw": _raw_url(row),
            "article_url": _article_url(row),
            "spec_version": row.get("spec_version") or "1.0",
            "watched": 1 if row.get("watched") else 0,
            "watched_at": row.get("watched_at") or "",
            "verified_depths": row.get("verified_depths") or "",
            # v3.0 record-first：记录标识与链接图谱
            "has_record": paths.record_path(slug, wiki_dir).exists(),
            "links": links_map.get(slug, []),
        }
        # v3.2: record.json as single source of truth
        record = load_record(slug, wiki_dir)
        if record:
            entry["tags"] = record.get("tags") or entry["tags"]
            entry["topic_type"] = _normalize_type(record.get("topic_type") or entry["topic_type"], entry["source_type"])
            entry["title"] = record.get("title") or entry["title"]
            # 优先 record.date，忽略占位值，回退 wiki.db
            rd = record.get("date") or ""
            entry["date"] = rd if rd and rd not in ("—", "-") else (entry["date"] if entry["date"] not in ("—", "-") else row.get("date") or "—")
            entry["summary"] = {
                "tldr": record.get("tldr") or entry.get("overview", ""),
                "text": record.get("summary") or "",
            }
            entry["source"] = record.get("source") or {}
            entry["entities"] = record.get("entities") or {}
            # v3.7：发起时的召回预览
            if record.get("preview"):
                entry["preview"] = record["preview"]
            if not entry["links"] and record.get("links"):
                entry["links"] = record["links"]
        # v3.2: alias-expanded search text
        if entry.get("entities"):
            aliases = _get_alias_map()
            extra = set()
            for vals in entry["entities"].values():
                for v in (vals or []):
                    vlow = str(v).strip().lower()
                    extra.add(vlow)
                    # reverse lookup: canonical → all known aliases
                    for alias, canon in aliases.items():
                        if canon.lower() == vlow and alias not in extra:
                            extra.add(alias)
            entry["_search_aliases"] = list(extra)
        entries.append(entry)
    return entries


def _build_tags(entries):
    """tag -> entry ids。"""
    tags = defaultdict(list)
    for e in entries:
        for tag in e.get("tags") or []:
            tags[tag].append(e["id"])
    return dict(sorted(tags.items(), key=lambda x: (-len(x[1]), x[0])))


def _build_sources(entries):
    """input_type -> source_type -> entry ids。"""
    sources = defaultdict(lambda: defaultdict(list))
    for e in entries:
        sources[e.get("input_type") or "unknown"][e.get("source_type") or "unknown"].append(e["id"])
    # 转为普通 dict
    return {k: dict(v) for k, v in sorted(sources.items())}


def _build_related_map(db_path, entries=None) -> dict[str, list[dict]]:
    """v3.4：relations 表 → entry → top related；v3.7：附标题（列表展示）。"""
    from scripts.records.links import get_all_relations
    rels = get_all_relations(db_path)
    titles = {e.get("id"): (e.get("title") or "") for e in (entries or [])}
    m: dict[str, dict[str, float]] = {}
    for r in rels:
        for eid in (r["entry_a"], r["entry_b"]):
            other = r["entry_b"] if eid == r["entry_a"] else r["entry_a"]
            d = m.setdefault(eid, {})
            d[other] = d.get(other, 0) + (r.get("score") or 0)
    out = {}
    for eid, others in m.items():
        ranked = sorted(others.items(), key=lambda kv: -kv[1])[:6]
        out[eid] = [{"id": oid, "score": round(s, 1), "title": titles.get(oid) or ""}
                    for oid, s in ranked]
    return out


def _slim_entry(e: dict) -> dict:
    """v3.3：前端展示所需字段（去掉 raw_files/article_url/raw/sources 等大体量字段）。"""
    src = e.get("source") or {}

    def _url_str(v) -> str:
        """v3.3：source URL 字段容错——老数据可能是 {type,url,source_name} dict。"""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return str(v.get("url") or "")
        return ""

    return {
        "id": e.get("id"),
        "date": e.get("date"),
        "title": e.get("title"),
        "overview": e.get("overview") or "",
        "topic_type": e.get("topic_type"),
        "status": e.get("status") or "",
        "tags": e.get("tags") or [],
        "summary": e.get("summary") or {},
        "entities": e.get("entities") or {},
        "links": e.get("links") or [],
        "has_record": bool(e.get("has_record")),
        "preview": e.get("preview") or {},
        "watched": bool(e.get("watched")),
        "watched_at": e.get("watched_at") or "",
        "_search_aliases": e.get("_search_aliases") or [],
        "source": {
            "direct_source": _url_str(src.get("direct_source")),
            "original_source": _url_str(src.get("original_source")),
        },
    }


def _write_html_index(entries: list[dict], wiki_dir: Path) -> Path:
    """v3.3：重新生成 wiki/wiki.html 静态语义索引（id/date/type/title/tldr/tags）。"""
    rows = []
    for e in entries:
        tldr = (e.get("summary") or {}).get("tldr") or e.get("overview") or ""
        tags = ", ".join(e.get("tags") or [])
        rows.append(
            f'<tr><td class="id">{e.get("id")}</td><td>{e.get("date")}</td>'
            f'<td>{e.get("topic_type")}</td><td class="t">{e.get("title")}</td>'
            f'<td class="m">{tldr}</td><td class="m">{tags}</td></tr>'
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Wiki Index</title>
<style>
body{{font-family:-apple-system,sans-serif;font-size:13px;color:#1f2328;margin:2rem}}
table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #d0d7de;padding:.35rem .5rem;text-align:left;vertical-align:top}}
.id{{font-family:monospace;color:#656d76;white-space:nowrap}}
.t{{font-weight:600;max-width:360px}}
.m{{color:#656d76;max-width:420px}}
</style></head><body>
<h1>Wiki Index — {len(entries)} entries</h1>
<table><thead><tr><th>ID</th><th>Date</th><th>Type</th><th>Title</th><th>TL;DR</th><th>Tags</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
</body></html>"""
    out = Path(wiki_dir) / "wiki.html"
    out.write_text(html, encoding="utf-8")
    return out


def _copy_assets(out_dir):
    """复制 assets/site/ 到 out_dir/assets/。"""
    asset_src = _ASSET_DIR
    asset_dst = out_dir / "assets"
    if asset_dst.exists():
        shutil.rmtree(asset_dst)
    if asset_src.exists():
        shutil.copytree(asset_src, asset_dst)


def _inject_cli_cmd(out_dir):
    """site.js 中的 __WIKI_CLI_CMD__ 占位符 → 当前 skill 的 cli.py 调用命令（绝对路径，正斜杠）。"""
    site_js = Path(out_dir) / "assets" / "site.js"
    if not site_js.exists():
        return
    cli_py = (Path(__file__).resolve().parent.parent / "cli.py").as_posix()
    site_js.write_text(
        site_js.read_text(encoding="utf-8").replace("__WIKI_CLI_CMD__", f"python {cli_py}"),
        encoding="utf-8",
    )


def build_site(db_path, wiki_dir, out_dir=None, export=False):
    """构建 wiki 站点。

    Args:
        db_path: wiki.db 路径。
        wiki_dir: wiki 工作区根目录（包含 artifacts/）。
        out_dir: 输出目录，默认 wiki_dir / "site"。
        export: 若 True，生成自包含的 site_dist（Phase 3）。
    Returns:
        生成的输出目录 Path。
    """
    db_path = Path(db_path)
    wiki_dir = Path(wiki_dir)
    if out_dir is None:
        out_dir = wiki_dir / "site_dist" if export else wiki_dir / "site"
    out_dir = Path(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    entries = _export_entries(db_path, wiki_dir)
    tags = _build_tags(entries)
    sources = _build_sources(entries)
    entities = build_entity_index(entries, wiki_dir)

    # v3.1：图谱（含 relations 表织边）
    from scripts.records.links import get_all_relations
    from scripts.site.graph import build_graph
    graph = build_graph(entries, wiki_dir, relation_edges=get_all_relations(db_path))

    # v3.4：关联条目（relations 表 top N）注入 entries.json 供详情展示
    related_map = _build_related_map(db_path, entries)

    # v3.3：entries.json 瘦身——只写前端表格/详情消费字段
    display_entries = [_slim_entry(e) for e in entries]
    for de in display_entries:
        de["_related"] = related_map.get(de["id"], [])
    (data_dir / "entries.json").write_text(
        json.dumps(display_entries, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (data_dir / "tags.json").write_text(
        json.dumps(tags, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (data_dir / "sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (data_dir / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (data_dir / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    # v3.1 时间线：按月聚合
    from collections import defaultdict
    months = defaultdict(lambda: {"month": "", "entries": [], "count": 0, "types": defaultdict(int)})
    for e in entries:
        date = e.get("date") or ""
        month_key = date[:7] if len(date) >= 7 else "unknown"
        if month_key not in months:
            months[month_key] = {"month": month_key, "entries": [], "count": 0, "types": defaultdict(int), "record_count": 0}
        months[month_key]["entries"].append(e["id"])
        months[month_key]["count"] += 1
        months[month_key]["types"][e.get("topic_type", "?")] += 1
        if e.get("has_record"):
            months[month_key]["record_count"] += 1
    timeline = sorted([v for v in months.values()], key=lambda x: x["month"], reverse=True)
    for m in timeline:
        m["types"] = dict(m["types"])
    (data_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 渲染 HTML 页面
    render_pages(entries, tags, sources, out_dir)

    # v3.3：清理陈旧 data 产物（search_index/themes/trends 等已废弃文件）
    current_data = {"entries.json", "tags.json", "sources.json", "entities.json",
                    "graph.json", "timeline.json", "entity_pages.json"}
    for f in data_dir.glob("*.json"):
        if f.name not in current_data:
            f.unlink()

    # v3.8：实体聚合页（确定性，零 LLM；摘要嵌读 wiki/entities/）
    entity_pages = build_entity_pages(db_path, wiki_dir)
    (data_dir / "entity_pages.json").write_text(
        json.dumps(entity_pages, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    # v3.3：重新生成 wiki/wiki.html 静态语义索引
    _write_html_index(display_entries, wiki_dir)

    # 站点已简化为单页结构，清理旧版多页站点的遗留页面（survey.html 已随冻结管线移除）
    for legacy_page in ("browse.html", "graph.html", "clusters.html", "timeline.html", "dive.html", "survey.html"):
        legacy_path = out_dir / legacy_page
        if legacy_path.exists():
            legacy_path.unlink()

    # 复制静态资源
    _copy_assets(out_dir)
    _inject_cli_cmd(out_dir)

    return out_dir


def main():
    parser = argparse.ArgumentParser(description="构建 wiki 静态站点")
    parser.add_argument("--workspace", help="wiki 工作区路径")
    parser.add_argument("--out", help="输出目录")
    parser.add_argument("--export", action="store_true", help="生成自包含导出")
    args = parser.parse_args()

    ws = Path(args.workspace) if args.workspace else paths.get_workspace()
    db = paths.db_path(ws)
    out = Path(args.out) if args.out else None
    build_site(db, ws, out_dir=out, export=args.export)
    print(f"wiki site built: {out or (ws / 'site')}")


if __name__ == "__main__":
    main()
