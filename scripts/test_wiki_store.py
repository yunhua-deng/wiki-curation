#!/usr/bin/env python3
"""test_wiki_store.py — wiki_index store 的 FTS 搜索契约测试（CJK 子串召回）。"""
import pytest

from scripts.wiki_index.schema import ensure_schema
from scripts.wiki_index import store


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "wiki.db"
    ensure_schema(path)
    return path


def test_search_cjk_substring_roundtrip(db):
    """CJK 子串召回：嵌在长词里的中文词也能命中（字符级短语匹配）。"""
    store.upsert_task(db, "e1", title="具身智能机器人抓取",
                      overview="Figure 的人形机器人", tags="robotics", status="done")
    for q in ('智能', '机器人', '具身智能'):
        ids = [e["id"] for e in store.search(db, q)]
        assert "e1" in ids, f"query {q!r} should find e1, got {ids}"


def test_search_latin_still_works(db):
    """Latin 查询不发生回归。"""
    store.upsert_task(db, "e1", title="Helix VLA paper",
                      overview="Vision-Language-Action for humanoid", status="done")
    store.upsert_task(db, "e2", title="具身智能机器人", overview="中文条目", status="done")
    ids = [e["id"] for e in store.search(db, "Helix")]
    assert ids == ["e1"]


def test_search_no_match(db):
    store.upsert_task(db, "e1", title="具身智能机器人", status="done")
    assert store.search(db, "完全无关的zzzqqq") == []
