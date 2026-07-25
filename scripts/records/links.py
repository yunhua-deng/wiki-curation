#!/usr/bin/env python3
"""
scripts/records/links.py — links / relations 表与 entries.entities 列的统一存取层。

写操作均为 replace 语义（删该条目旧行后批量插入），保证幂等；
relations 边无向存储：entry_a < entry_b（字典序规范化）。
"""
import json
import sqlite3
from pathlib import Path

from scripts.wiki_index.schema import ensure_schema
from scripts.wiki_index.store import _now_iso

LINK_DEFAULTS = {
    "kind": "other",
    "role": "related",
    "origin": "explicit",
    "fetched": 0,
    "verified": None,
}

EMPTY_ENTITIES = {"company": [], "author": [], "product": [], "series": []}


def _connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# links
# ============================================================

def _row_to_link(row) -> dict:
    return {
        "url": row["url"],
        "kind": row["kind"] or "other",
        "role": row["role"] or "related",
        "origin": row["origin"] or "explicit",
        "fetched": row["fetched"] or 0,
        "verified": row["verified"],
        "discovered_at": row["discovered_at"],
    }


def replace_links(db_path, entry_id, links: list[dict]) -> int:
    """用给定 links 全量替换该 entry 的旧链接。返回写入条数。"""
    conn = _connect(db_path)
    now = _now_iso()
    conn.execute("DELETE FROM links WHERE entry_id = ?", (entry_id,))
    seen = set()
    count = 0
    for link in links:
        url = (link.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        conn.execute(
            "INSERT INTO links (entry_id, url, kind, role, origin, fetched, verified, discovered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id, url,
                link.get("kind") or LINK_DEFAULTS["kind"],
                link.get("role") or LINK_DEFAULTS["role"],
                link.get("origin") or LINK_DEFAULTS["origin"],
                1 if link.get("fetched") else 0,
                link.get("verified"),
                now,
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_links(db_path, entry_id) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM links WHERE entry_id = ? ORDER BY id ASC", (entry_id,)
    ).fetchall()
    conn.close()
    return [_row_to_link(r) for r in rows]


def get_links_map(db_path) -> dict[str, list[dict]]:
    """全库 entry_id -> links 映射（一次查询，避免 N+1）。"""
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM links ORDER BY entry_id, id").fetchall()
    conn.close()
    result: dict[str, list[dict]] = {}
    for r in rows:
        result.setdefault(r["entry_id"], []).append(_row_to_link(r))
    return result


def get_links_count_map(db_path) -> dict[str, int]:
    """全库 entry_id -> links 数量（list/stats 展示用）。"""
    conn = _connect(db_path)
    rows = conn.execute("SELECT entry_id, COUNT(*) FROM links GROUP BY entry_id").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def find_entries_by_url(db_path, url: str) -> list[str]:
    """links.url 精确匹配 → entry_id 列表（字典序去重）。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT entry_id FROM links WHERE url = ? ORDER BY entry_id", (url,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def set_link_verified(db_path, entry_id, url: str, verified) -> None:
    """回填单条链接的可达性标志（1 可达 / 0 不可达 / None 未验证）。"""
    conn = _connect(db_path)
    conn.execute(
        "UPDATE links SET verified = ? WHERE entry_id = ? AND url = ?",
        (verified, entry_id, url),
    )
    conn.commit()
    conn.close()


# ============================================================
# relations（无向边：entry_a < entry_b）
# ============================================================

def _row_to_edge(row, perspective: str) -> dict:
    other = row["entry_b"] if row["entry_a"] == perspective else row["entry_a"]
    evidence = row["evidence"]
    try:
        evidence = json.loads(evidence) if evidence else {}
    except Exception:
        evidence = {"raw": evidence}
    return {
        "other": other,
        "kind": row["kind"],
        "score": row["score"] or 0,
        "evidence": evidence,
    }


def replace_relations(db_path, entry_id, edges: list[dict]) -> int:
    """删除该 entry 的全部旧边后织入新边。edge 键：a, b, kind, score, evidence。"""
    conn = _connect(db_path)
    now = _now_iso()
    conn.execute(
        "DELETE FROM relations WHERE entry_a = ? OR entry_b = ?", (entry_id, entry_id)
    )
    seen = set()
    count = 0
    for e in edges:
        a, b = e.get("a"), e.get("b")
        if not a or not b or a == b:
            continue
        a, b = sorted([a, b])
        kind = e.get("kind") or "shared_link"
        key = (a, b, kind)
        if key in seen:
            continue
        seen.add(key)
        evidence = e.get("evidence")
        conn.execute(
            "INSERT INTO relations (entry_a, entry_b, kind, score, evidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (a, b, kind, e.get("score") or 0,
             json.dumps(evidence, ensure_ascii=False) if evidence else None, now),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_related(db_path, entry_id) -> list[dict]:
    """该 entry 的全部关联边（双向展开），按 score 降序。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM relations WHERE entry_a = ? OR entry_b = ? ORDER BY score DESC",
        (entry_id, entry_id),
    ).fetchall()
    conn.close()
    return [_row_to_edge(r, entry_id) for r in rows]


def get_all_relations(db_path) -> list[dict]:
    """全库关联边（站点图谱用），按 score 降序。"""
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM relations ORDER BY score DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        evidence = r["evidence"]
        try:
            evidence = json.loads(evidence) if evidence else {}
        except Exception:
            evidence = {}
        result.append({
            "entry_a": r["entry_a"], "entry_b": r["entry_b"],
            "kind": r["kind"], "score": r["score"] or 0, "evidence": evidence,
        })
    return result


# ============================================================
# entries.entities 列
# ============================================================

def _parse_entities(raw) -> dict:
    if not raw:
        return dict(EMPTY_ENTITIES)
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except Exception:
            return dict(EMPTY_ENTITIES)
    if not isinstance(data, dict):
        return dict(EMPTY_ENTITIES)
    result = {}
    for k in EMPTY_ENTITIES:
        v = data.get(k)
        result[k] = [x for x in v if x] if isinstance(v, list) else []
    return result


def set_entry_entities(db_path, entry_id, entities: dict) -> None:
    """写 entries.entities 列（JSON 文本）。"""
    conn = _connect(db_path)
    payload = json.dumps(_parse_entities(entities), ensure_ascii=False)
    conn.execute("UPDATE entries SET entities = ? WHERE id = ?", (payload, entry_id))
    conn.commit()
    conn.close()


def get_entry_entities(db_path, entry_id) -> dict:
    conn = _connect(db_path)
    row = conn.execute("SELECT entities FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    if not row:
        return dict(EMPTY_ENTITIES)
    return _parse_entities(row[0])


def all_entry_entities(db_path) -> dict[str, dict]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT id, entities FROM entries").fetchall()
    conn.close()
    return {r[0]: _parse_entities(r[1]) for r in rows}
