#!/usr/bin/env python3
"""
scripts/records/relations.py — 条目间关联边的确定性计算与织入。

边类型与权重：
  same_url      source_input 规范化相等            100
  shared_link   links.url 相交                     40 / 条
  shared_entity entities 各桶相交                  20 / 个
  tag_overlap   tags Jaccard ≥ 0.34                10 * jaccard
"""
from scripts.records import links as L
from scripts.records.schema import normalize_url
from scripts.wiki_index.store import list_entries, _tags_to_list

SCORE_SAME_URL = 100
SCORE_SHARED_LINK = 40
SCORE_SHARED_ENTITY = 20
TAG_JACCARD_MIN = 0.34


def _norm_lines(text: str) -> set:
    return {normalize_url(s) for s in (text or "").splitlines() if s.strip()}


def _flat_entities(buckets: dict) -> set:
    return {str(v).lower() for vals in (buckets or {}).values() for v in vals}


def compute_relations(db_path, slug) -> list[dict]:
    """计算 slug 与全库条目的关联边。edge: {a, b, kind, score, evidence}。"""
    entries = {e["id"]: e for e in list_entries(db_path)}
    me = entries.get(slug)
    if not me:
        return []

    links_map = L.get_links_map(db_path)
    entities_map = L.all_entry_entities(db_path)

    my_src = _norm_lines(me.get("source_input") or "")
    my_links = {normalize_url(l["url"]) for l in links_map.get(slug, [])}
    my_ents = _flat_entities(entities_map.get(slug, {}))
    my_tags = {t.lower() for t in _tags_to_list(me.get("tags"))}

    edges = []
    for oid, other in entries.items():
        if oid == slug or other.get("status") == "failed":
            continue

        for url in my_src & _norm_lines(other.get("source_input") or ""):
            edges.append({"a": slug, "b": oid, "kind": "same_url",
                          "score": SCORE_SAME_URL, "evidence": {"url": url}})

        other_links = {normalize_url(l["url"]) for l in links_map.get(oid, [])}
        for url in my_links & other_links:
            edges.append({"a": slug, "b": oid, "kind": "shared_link",
                          "score": SCORE_SHARED_LINK, "evidence": {"url": url}})

        other_ents = _flat_entities(entities_map.get(oid, {}))
        for ent in sorted(my_ents & other_ents):
            edges.append({"a": slug, "b": oid, "kind": "shared_entity",
                          "score": SCORE_SHARED_ENTITY, "evidence": {"entity": ent}})

        other_tags = {t.lower() for t in _tags_to_list(other.get("tags"))}
        if my_tags and other_tags:
            inter = my_tags & other_tags
            union = my_tags | other_tags
            jaccard = len(inter) / len(union)
            if jaccard >= TAG_JACCARD_MIN:
                edges.append({"a": slug, "b": oid, "kind": "tag_overlap",
                              "score": round(10 * jaccard, 2),
                              "evidence": {"tags": sorted(inter)}})

    return edges


def rewire_relations(db_path, slug) -> int:
    """幂等重织 slug 的全部关联边（删旧织新）。返回边数。"""
    edges = compute_relations(db_path, slug)
    return L.replace_relations(db_path, slug, edges)
