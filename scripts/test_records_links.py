#!/usr/bin/env python3
"""test_records_links.py — v5 迁移 + records links/relations/entities 存取层契约测试。"""
import sqlite3

from scripts.wiki_index.schema import ensure_schema


def test_v5_migration_creates_tables(tmp_path):
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "links" in tables and "relations" in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    assert "entities" in cols
    conn.close()
    # 幂等：重复执行不报错
    ensure_schema(db)


def test_links_roundtrip_and_idempotent(tmp_path):
    from scripts.records import links as L
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    L.replace_links(db, "e1", [
        {"url": "https://github.com/a/b", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": 1, "verified": None},
    ])
    L.replace_links(db, "e1", [
        {"url": "https://github.com/a/b", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": 1, "verified": None},
        {"url": "https://arxiv.org/abs/1234.5678", "kind": "arxiv", "role": "related",
         "origin": "inferred", "fetched": 0, "verified": None},
    ])
    got = L.get_links(db, "e1")
    assert len(got) == 2
    urls = {l["url"] for l in got}
    assert "https://github.com/a/b" in urls
    assert L.find_entries_by_url(db, "https://github.com/a/b") == ["e1"]
    # verified 为 None 时保持 None（未验证）
    gh = [l for l in got if l["kind"] == "github"][0]
    assert gh["verified"] is None and gh["role"] == "canonical" and gh["origin"] == "explicit"


def test_get_links_map(tmp_path):
    from scripts.records import links as L
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    L.replace_links(db, "e1", [{"url": "https://github.com/a/b"}])
    L.replace_links(db, "e2", [{"url": "https://x.com/y"}, {"url": "https://z.com/w"}])
    m = L.get_links_map(db)
    assert set(m.keys()) == {"e1", "e2"}
    assert len(m["e2"]) == 2


def test_relations_replace_idempotent_and_normalized(tmp_path):
    from scripts.records import links as L
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    edges = [
        {"a": "e2", "b": "e1", "kind": "shared_link", "score": 40, "evidence": {"url": "u"}},
        {"a": "e1", "b": "e3", "kind": "shared_entity", "score": 20, "evidence": {"entity": "X"}},
    ]
    L.replace_relations(db, "e1", edges)
    L.replace_relations(db, "e1", edges)  # 重复织边不翻倍
    rel = L.get_related(db, "e1")
    assert len(rel) == 2
    others = {r["other"] for r in rel}
    assert others == {"e2", "e3"}
    # entry_a < entry_b 规范化落库
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT entry_a, entry_b FROM relations").fetchall()
    conn.close()
    for a, b in rows:
        assert a < b
    # 换成空边 = 清除该条目所有边
    L.replace_relations(db, "e1", [])
    assert L.get_related(db, "e1") == []


def test_entities_column_roundtrip(tmp_path):
    from scripts.records import links as L
    from scripts.wiki_index.store import upsert_task, get_entry
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    upsert_task(db, "e1", source_input="https://x.com", status="pending")
    L.set_entry_entities(db, "e1", {"company": ["Anthropic"], "author": [], "product": ["Claude"], "series": []})
    got = L.get_entry_entities(db, "e1")
    assert got["company"] == ["Anthropic"] and got["product"] == ["Claude"]
    # 空字符串（老条目）→ 四键空 dict
    assert L.get_entry_entities(db, "e1")["series"] == []
    # 不存在的条目 → 四键空 dict
    missing = L.get_entry_entities(db, "nope")
    assert missing == {"company": [], "author": [], "product": [], "series": []}
    # all_entry_entities
    allm = L.all_entry_entities(db)
    assert "e1" in allm and allm["e1"]["company"] == ["Anthropic"]
