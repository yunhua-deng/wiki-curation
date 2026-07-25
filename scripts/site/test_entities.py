#!/usr/bin/env python3
"""
scripts/site/test_entities.py — 实体抽取模块测试。
"""
from pathlib import Path

import pytest

from scripts.site.entities import (
    load_aliases,
    normalize,
    normalize_list,
    _build_variant_map,
    _extract_author_patterns,
    extract_companies,
    extract_authors,
    extract_products,
    derive_series,
    extract_entities,
    build_entity_index,
)


@pytest.fixture
def aliases():
    return {
        "terms": {
            "具身智能": ["embodied intelligence", "embodied AI", "Embodied AI"],
            "视觉语言动作模型": ["VLA", "vla"],
        },
        "entities": {
            "company": {
                "蚂蚁灵波": ["Robbyant", "灵波"],
                "极佳科技": ["GigaWorld"],
                "英伟达": ["Nvidia", "NVIDIA"],
            },
            "product": {
                "LingBot-Vision": [],
                "LingBot-Depth 2.0": ["LingBot-Depth"],
                "LingBot-VLA 2.0": ["LingBot-VLA"],
                "VGGT": ["VGGT-omega"],
                "DINOv3": [],
            },
            "person": {
                "翁荔": ["Lilian Weng"],
                "何恺明": ["Kaiming He"],
            },
        },
        "series_roots": {
            "LingBot": ["LingBot-Vision", "LingBot-Depth", "LingBot-Depth 2.0", "LingBot-VLA", "LingBot-VLA 2.0"],
            "VGGT": ["VGGT", "VGGT-omega"],
            "DINO": ["DINOv3"],
        },
    }


# ---------------------------------------------------------------------------
# Alias normalization
# ---------------------------------------------------------------------------

def test_build_variant_map(aliases):
    vm = _build_variant_map(aliases)
    assert vm["embodied ai"] == "具身智能"
    assert vm["vla"] == "视觉语言动作模型"
    assert vm["robbyant"] == "蚂蚁灵波"
    assert vm["lilian weng"] == "翁荔"


def test_normalize_list(aliases):
    vm = _build_variant_map(aliases)
    assert normalize_list(["VLA", "vla", "Embodied AI"], vm) == ["视觉语言动作模型", "具身智能"]


# ---------------------------------------------------------------------------
# Company extraction
# ---------------------------------------------------------------------------

def test_extract_companies_from_text(aliases):
    vm = _build_variant_map(aliases)
    text = "蚂蚁灵波（Robbyant Team）发布了 LingBot-Vision。英伟达也参与了项目。"
    companies = extract_companies(text, aliases, vm)
    assert "蚂蚁灵波" in companies
    assert "英伟达" in companies


def test_extract_companies_from_key_info(aliases):
    vm = _build_variant_map(aliases)
    text = "**公司**：极佳科技（GigaWorld）\n**产品**：LingBot-Vision"
    companies = extract_companies(text, aliases, vm)
    assert "极佳科技" in companies


# ---------------------------------------------------------------------------
# Author extraction
# ---------------------------------------------------------------------------

def test_extract_authors_from_key_info(aliases):
    vm = _build_variant_map(aliases)
    text = "**作者**：翁荔，Lilian Weng\n**公司**：OpenAI"
    authors = extract_authors(text, aliases, vm)
    assert "翁荔" in authors


def test_extract_authors_from_blog_byline(aliases):
    vm = _build_variant_map(aliases)
    text = "文/翁荔\n\n本文介绍了 VLA 模型。"
    authors = extract_authors(text, aliases, vm)
    assert "翁荔" in authors


def test_extract_authors_filters_institution(aliases):
    vm = _build_variant_map(aliases)
    text = "**作者**：清华大学，何恺明\n"
    authors = extract_authors(text, aliases, vm)
    assert "何恺明" in authors
    assert "清华大学" not in authors


def test_extract_author_patterns():
    text = "作者｜翁荔\n**Authors**: Kaiming He, Saining Xie\nBy Lilian Weng"
    found = _extract_author_patterns(text)
    assert "翁荔" in found
    assert "Kaiming He" in found
    assert "Saining Xie" in found
    assert "Lilian Weng" in found


# ---------------------------------------------------------------------------
# Product extraction
# ---------------------------------------------------------------------------

def test_extract_products_from_text(aliases):
    vm = _build_variant_map(aliases)
    text = "LingBot-Vision 与 VGGT-omega 都是视觉基础模型。"
    products = extract_products(text, aliases, vm)
    assert "LingBot-Vision" in products
    assert "VGGT" in products  # VGGT-omega 归一到 VGGT


def test_extract_products_from_key_info(aliases):
    vm = _build_variant_map(aliases)
    text = "**产品**：LingBot-Depth 2.0，DINOv3"
    products = extract_products(text, aliases, vm)
    assert "LingBot-Depth 2.0" in products
    assert "DINOv3" in products


# ---------------------------------------------------------------------------
# Series derivation
# ---------------------------------------------------------------------------

def test_derive_series(aliases):
    products = ["LingBot-Vision", "LingBot-Depth 2.0"]
    series = derive_series(products, aliases)
    assert "LingBot" in series
    assert "VGGT" not in series


def test_derive_series_single_product(aliases):
    products = ["DINOv3"]
    series = derive_series(products, aliases)
    assert "DINO" in series


# ---------------------------------------------------------------------------
# Entry-level extraction
# ---------------------------------------------------------------------------

def test_extract_entities_with_tags(tmp_path, aliases):
    wiki_dir = tmp_path / "wiki"
    slug = "2026-07-08_entitytest"
    entry_dir = wiki_dir / "artifacts" / slug
    entry_dir.mkdir(parents=True)
    (entry_dir / f"{slug}_brief.md").write_text(
        "# Test\n\n**公司**：蚂蚁灵波\n**作者**：翁荔\n\n"
        "LingBot-Vision 和 VGGT-omega 是两款产品。",
        encoding="utf-8",
    )

    entry = {
        "id": slug,
        "title": "Entity Test",
        "overview": "overview",
        "tags": ["VLA", "具身智能", "翁荔"],
    }
    entities = extract_entities(entry, wiki_dir, aliases)

    assert "蚂蚁灵波" in entities["company"]
    assert "翁荔" in entities["author"]
    assert "LingBot-Vision" in entities["product"]
    assert "VGGT" in entities["product"]
    assert "LingBot" in entities["series"]
    assert "视觉语言动作模型" in entities["normalized_tags"]
    assert "具身智能" in entities["normalized_tags"]


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def test_build_entity_index(tmp_path, aliases):
    wiki_dir = tmp_path / "wiki"
    for slug, md in [
        ("2026-07-08_a", "**公司**：蚂蚁灵波\nLingBot-Vision。"),
        ("2026-07-08_b", "蚂蚁灵波和 LingBot-Depth 2.0。"),
        ("2026-07-08_c", "完全不相关的文章。"),
    ]:
        entry_dir = wiki_dir / "artifacts" / slug
        entry_dir.mkdir(parents=True)
        (entry_dir / f"{slug}_brief.md").write_text(md, encoding="utf-8")

    entries = [
        {"id": "2026-07-08_a", "title": "A", "overview": "", "tags": []},
        {"id": "2026-07-08_b", "title": "B", "overview": "", "tags": []},
        {"id": "2026-07-08_c", "title": "C", "overview": "", "tags": []},
    ]

    idx = build_entity_index(entries, wiki_dir, aliases)

    assert len(idx["by_entry"]) == 3
    assert set(idx["by_type"]["company"]["蚂蚁灵波"]) == {"2026-07-08_a", "2026-07-08_b"}
    assert "2026-07-08_c" not in idx["by_type"]["company"].get("蚂蚁灵波", [])
    assert "LingBot" in idx["by_type"]["series"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
