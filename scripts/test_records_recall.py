#!/usr/bin/env python3
"""test_records_recall.py — 四层召回引擎契约测试。"""
import pytest

from scripts.wiki_index.schema import ensure_schema
from scripts.wiki_index.store import upsert_task
from scripts.records import links as L
from scripts.records import recall as R


@pytest.fixture
def kb(tmp_path):
    """构造 3 条目的知识库 fixture。"""
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    upsert_task(db, "e1", source_input="https://mp.weixin.qq.com/s/aaa",
                title="Figure Helix 解读", overview="Figure 的人形机器人 Helix VLA 模型",
                tags="robotics,VLA", status="done")
    upsert_task(db, "e2", source_input="https://arxiv.org/abs/2501.0001",
                title="Helix VLA paper", overview="Vision-Language-Action model for humanoid",
                tags="VLA,humanoid", status="done")
    upsert_task(db, "e3", source_input="https://example.com/blog/quantum",
                title="Quantum computing news", overview="量子计算进展",
                tags="quantum", status="done")
    L.replace_links(db, "e1", [
        {"url": "https://github.com/figure/helix", "kind": "github"},
        {"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv"},
    ])
    L.set_entry_entities(db, "e1", {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []})
    L.set_entry_entities(db, "e2", {"company": ["Figure AI"], "author": ["Brett Adcock"], "product": [], "series": []})
    return db


VARIANT_MAP = {
    "figure ai": "Figure AI",
    "figure": "Figure AI",
    "helix": "Helix",
    "brett adcock": "Brett Adcock",
}


def test_extract_urls():
    urls = R.extract_urls("看这个 https://github.com/a/b 和 https://arxiv.org/abs/1234.5678。")
    assert "https://github.com/a/b" in urls
    assert "https://arxiv.org/abs/1234.5678" in urls


def test_extract_entities_from_text():
    ents = R.extract_entities_from_text("Figure AI 发布了新的 Helix", VARIANT_MAP)
    assert "Figure AI" in ents and "Helix" in ents


def test_url_exact_match_tops(kb):
    out = R.recall(kb, "https://arxiv.org/abs/2501.0001", variant_map=VARIANT_MAP)
    assert out["matches"], "should match e2 by source_input"
    top = out["matches"][0]
    assert top["id"] == "e2"
    kinds = {r["kind"] for r in top["reasons"]}
    assert "url_exact" in kinds
    assert top["score"] >= 100


def test_url_exact_via_links_table(kb):
    out = R.recall(kb, "分享个仓库 https://github.com/figure/helix", variant_map=VARIANT_MAP)
    assert out["matches"][0]["id"] == "e1"
    kinds = {r["kind"] for r in out["matches"][0]["reasons"]}
    assert "shared_link" in kinds


def test_shared_link_score(kb):
    # arxiv URL 是 e2 的 source_input（url_exact, 100），同时在 e1.links 里（shared_link, 40）
    out = R.recall(kb, "论文全文在 https://arxiv.org/abs/2501.0001 这里", variant_map=VARIANT_MAP)
    by_id = {m["id"]: m for m in out["matches"]}
    assert by_id["e2"]["score"] == 100
    assert by_id["e1"]["score"] == 40
    e1_kinds = {r["kind"] for r in by_id["e1"]["reasons"]}
    assert "shared_link" in e1_kinds


def test_entity_match(kb):
    out = R.recall(kb, "Figure AI 最近怎么样", variant_map=VARIANT_MAP)
    ids = [m["id"] for m in out["matches"]]
    assert "e1" in ids and "e2" in ids
    for m in out["matches"]:
        if m["id"] in ("e1", "e2"):
            assert any(r["kind"] == "entity" and "Figure AI" in r["detail"] for r in m["reasons"])


def test_fts_match(kb):
    out = R.recall(kb, "humanoid robot", variant_map=VARIANT_MAP)
    ids = [m["id"] for m in out["matches"]]
    assert "e2" in ids
    e2 = [m for m in out["matches"] if m["id"] == "e2"][0]
    assert any(r["kind"] == "fts" for r in e2["reasons"])


def test_fts_url_tokens(kb):
    """URL 也能通过拆出的关键词命中 FTS（host/路径词）。"""
    out = R.recall(kb, "https://example.com/humanoid-vla-news", variant_map=VARIANT_MAP)
    ids = [m["id"] for m in out["matches"]]
    assert "e2" in ids  # overview 含 humanoid / VLA


def test_exclude_id(kb):
    out = R.recall(kb, "https://arxiv.org/abs/2501.0001", exclude_id="e2", variant_map=VARIANT_MAP)
    ids = [m["id"] for m in out["matches"]]
    assert "e2" not in ids


def test_no_match_returns_empty(kb):
    out = R.recall(kb, "完全无关的 zzzqqq", variant_map=VARIANT_MAP)
    assert out["matches"] == []


def test_matches_sorted_by_score(kb):
    out = R.recall(kb, "https://arxiv.org/abs/2501.0001 Figure AI", variant_map=VARIANT_MAP)
    scores = [m["score"] for m in out["matches"]]
    assert scores == sorted(scores, reverse=True)
