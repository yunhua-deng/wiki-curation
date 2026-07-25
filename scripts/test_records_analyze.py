#!/usr/bin/env python3
"""test_records_analyze.py — 分析层（cluster / dedup / emit_trend_task）契约测试。"""
import pytest

from scripts.wiki_index.schema import ensure_schema
from scripts.wiki_index.store import upsert_task
from scripts.records import links as L
from scripts.records import analyze as AZ


@pytest.fixture
def kb(tmp_path):
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    upsert_task(db, "e1", source_input="https://arxiv.org/abs/2501.0001",
                title="Helix VLA paper", overview="Vision-Language-Action for humanoid",
                tags="VLA,humanoid", status="done")
    upsert_task(db, "e2", source_input="https://github.com/figure/helix",
                title="Figure Helix", overview="Helix humanoid robot VLA model",
                tags="robotics,VLA", status="done")
    upsert_task(db, "e3", source_input="https://example.com/quantum",
                title="Quantum news", overview="量子计算进展", tags="quantum", status="done")
    L.replace_links(db, "e1", [{"url": "https://arxiv.org/abs/2501.0001", "role": "canonical"}])
    L.replace_links(db, "e2", [{"url": "https://arxiv.org/abs/2501.0001", "role": "related"},
                               {"url": "https://github.com/figure/helix", "role": "canonical"}])
    L.set_entry_entities(db, "e1", {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []})
    L.set_entry_entities(db, "e2", {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []})
    L.replace_relations(db, "e1", [
        {"a": "e1", "b": "e2", "kind": "shared_link", "score": 40,
         "evidence": {"url": "https://arxiv.org/abs/2501.0001"}},
        {"a": "e1", "b": "e2", "kind": "shared_entity", "score": 20,
         "evidence": {"entity": "Figure AI"}},
    ])
    return db


def test_cluster_finds_related_entries(kb):
    result = AZ.cluster(kb, "Helix humanoid", variant_map={})
    ids = [e["id"] for e in result["entries"]]
    assert "e1" in ids and "e2" in ids
    assert "e3" not in ids
    assert result["cluster_size"] == 2
    # 每个条目带命中原因
    for e in result["entries"]:
        assert e["reasons"], "entry must carry reasons"
    # hints 非空
    assert "Figure AI" in result["hints"]["top_entities"]


def test_cluster_empty_topic(kb):
    result = AZ.cluster(kb, "zzzqqq 完全无关", variant_map={})
    assert result["cluster_size"] == 0
    assert result["entries"] == []


def test_dedup_candidates(kb):
    cands = AZ.dedup_candidates(kb, min_score=40)
    assert len(cands) >= 1
    pair = cands[0]
    assert {pair["a"], pair["b"]} == {"e1", "e2"}
    assert pair["kind"] == "shared_link"
    assert pair["evidence"].get("url")


def test_emit_trend_task_envelope(kb):
    c = AZ.cluster(kb, "Helix", variant_map={})
    task = AZ.emit_trend_task(c)
    assert task["task_mode"] == "trend"
    assert task["mode"] == "run"
    assert "wiki/trends/" in task["task"]
    assert "Helix" in task["task"]
    assert "禁止编造" in task["task"]
    assert isinstance(task["fallback"], list)


def test_discover_topics(kb, monkeypatch):
    """discover：近窗口热点排序 + 已覆盖主题标记。"""
    from scripts import paths as _paths
    monkeypatch.setattr(_paths, "get_workspace", lambda _=None: _paths.Path(kb.parent))
    out = AZ.discover_topics(kb, recent_days=3650, min_recent=1, top_n=10)
    assert out, "discover should find topics"
    kinds = {c["kind"] for c in out}
    assert "tag" in kinds or "entity" in kinds
    scores = [c["score"] for c in out]
    assert scores == sorted(scores, reverse=True)
    # VLA / Figure AI 应该在列（fixture 中各出现 2 次）
    names = {c["name"] for c in out}
    assert "VLA" in names or "Figure AI" in names
