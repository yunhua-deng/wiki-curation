#!/usr/bin/env python3
"""scripts/records/clean_entities.py — 批量清洗存量 record.json 实体。

逻辑：遍历 done entries（或指定 id）→ 读 wiki/artifacts/{id}/record.json →
对 entities 四桶应用「alias 归一 + suppress」（scripts.entity_filter.canonicalize_entities，
与 publish 同一套函数）→ 有变化时（--apply）写回 record.json、更新 db、
对该 entry 重织 relations → 最后统一重建站点。

默认 dry-run，不落任何变更。
"""
import json
from collections import Counter
from pathlib import Path

from scripts import paths
from scripts.entity_filter import canonicalize_entities, ENTITY_BUCKETS
from scripts.records import links as L
from scripts.records import relations as REL
from scripts.records import schema as RS
from scripts.wiki_index.store import list_entries


def _normalize_raw_entities(entities) -> dict | None:
    """record.json entities 容错规整为四桶 list；非法结构返回 None（跳过并计数）。"""
    if not isinstance(entities, dict):
        return None
    out = {}
    for b in ENTITY_BUCKETS:
        v = entities.get(b)
        out[b] = [str(x).strip() for x in v if str(x or "").strip()] if isinstance(v, list) else []
    return out


def clean_entities(db_path, ws, entry_id: str = None, apply: bool = False) -> dict:
    """清洗存量实体。apply=False 时只扫描统计（dry-run）。"""
    db_path = Path(db_path)
    ws = Path(ws)
    if entry_id:
        entries = [e for e in list_entries(db_path) if e["id"] == entry_id]
    else:
        entries = list_entries(db_path, status="done")

    scanned = changed = skipped = 0
    removed: Counter = Counter()
    changed_ids = []

    for e in entries:
        slug = e["id"]
        scanned += 1
        record = RS.load_record(slug, ws)
        if record is None:
            skipped += 1  # record.json 缺失/损坏
            continue
        raw = _normalize_raw_entities(record.get("entities"))
        if raw is None:
            skipped += 1  # entities 不是合法 dict（老数据），容错跳过
            continue
        cleaned = canonicalize_entities(raw)
        if cleaned == raw:
            continue
        changed += 1
        changed_ids.append(slug)
        for b in ENTITY_BUCKETS:
            for name, n in (Counter(raw[b]) - Counter(cleaned[b])).items():
                removed[name] += n
        if apply:
            record["entities"] = cleaned
            paths.record_path(slug, ws).write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            L.set_entry_entities(db_path, slug, cleaned)
            REL.rewire_relations(db_path, slug)

    site_rebuilt = False
    if apply and changed:
        from scripts.site.build import build_site
        build_site(db_path, ws)
        site_rebuilt = True

    return {
        "scanned": scanned,
        "changed": changed,
        "skipped": skipped,
        "removed_entities": dict(removed.most_common()),
        "changed_ids": changed_ids,
        "dry_run": not apply,
        "site_rebuilt": site_rebuilt,
    }
