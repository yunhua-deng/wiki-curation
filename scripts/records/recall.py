#!/usr/bin/env python3
"""
scripts/records/recall.py — 确定性四层召回引擎。

评分（可复现，无模型调用）：
  1. url_exact   输入 URL == entry.source_input（归一化）        +100
  2. shared_link 输入 URL ∈ entry.links.url                      +40 / 条
  3. entity      输入文本命中的实体 ∩ entries.entities           +20 / 个
  4. fts         FTS5 全文命中（OR 语义）                         +max(1, 10-2i)

每个命中都带 reason，输出"为什么相似"而不只是分数。
"""
import re
from pathlib import Path

from scripts.records import links as L
from scripts.records.schema import normalize_url
from scripts.wiki_index import store as wiki_store

SCORE_URL_EXACT = 100
SCORE_SHARED_LINK = 40
SCORE_ENTITY = 20
MIN_ALIAS_LEN = 3

_URL_RE = re.compile(r"https?://[^\s，。；）)\"'<>]+", re.I)


def extract_urls(text: str) -> list[str]:
    """从任意文本提取 URL 并规范化去重。"""
    seen = []
    for m in _URL_RE.finditer(text or ""):
        url = normalize_url(m.group(0).rstrip(".,;:!?)）]】"))
        if url and url not in seen:
            seen.append(url)
    return seen


def build_variant_map(aliases: dict) -> dict:
    """{variant_lower: canonical}，涵盖 terms 与 entities 各类目；过滤过短别名。"""
    variant_map = {}
    for canonical, variants in (aliases.get("terms") or {}).items():
        for v in [canonical] + (variants or []):
            v = str(v).strip().lower()
            if len(v) >= MIN_ALIAS_LEN:
                variant_map[v] = canonical
    for _etype, entities in (aliases.get("entities") or {}).items():
        for canonical, variants in (entities or {}).items():
            for v in [canonical] + (variants or []):
                v = str(v).strip().lower()
                if len(v) >= MIN_ALIAS_LEN:
                    variant_map[v] = canonical
    return variant_map


def extract_entities_from_text(text: str, variant_map: dict) -> list[str]:
    """输入文本 → 归一化实体列表（子串匹配，canonical 去重）。"""
    lowered = (text or "").lower()
    found = []
    for variant, canonical in variant_map.items():
        if variant in lowered and canonical not in found:
            found.append(canonical)
    return found


def _default_variant_map() -> dict:
    """生产路径：从 references/entity_aliases.yaml 构建 variant map。"""
    try:
        from scripts.site.entities import load_aliases
        return build_variant_map(load_aliases())
    except Exception:
        return {}


def _fts_query_or(text: str) -> str:
    """把任意输入转成 FTS5 OR 查询。

    先按非词字符切分（URL 也会拆成 host/路径关键词），每个 token 独立引号包裹，
    OR 语义保证部分命中也能召回。
    """
    tokens = re.findall(r"[\w一-鿿]+", (text or ""))
    # 过滤无判别力的协议/通用 token 与单字符
    stop = {"http", "https", "www", "com", "org", "net", "html", "htm", "s", "abs"}
    tokens = [t for t in tokens if len(t) >= 2 and t.lower() not in stop]
    parts = []
    for t in tokens[:12]:
        escaped = t.replace('"', '""')
        parts.append(f'"{escaped}"')
    return " OR ".join(parts)


def _fts_matches(db_path, input_text: str, limit: int = 10) -> list[str]:
    """FTS5 OR 查询，按 rank 返回 entry id 列表；任何异常都返回空。"""
    query = _fts_query_or(input_text)
    if not query:
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT e.id FROM entries e JOIN entries_fts fts ON e.rowid = fts.rowid"
            " WHERE entries_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def recall(db_path, input_text: str, limit: int = 5, exclude_id: str = "",
           variant_map: dict = None) -> dict:
    """对 input_text 做四层相似度召回，返回 ranked matches + 命中原因。"""
    db_path = Path(db_path)
    if variant_map is None:
        variant_map = _default_variant_map()

    input_urls = extract_urls(input_text)
    input_entities = extract_entities_from_text(input_text, variant_map)

    # 收集候选：entry_id -> {score, reasons}
    candidates: dict[str, dict] = {}

    def _hit(entry_id: str, kind: str, score: float, detail: str):
        c = candidates.setdefault(entry_id, {"score": 0.0, "reasons": []})
        c["score"] += score
        c["reasons"].append({"kind": kind, "detail": detail})

    entries = {e["id"]: e for e in wiki_store.list_entries(db_path)}

    # Layer 1: url_exact（source_input 任一行归一化后相等）
    if input_urls:
        input_url_set = set(input_urls)
        for eid, entry in entries.items():
            src_lines = {normalize_url(s) for s in (entry.get("source_input") or "").splitlines()}
            for url in input_url_set & src_lines:
                _hit(eid, "url_exact", SCORE_URL_EXACT, f"source_input == {url}")

    # Layer 2: shared_link（输入 URL 出现在 entry 的 links 表）
    for url in input_urls:
        for eid in L.find_entries_by_url(db_path, url):
            _hit(eid, "shared_link", SCORE_SHARED_LINK, f"links contains {url}")

    # Layer 3: entity（归一化实体 ∩ entries.entities 各桶）
    if input_entities:
        wanted = {e.lower(): e for e in input_entities}
        for eid, buckets in L.all_entry_entities(db_path).items():
            flat = {str(v).lower() for vals in buckets.values() for v in vals}
            for ent_lower in sorted(set(wanted) & flat):
                _hit(eid, "entity", SCORE_ENTITY, f"entity {wanted[ent_lower]}")

    # Layer 4: fts（OR 语义全文检索，按命中序给分）
    for i, eid in enumerate(_fts_matches(db_path, input_text)):
        _hit(eid, "fts", max(1, 10 - 2 * i), "full-text match")

    # 组装输出：排除自身 / failed，按分数降序（同分按 id 保证确定性）
    matches = []
    for eid, c in candidates.items():
        if eid == exclude_id:
            continue
        entry = entries.get(eid)
        if not entry or entry.get("status") == "failed":
            continue
        matches.append({
            "id": eid,
            "title": entry.get("title") or eid,
            "tldr": (entry.get("overview") or "")[:200],
            "status": entry.get("status") or "",
            "score": round(c["score"], 2),
            "reasons": c["reasons"],
        })
    matches.sort(key=lambda m: (-m["score"], m["id"]))
    return {"input": input_text, "matches": matches[:limit]}
