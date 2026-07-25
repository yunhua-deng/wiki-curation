#!/usr/bin/env python3
"""test_records_schema.py — record.json 校验器契约测试。"""
import copy
import json

from scripts.records import schema as RS


VALID_RECORD = {
    "version": "3.0",
    "id": "2026-07-21_1234",
    "title": "Test Project",
    "date": "2026-07-21",
    "topic_type": "project",
    "tldr": "一句话总结。",
    "tags": ["robotics", "VLA", "LLM"],
    "entities": {"company": ["Figure AI"], "author": [], "product": [], "series": []},
    "links": [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": 1, "verified": None},
        {"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv", "role": "related",
         "origin": "inferred", "fetched": 0, "verified": None},
    ],
    "source": {
        "input_type": "url",
        "source_type": "weixin",
        "direct_source": "https://mp.weixin.qq.com/s/abc",
        "original_source": "https://figure.ai/blog",
    },
}


def test_valid_record_passes():
    ok, errors = RS.validate_record(copy.deepcopy(VALID_RECORD))
    assert ok, f"unexpected errors: {errors}"
    assert errors == []


def test_missing_required_field_fails():
    rec = copy.deepcopy(VALID_RECORD)
    del rec["tldr"]
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("tldr" in e for e in errors)


def test_bad_version_fails():
    rec = copy.deepcopy(VALID_RECORD)
    rec["version"] = "2.0"
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("version" in e for e in errors)


def test_bad_topic_type_fails():
    rec = copy.deepcopy(VALID_RECORD)
    rec["topic_type"] = "not_a_type"
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("topic_type" in e for e in errors)


def test_entities_missing_bucket_fails():
    rec = copy.deepcopy(VALID_RECORD)
    del rec["entities"]["author"]
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("author" in e for e in errors)


def test_duplicate_canonical_per_kind_fails():
    rec = copy.deepcopy(VALID_RECORD)
    rec["links"].append({"url": "https://github.com/other/repo", "kind": "github",
                         "role": "canonical", "origin": "explicit"})
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("canonical" in e for e in errors)


def test_bad_link_url_fails():
    rec = copy.deepcopy(VALID_RECORD)
    rec["links"][0]["url"] = "not-a-url"
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("url" in e.lower() for e in errors)


def test_bad_link_kind_fails():
    rec = copy.deepcopy(VALID_RECORD)
    rec["links"][0]["kind"] = "twitter"
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("kind" in e for e in errors)


def test_duplicate_link_urls_fail():
    rec = copy.deepcopy(VALID_RECORD)
    rec["links"].append(copy.deepcopy(rec["links"][0]))
    ok, errors = RS.validate_record(rec)
    assert not ok
    assert any("duplicate" in e.lower() or "重复" in e for e in errors)


def test_empty_links_and_tags_bounds():
    rec = copy.deepcopy(VALID_RECORD)
    rec["links"] = []
    ok, errors = RS.validate_record(rec)
    assert ok, f"empty links should be legal: {errors}"
    rec["tags"] = []
    ok, errors = RS.validate_record(rec)
    assert not ok  # tags 至少 1 个


def test_classify_link_kind():
    assert RS.classify_link_kind("https://github.com/a/b") == "github"
    assert RS.classify_link_kind("https://arxiv.org/abs/1234.5678") == "arxiv"
    assert RS.classify_link_kind("https://huggingface.co/org/model") == "huggingface"
    assert RS.classify_link_kind("https://mp.weixin.qq.com/s/xx") == "weixin"
    assert RS.classify_link_kind("https://www.linkedin.com/posts/yy") == "linkedin"
    assert RS.classify_link_kind("https://docs.python.org/3/") == "docs"
    assert RS.classify_link_kind("https://figure.ai") == "homepage"
    assert RS.classify_link_kind("https://medium.com/@x/y") == "other"


def test_normalize_url():
    assert RS.normalize_url("HTTPS://GitHub.com/A/B/") == "https://github.com/A/B"
    assert RS.normalize_url("https://arxiv.org/abs/1234.5678#fig") == "https://arxiv.org/abs/1234.5678"
    assert RS.normalize_url("https://x.com/y/") == "https://x.com/y"


def test_normalize_url_strips_tracking():
    assert RS.normalize_url("https://x.com/p?utm_source=tw&a=1") == "https://x.com/p?a=1"
    assert RS.normalize_url("https://a.com/?fbclid=xyz") == "https://a.com"  # path / strip to empty
    assert RS.normalize_url("https://a.com/page?ref=other") == "https://a.com/page"
    assert RS.normalize_url("https://a.com/page?a=1&gclid=2&b=3") == "https://a.com/page?a=1&b=3"


def test_record_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_WORKSPACE", str(tmp_path))
    from scripts import paths
    RS.save_record("2026-07-21_1234", tmp_path, VALID_RECORD)
    assert paths.record_path("2026-07-21_1234", tmp_path).exists()
    got = RS.load_record("2026-07-21_1234", tmp_path)
    assert got["title"] == "Test Project"
    assert RS.load_record("nonexistent", tmp_path) is None
