#!/usr/bin/env python3
"""
scripts/site/graph.py — 构建 wiki 条目关联图谱。

输出 graph.json：
{
  "nodes": [{"id": "...", "title": "...", "type": "...", "depth": "...", "status": "..."}],
  "edges": [{"source": "...", "target": "...", "type": "shared_tag", "weight": 2}]
}
"""
import re
from collections import defaultdict
from pathlib import Path


def _tags_set(entry):
    tags = entry.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return set(tags)


def _month(date):
    if date and len(str(date)) >= 7:
        return str(date)[:7]
    return None


def _scan_crossrefs(entries, wiki_dir):
    """扫描所有 markdown 中的交叉引用链接，返回 (source_id, target_id, depth) 列表。"""
    wiki_dir = Path(wiki_dir)
    valid_ids = {e["id"] for e in entries}
    refs = []
    link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    # 匹配 artifacts/{id}/{id}_{depth}.md
    artifact_re = re.compile(r"artifacts/([^/]+)/\1_(brief|deep)\.md")
    # 匹配 ../2026-..._topic.md 或 ./2026-..._topic.md 等相对路径中的 slug
    rel_re = re.compile(r"(?:\.{1,2}/|^)(\d{4}-\d{2}-\d{2}[^/\s]*?)(?:_(brief|deep))?\.md")

    for e in entries:
        slug = e["id"]
        depth = e.get("depth") or "brief"
        for d in ("brief", "deep"):
            md_path = wiki_dir / "artifacts" / slug / f"{slug}_{d}.md"
            if not md_path.exists():
                continue
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for _, href in link_re.findall(text):
                m = artifact_re.search(href)
                if m:
                    target, target_depth = m.group(1), m.group(2)
                    if target in valid_ids and target != slug:
                        refs.append((slug, target, target_depth))
                    continue
                m = rel_re.search(href)
                if m:
                    target = m.group(1)
                    target_depth = m.group(2) or d
                    if target in valid_ids and target != slug:
                        refs.append((slug, target, target_depth))
    return refs


def build_graph(entries, wiki_dir, relation_edges=None):
    """从 entries 构建关联图谱。

    relation_edges：可选的 relations 表边列表
    （[{entry_a, entry_b, kind, score, evidence}]，见 records.links.get_all_relations），
    以 rel_<kind> 类型并入图谱。
    """
    nodes = []
    for e in entries:
        nodes.append({
            "id": e["id"],
            "title": e.get("title") or e["id"],
            "topic_type": e.get("topic_type") or e.get("type") or "unknown",
            "type": e.get("topic_type") or e.get("type") or "unknown",
            "depth": e.get("depth") or "—",
            "status": e.get("status") or "",
            "tags": e.get("tags") or [],
            "input_type": e.get("input_type") or "unknown",
            "source_type": e.get("source_type") or "unknown",
            "date": e.get("date") or "—",
        })
    nodes.sort(key=lambda n: n["id"])

    edges = []
    edge_keys = set()

    def add_edge(source, target, etype, weight=1, directed=False):
        if source == target:
            return
        if not directed:
            key = tuple(sorted([source, target]) + [etype])
        else:
            key = (source, target, etype)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append({
            "source": source,
            "target": target,
            "type": etype,
            "weight": weight,
            "directed": directed,
        })

    # shared_tag
    tag_to_ids = defaultdict(list)
    for e in entries:
        for tag in _tags_set(e):
            tag_to_ids[tag].append(e["id"])
    for tag in sorted(tag_to_ids.keys()):
        ids = sorted(tag_to_ids[tag])
        if len(ids) < 2:
            continue
        # 只生成共享 tag 数 >= 2 的边
        local = defaultdict(int)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                local[tuple(sorted((a, b)))] += 1
        for (a, b), count in sorted(local.items()):
            if count >= 1:
                add_edge(a, b, "shared_tag", weight=count)

    # crossref
    for source, target, _depth in sorted(_scan_crossrefs(entries, wiki_dir)):
        add_edge(source, target, "crossref", weight=1, directed=True)

    # relations 表（record-first 织边：same_url/shared_link/shared_entity/tag_overlap）
    valid_ids = {e["id"] for e in entries}
    for r in (relation_edges or []):
        a, b = r.get("entry_a"), r.get("entry_b")
        if a in valid_ids and b in valid_ids:
            add_edge(a, b, f"rel_{r.get('kind') or 'relation'}",
                     weight=max(1, int(r.get("score") or 1)))

    # same input type / source type
    input_type_groups = defaultdict(list)
    source_type_groups = defaultdict(list)
    for e in entries:
        input_type_groups[e.get("input_type") or "unknown"].append(e["id"])
        source_type_groups[e.get("source_type") or "unknown"].append(e["id"])

    group_edge_limit = 5
    for key in sorted(input_type_groups.keys()):
        group = input_type_groups[key]
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:i + 1 + group_edge_limit]:
                add_edge(a, b, "same_input_type", weight=1)

    for key in sorted(source_type_groups.keys()):
        group = source_type_groups[key]
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:i + 1 + group_edge_limit]:
                add_edge(a, b, "same_source_type", weight=1)

    # same_month
    month_groups = defaultdict(list)
    for e in entries:
        m = _month(e.get("date"))
        if m:
            month_groups[m].append(e["id"])
    for key in sorted(month_groups.keys()):
        group = month_groups[key]
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:i + 1 + group_edge_limit]:
                add_edge(a, b, "same_month", weight=1)

    edges.sort(key=lambda e: (e["source"], e["target"], e["type"]))

    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    import json

    from scripts import paths
    from scripts.wiki_index.store import list_entries

    ws = paths.get_workspace()
    entries = list_entries(paths.db_path(ws), order_by="date DESC, id ASC")
    graph = build_graph(entries, ws)
    print(json.dumps({"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
                     ensure_ascii=False))
