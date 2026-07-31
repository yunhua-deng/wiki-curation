#!/usr/bin/env python3
"""test_tracking.py — 实体跟踪（tracking topics）契约测试。"""
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


RECORD = {
    "version": "3.0", "id": "a1", "title": "World Labs 收购 SceniX", "date": "2026-07-22",
    "topic_type": "company", "tldr": "t",
    "tags": ["世界模型"],
    "entities": {"company": ["World Labs"], "author": ["Yunzhu Li"], "product": [], "series": []},
    "links": [], "source": {"input_type": "url", "source_type": "weixin"},
}

GOOD_DIGEST = """# Yunzhu Li — 跟踪
> 更新：2026-07-31 · 第 1 版 · 关联 1 条记录

## 画像
哥伦比亚大学助理教授，长期做仿真与学习式动力学。

## 动态时间线
- [a1]（2026-07-22）World Labs 收购 SceniX——仿真路线迁移

## 近期线索
暂无新线索。

## 已关联记录
- a1

## 来源
- https://example.com
"""


def test_slugify_name():
    from scripts import tracking
    assert tracking.slugify_name("Yunzhu Li") == "yunzhu-li"
    assert tracking.slugify_name(" 李飞飞 ") == "李飞飞"


def test_create_topic_associates_records(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "a1", status="done")
    from scripts.wiki_index import store
    store.upsert_task(db, "a1", title="World Labs 收购 SceniX")
    L.set_entry_entities(db, "a1", {"company": ["World Labs"], "author": ["Yunzhu Li"],
                                    "product": [], "series": []})
    from scripts import tracking
    topic = tracking.create_topic("Yunzhu Li", kind="person", ws=tmp_path, db_path=db)
    assert topic["slug"] == "yunzhu-li"
    assert topic["records"] == ["a1"]
    assert topic["records_detail"][0]["match"] == "entity:author"
    assert tracking.load_topic("yunzhu-li", tmp_path)["name"] == "Yunzhu Li"
    # 重复创建 → TOPIC_EXISTS
    with pytest.raises(tracking.TrackError) as ei:
        tracking.create_topic("Yunzhu Li", ws=tmp_path, db_path=db)
    assert ei.value.code == "TOPIC_EXISTS"


def test_find_records_fts_fallback(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "a2", status="done", source_input="https://x.com/y")
    from scripts.wiki_index import store
    store.upsert_task(db, "a2", title="某篇关于稀疏注意力的论文")
    from scripts import tracking
    hits = tracking.find_records_for_entity(db, "稀疏注意力")
    assert hits and hits[0]["id"] == "a2"


def test_validate_digest_md():
    from scripts import tracking
    ok, errors = tracking.validate_digest_md(GOOD_DIGEST, {"records": ["a1"]})
    assert ok, errors
    ok, errors = tracking.validate_digest_md("# t\n\n## 画像\nx", {"records": ["a1"]})
    assert not ok and any("动态时间线" in e for e in errors)
    ok, errors = tracking.validate_digest_md("# t\n\n## 画像\nx\n\n## 动态时间线\ny\n\n## 已关联记录\n无", {"records": ["a1"]})
    assert not ok and any("record id" in e for e in errors)


def test_auto_write_digest_happy(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "a1", status="done")
    from scripts.wiki_index import store
    store.upsert_task(db, "a1", title="World Labs 收购 SceniX")
    L.set_entry_entities(db, "a1", {"company": [], "author": ["Yunzhu Li"], "product": [], "series": []})
    from scripts import tracking
    tracking.create_topic("Yunzhu Li", ws=tmp_path, db_path=db)

    def fake_runner(prompt, ws, timeout=900):
        out = Path(prompt.splitlines()[1].strip("`"))
        out.write_text(GOOD_DIGEST, encoding="utf-8")
        return {"ok": True, "stdout": "done", "stderr": ""}

    result = tracking.auto_write_digest("yunzhu-li", tmp_path, db, runner=fake_runner)
    assert result["ok"] and result["revision"] == 1
    assert (tmp_path / "tracking" / "yunzhu-li" / "digest.md").exists()
    assert not (tmp_path / "tracking" / "yunzhu-li" / "digest.new.md").exists()
    tj = tracking.load_topic("yunzhu-li", tmp_path)
    assert tj["digest_revision"] == 1
    tracking_json = json.loads((tmp_path / "site" / "data" / "tracking.json").read_text(encoding="utf-8"))
    assert tracking_json and tracking_json[0]["slug"] == "yunzhu-li" and tracking_json[0]["has_digest"]


def test_refresh_topic_incremental(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "a1", status="done")
    from scripts.wiki_index import store
    store.upsert_task(db, "a1", title="World Labs 收购 SceniX")
    L.set_entry_entities(db, "a1", {"company": [], "author": ["Yunzhu Li"], "product": [], "series": []})
    from scripts import tracking
    tracking.create_topic("Yunzhu Li", ws=tmp_path, db_path=db)

    # 新增一条相关记录
    conftest.seed_entry(db, "b2", status="done", source_input="https://x.com/z")
    store.upsert_task(db, "b2", title="Yunzhu Li 的新论文 PGRD")

    def fake_runner(prompt, ws, timeout=900):
        assert "b2" in prompt  # 新增关联应进入任务
        out = Path(prompt.splitlines()[1].strip("`"))
        out.write_text(GOOD_DIGEST.replace("[a1]", "[a1]\n- [b2]（2026-07-31）新论文").replace("- a1", "- a1\n- b2"),
                       encoding="utf-8")
        return {"ok": True, "stdout": "done", "stderr": ""}

    result = tracking.refresh_topic("yunzhu-li", tmp_path, db, runner=fake_runner)
    assert result["ok"] and result["new_records"] == ["b2"]
    assert result["digest"]["revision"] == 1  # 本测试 create 未写 digest，refresh 写的是首版
    tj = tracking.load_topic("yunzhu-li", tmp_path)
    assert tj["refresh"]["last_at"] and tj["refresh"]["next_due"]
    assert "b2" in tj["records"]


def test_due_and_archive(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    from scripts import tracking
    tracking.create_topic("Yunzhu Li", ws=tmp_path, db_path=db)
    # 从未 refresh → 到期
    assert [t["slug"] for t in tracking.due_topics(tmp_path)] == ["yunzhu-li"]
    tracking.archive_topic("yunzhu-li", tmp_path)
    assert tracking.due_topics(tmp_path) == []
    assert tracking.list_topics(tmp_path) == []
    assert len(tracking.list_topics(tmp_path, include_archived=True)) == 1
