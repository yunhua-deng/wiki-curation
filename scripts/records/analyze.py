#!/usr/bin/env python3
"""
scripts/records/analyze.py — 分析层：主题聚簇 + 去重候选。

确定性部分（本模块）：FTS 种子 + relations 扩展 → ranked cluster + evidence。
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from scripts.records import links as L
from scripts.records.recall import build_variant_map, _default_variant_map
from scripts.records.schema import normalize_url
from scripts.wiki_index import store as wiki_store


def _fts_seed(db_path, query: str, limit: int = 20) -> list[str]:
    """FTS 种子条目（OR 语义）。"""
    from scripts.records.recall import _fts_matches
    return _fts_matches(db_path, query, limit=limit)


def _entity_seed(db_path, entity: str, limit: int = 30) -> list[str]:
    """按实体名（含别名）找条目。"""
    wanted = entity.lower()
    out = []
    for eid, buckets in L.all_entry_entities(db_path).items():
        flat = {str(v).lower() for vals in buckets.values() for v in vals}
        if wanted in flat:
            out.append(eid)
    return out[:limit]


def cluster(db_path, topic: str, limit: int = 30, variant_map: dict = None) -> dict:
    """主题聚簇：FTS 种子 + relations 边扩展，按分数排序。

    返回 {topic, cluster_size, entries, hints}。entries 每项含命中原因。
    """
    db_path = Path(db_path)
    if variant_map is None:
        variant_map = _default_variant_map()

    entries = {e["id"]: e for e in wiki_store.list_entries(db_path)}
    links_map = L.get_links_map(db_path)
    relations = L.get_all_relations(db_path)

    # 1) 种子：FTS + 实体匹配（topic 本身可能就是实体名）
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    def _hit(eid, score, reason):
        if eid not in entries:
            return
        scores[eid] = scores.get(eid, 0) + score
        reasons.setdefault(eid, []).append(reason)

    for i, eid in enumerate(_fts_seed(db_path, topic)):
        _hit(eid, max(1, 10 - i // 2), "fts")

    t_lower = topic.lower()
    for eid, buckets in L.all_entry_entities(db_path).items():
        flat = {str(v).lower() for vals in buckets.values() for v in vals}
        if any(t_lower in v or v in t_lower for v in flat if len(v) >= 3):
            _hit(eid, 15, "entity")

    # tag 直接命中
    for eid, e in entries.items():
        tags = [t.lower() for t in (e.get("tags") or [])] if not isinstance(e.get("tags"), str) else [t.strip().lower() for t in e["tags"].split(",")]
        if any(t_lower in t or t in t_lower for t in tags if t):
            _hit(eid, 8, "tag")

    # 2) 扩展：relations 边（种子命中越多权重越高）
    seed_set = set(scores)
    for r in relations:
        a, b = r["entry_a"], r["entry_b"]
        if a in seed_set or b in seed_set:
            other = b if a in seed_set else a
            weight = min(20, (r.get("score") or 0) / 4)
            _hit(other, weight, f"relation:{r['kind']}")

    # 3) 组装
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    out_entries = []
    for eid, score in ranked:
        e = entries[eid]
        canonical = [l for l in links_map.get(eid, []) if l.get("role") == "canonical"]
        out_entries.append({
            "id": eid,
            "title": e.get("title") or eid,
            "date": e.get("date") or "",
            "topic_type": e.get("topic_type") or "",
            "tldr": (e.get("overview") or "")[:200],
            "tags": e.get("tags") if isinstance(e.get("tags"), list) else (e.get("tags") or "").split(","),
            "canonical_links": [l["url"] for l in canonical[:4]],
            "score": round(score, 1),
            "reasons": reasons.get(eid, []),
        })

    # hints：聚簇内的主导 tag / 实体 / 域名
    from collections import Counter
    tag_c, ent_c, dom_c = Counter(), Counter(), Counter()
    ents_map = L.all_entry_entities(db_path)
    for eid, _ in ranked:
        for t in out_entries[-1]["tags"] if out_entries else []:
            pass
        for t in (entries[eid].get("tags") or []):
            tag_c[t] += 1
        for vals in ents_map.get(eid, {}).values():
            for v in vals:
                ent_c[v] += 1
        for l in links_map.get(eid, []):
            try:
                dom_c[urlsplit(l["url"]).netloc.lower()] += 1
            except Exception:
                pass

    return {
        "topic": topic,
        "cluster_size": len(out_entries),
        "entries": out_entries,
        "hints": {
            "top_tags": [t for t, _ in tag_c.most_common(8)],
            "top_entities": [t for t, _ in ent_c.most_common(8)],
            "top_domains": [t for t, _ in dom_c.most_common(8)],
        },
    }


def discover_topics(db_path, recent_days: int = 14, min_recent: int = 5,
                    top_n: int = 8) -> list[dict]:
    """自动发现近期热点主题：近 N 天高频 tag/实体 vs 全库基线。

    打分：recent_count * (1 + log(1 + recency_ratio))，过滤掉全库都热的低判别力项。
    """
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    import math

    db_path = Path(db_path)
    entries = wiki_store.list_entries(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).strftime("%Y-%m-%d")

    def _recent(e):
        return (e.get("completed_at") or e.get("queued_at") or e.get("date") or "") >= cutoff

    recent_tag, base_tag = Counter(), Counter()
    recent_ent, base_ent = Counter(), Counter()
    ents_map = L.all_entry_entities(db_path)

    for e in entries:
        tags = e.get("tags") if isinstance(e.get("tags"), list) else (e.get("tags") or "").split(",")
        tags = [t.strip() for t in tags if t and t.strip()]
        ents = {v for vals in ents_map.get(e["id"], {}).values() for v in vals}
        for t in tags:
            base_tag[t] += 1
            if _recent(e):
                recent_tag[t] += 1
        for v in ents:
            base_ent[v] += 1
            if _recent(e):
                recent_ent[v] += 1

    candidates = []
    for kind, recent, base in (("tag", recent_tag, base_tag), ("entity", recent_ent, base_ent)):
        for name, cnt in recent.items():
            if cnt < min_recent:
                continue
            ratio = cnt / base.get(name, 1)
            score = round(cnt * (1 + math.log1p(ratio)), 1)
            candidates.append({
                "kind": kind, "name": name,
                "recent_count": cnt, "base_count": base.get(name, 0),
                "recency_ratio": round(ratio, 2), "score": score,
            })

    candidates.sort(key=lambda c: (-c["score"], c["name"]))
    return candidates[:top_n]


def dedup_candidates(db_path, min_score: float = 40, limit: int = 50) -> list[dict]:
    """去重候选：same_url（同来源重复收录）或强 shared_link 边。"""
    db_path = Path(db_path)
    entries = {e["id"]: e for e in wiki_store.list_entries(db_path)}
    seen = set()
    out = []
    for r in L.get_all_relations(db_path):
        if r["kind"] not in ("same_url", "shared_link"):
            continue
        if (r.get("score") or 0) < min_score:
            continue
        a, b = r["entry_a"], r["entry_b"]
        key = tuple(sorted([a, b]))
        if key in seen or a not in entries or b not in entries:
            continue
        seen.add(key)
        out.append({
            "a": a, "b": b,
            "kind": r["kind"], "score": r["score"],
            "evidence": r.get("evidence") or {},
            "a_title": (entries[a].get("title") or "")[:60],
            "b_title": (entries[b].get("title") or "")[:60],
        })
        if len(out) >= limit:
            break
    return out

