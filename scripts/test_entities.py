"""scripts/test_entities.py — 实体综合层：watch CRUD + 聚合契约测试。"""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts import conftest
from scripts.entity_summary import (EntityError, aggregate_entity, entity_index,
                                    find_entity, flatten_entities, list_watched,
                                    unwatch_entity, watch_entity, watched_hits)
from scripts.records import links as L
from scripts.wiki_index import ensure_schema


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "data" / "wiki.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(p)
    return p


def _seed(db):
    conftest.seed_entry(db, "2026-08-01_aaaa", status="done")
    conftest.seed_entry(db, "2026-08-02_bbbb", status="done")
    L.set_entry_entities(db, "2026-08-01_aaaa",
                         {"company": ["Figure AI"], "author": ["Brett Adcock"],
                          "product": ["Helix"], "series": []})
    L.set_entry_entities(db, "2026-08-02_bbbb",
                         {"company": ["Figure AI"], "author": [], "product": [], "series": []})


def test_watch_crud_and_idempotent(db):
    r1 = watch_entity(db, "Figure AI", type="company", note="人形机器人")
    assert r1 == {"name": "Figure AI", "already_watched": False}
    r2 = watch_entity(db, "Figure AI")
    assert r2["already_watched"] is True
    assert [w["name"] for w in list_watched(db)] == ["Figure AI"]
    assert unwatch_entity(db, "Figure AI") is True
    assert list_watched(db) == []
    assert unwatch_entity(db, "Figure AI") is False


def test_watch_empty_name_raises(db):
    with pytest.raises(EntityError):
        watch_entity(db, "  ")


def test_entity_index_and_find(db):
    _seed(db)
    idx = entity_index(db)
    assert idx["Figure AI"]["type"] == "company"
    assert sorted(idx["Figure AI"]["entries"]) == ["2026-08-01_aaaa", "2026-08-02_bbbb"]
    assert find_entity(db, "figure ai")["name"] == "Figure AI"  # 大小写不敏感
    assert find_entity(db, "No Such Entity") is None


def test_aggregate_entity(db):
    _seed(db)
    watch_entity(db, "Figure AI", type="company")
    L.replace_links(db, "2026-08-01_aaaa", [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical"},
        {"url": "https://example.com/x", "kind": "other", "role": "related"},
    ])
    agg = aggregate_entity(db, "Figure AI")
    assert agg["slug"] == "figure-ai"
    assert agg["type"] == "company"
    assert agg["watched"] is True
    assert {r["id"] for r in agg["records"]} == {"2026-08-01_aaaa", "2026-08-02_bbbb"}
    assert sum(t["count"] for t in agg["timeline"]) == 2
    co_names = [c["name"] for c in agg["co_entities"]]
    assert "Helix" in co_names and "Brett Adcock" in co_names
    assert agg["links"] == [{"url": "https://github.com/figure/helix", "kind": "github"}]


def test_aggregate_not_found_suggests(db):
    _seed(db)
    with pytest.raises(EntityError) as ei:
        aggregate_entity(db, "Figure A")  # difflib 相近
    assert ei.value.code == "ENTITY_NOT_FOUND"
    assert "Figure AI" in str(ei.value)


def test_watched_hits(db):
    watch_entity(db, "Figure AI")
    watch_entity(db, "Physical Intelligence")
    assert watched_hits(db, ["Figure AI", "Tesla"]) == ["Figure AI"]
    assert watched_hits(db, []) == []


def test_flatten_entities():
    assert flatten_entities({"company": ["A"], "author": ["B"], "product": [], "series": []}) == ["A", "B"]
    assert flatten_entities(None) == []
