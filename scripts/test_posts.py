#!/usr/bin/env python3
"""test_posts.py — 技术 post（trends 改造）契约测试。"""
import json
from pathlib import Path

import pytest

from scripts import conftest, paths
from scripts.records import schema as RS
from scripts.records import links as L


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


RECORD_A = {
    "version": "3.0", "id": "a1", "title": "Helix VLA", "date": "2026-07-20",
    "topic_type": "project", "tldr": "Figure 的 VLA 项目。",
    "summary": "核心要点：双系统架构。",
    "tags": ["robotics", "VLA"],
    "entities": {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []},
    "links": [{"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical",
               "origin": "explicit", "fetched": 1, "verified": None}],
    "source": {"input_type": "url", "source_type": "github"},
}
RECORD_B = dict(RECORD_A, id="b1", title="Octo Policy",
                links=[{"url": "https://github.com/octo-models/octo", "kind": "github",
                        "role": "canonical", "origin": "explicit", "fetched": 1, "verified": None}])


def test_slugify():
    from scripts import posts
    assert posts.slugify("World Models 训练场") == "world-models-训练场"
    assert posts.slugify("  a  b!!c  ") == "a-bc"
    assert posts.slugify("") == "post"


def test_gather_records_evidence(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("a1", tmp_path, RECORD_A)
    from scripts import posts
    ev = posts.gather_records_evidence(paths.db_path(tmp_path), tmp_path, ["a1"])
    assert ev[0]["title"] == "Helix VLA" and ev[0]["links"] == ["https://github.com/figure/helix"]
    with pytest.raises(posts.PostError) as ei:
        posts.gather_records_evidence(paths.db_path(tmp_path), tmp_path, ["ghost"])
    assert ei.value.code == "RECORD_MISSING"


def test_generate_post_task_records(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("a1", tmp_path, RECORD_A)
    RS.save_record("b1", tmp_path, RECORD_B)
    from scripts import posts
    task = posts.generate_post_task({"kind": "records", "ids": ["a1", "b1"]}, tmp_path,
                                    paths.db_path(tmp_path))
    assert task["task_mode"] == "post" and task["evidence_count"] == 2
    body = task["task"]
    for needle in ["技术 post", "a1", "b1", "hook", "禁止", "默认基于 wiki 证据"]:
        assert needle in body, needle
    assert task["staging_path"].endswith(".md")


def test_validate_post_md():
    from scripts import posts
    good = "# 标题\n\n" + "正文" * 500 + "\n\n引用 [2026-07-28_2563] 的结论。\n"
    ok, errors = posts.validate_post_md(good)
    assert ok, errors
    ok, errors = posts.validate_post_md("# t\n\n短")
    assert not ok and any("过短" in e for e in errors)
    ok, errors = posts.validate_post_md("没有标题" + "字" * 900 + " https://a.b/c")
    assert not ok and any("H1" in e for e in errors)
    ok, errors = posts.validate_post_md("# t\n\n" + "字" * 900)
    assert not ok and any("引用" in e or "evidence" in e for e in errors)


def test_auto_write_post_happy(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("a1", tmp_path, RECORD_A)
    conftest.seed_entry(paths.db_path(tmp_path), "a1", status="done")
    from scripts import posts

    def fake_runner(prompt, ws, timeout=900):
        m = [l for l in prompt.splitlines() if l.startswith("`") and l.endswith(".md`")]
        out = Path(m[0].strip("`"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# 融合分析\n\n" + "正文" * 500 + "\n\n见 [a1] 与 https://github.com/figure/helix\n",
                       encoding="utf-8")
        return {"ok": True, "stdout": "done", "stderr": ""}

    result = posts.auto_write_post({"kind": "records", "ids": ["a1"]}, tmp_path,
                                   paths.db_path(tmp_path), runner=fake_runner)
    assert result["ok"] and result["file"].startswith("posts/")
    final = tmp_path / result["file"]
    assert final.exists()
    meta = json.loads((final.parent / (result["stem"] + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["trigger"]["ids"] == ["a1"]
    # staging 已清空
    assert not list((tmp_path / "posts" / "_staging").glob("*.md"))
    # 站点 posts.json 已收录
    posts_json = json.loads((tmp_path / "site" / "data" / "posts.json").read_text(encoding="utf-8"))
    assert any(i["slug"] == result["stem"] for i in posts_json["items"])


def test_auto_write_post_no_output(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("a1", tmp_path, RECORD_A)
    from scripts import posts
    with pytest.raises(posts.PostError) as ei:
        posts.auto_write_post({"kind": "records", "ids": ["a1"]}, tmp_path,
                              paths.db_path(tmp_path),
                              runner=lambda p, ws, timeout=900: {"ok": False, "stderr": "boom"})
    assert ei.value.code == "WRITE_FAILED"


def test_suggest_post_topics_hub_detection(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    for i, slug in enumerate(["hub1", "n1", "n2", "n3"]):
        conftest.seed_entry(db, slug, status="done", topic_type="paper")
    url = "https://arxiv.org/abs/2501.0001"
    L.replace_links(db, "hub1", [{"url": url, "kind": "arxiv"}])
    for s in ("n1", "n2", "n3"):
        L.replace_links(db, s, [{"url": url, "kind": "arxiv"}])
        from scripts.records import relations as REL
        REL.rewire_relations(db, s)
    from scripts import posts
    suggestions = posts.suggest_post_topics(db, min_degree=3, top_n=5, ws=tmp_path)
    assert suggestions and suggestions[0]["anchor"] == "hub1"
    assert suggestions[0]["degree"] >= 3
    assert "hub1" in suggestions[0]["records"]
    assert "post --records" in suggestions[0]["suggested_cmd"]
    assert suggestions[0]["covered"] is False
