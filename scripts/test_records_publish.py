#!/usr/bin/env python3
"""test_records_publish.py — 记录发布（publish 默认路径）+ 关联织边契约测试。"""
import json
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

from scripts import conftest
from scripts import paths
from scripts import wiki_index
from scripts.publish import commands as publish_cmds
from scripts.records import links as L
from scripts.records import relations as REL
from scripts.records import schema as RS

SCRIPT_DIR = Path(__file__).resolve().parent

VALID_RECORD = {
    "version": "3.0",
    "id": "rec1",
    "title": "Helix VLA Project",
    "date": "2026-07-20",
    "topic_type": "project",
    "tldr": "Figure 的人形机器人 VLA 项目。",
    "tags": ["robotics", "VLA"],
    "entities": {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []},
    "links": [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": None, "verified": None},
        {"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv", "role": "related",
         "origin": "inferred", "fetched": None, "verified": None},
    ],
    "source": {"input_type": "url", "source_type": "weixin",
               "direct_source": "https://mp.weixin.qq.com/s/aaa",
               "original_source": "https://github.com/figure/helix"},
}


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


def _seed_record_entry(db_path, tmp: Path, record=None):
    conftest.seed_entry(
        db_path, "rec1",
        source_input="https://mp.weixin.qq.com/s/aaa",
        input_type="url", source_type="weixin", topic_type="article",
        status="running",
        events=[("ENQUEUE", {}), ("STARTED", {}), ("FETCH", {}), ("GATE", {})],
    )
    raw = paths.raw_dir("rec1", tmp)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "_drill_log.json").write_text(json.dumps({
        "levels": [{"level": 1, "entries": [
            {"input": "https://mp.weixin.qq.com/s/aaa", "status": "success"},
            {"input": "https://github.com/figure/helix", "status": "success"},
        ]}],
    }), encoding="utf-8")
    RS.save_record("rec1", tmp, record or VALID_RECORD)


def _publish_args(**kwargs):
    defaults = {"id": "rec1", "depth": None, "spec": None, "title": None, "json": True}
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_publish_record_happy_path(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    _seed_record_entry(db, tmp_path)
    # 另一个已存在的条目：共享 arxiv link + 共享实体
    conftest.seed_entry(db, "rec2", source_input="https://arxiv.org/abs/2501.0001",
                        topic_type="paper", status="done")
    L.replace_links(db, "rec2", [{"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv"}])
    L.set_entry_entities(db, "rec2", {"company": ["Figure AI"], "author": [], "product": [], "series": []})

    captured = []
    with mock.patch("builtins.print", captured.append):
        publish_cmds.cmd_publish(_publish_args(), db, tmp_path, SCRIPT_DIR)

    data = json.loads(captured[-1])
    assert data["ok"] and data["mode"] == "record"
    assert data["links"] == 2 and data["relations"] >= 1

    entry = wiki_index.get_entry(db, "rec1")
    assert entry["status"] == "done"
    assert entry["title"] == "Helix VLA Project"
    assert entry["overview"] == VALID_RECORD["tldr"]
    assert entry["topic_type"] == "project"
    assert entry["spec_version"] == "3.0"

    # links 表
    links = L.get_links(db, "rec1")
    assert len(links) == 2
    # fetched 回填：github 在 drill_log 中 success；arxiv 不在
    gh = [l for l in links if l["kind"] == "github"][0]
    arxiv = [l for l in links if l["kind"] == "arxiv"][0]
    assert gh["fetched"] == 1 and arxiv["fetched"] == 0
    rec = RS.load_record("rec1", tmp_path)
    rec_gh = [l for l in rec["links"] if l["kind"] == "github"][0]
    assert rec_gh["fetched"] == 1

    # entities 列
    ents = L.get_entry_entities(db, "rec1")
    assert ents["company"] == ["Figure AI"] and ents["product"] == ["Helix"]

    # relations 边：shared_link(rec2 arxiv) + shared_entity(Figure AI)
    rel = L.get_related(db, "rec1")
    kinds = {r["kind"] for r in rel}
    assert "shared_link" in kinds and "shared_entity" in kinds
    assert all(r["other"] == "rec2" for r in rel)


    # DONE 事件
    events = wiki_index.get_events(db, slug="rec1", action="DONE")
    assert len(events) == 1


def test_publish_record_invalid_fails(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    bad = dict(VALID_RECORD)
    bad["topic_type"] = "not_a_type"
    _seed_record_entry(db, tmp_path, record=bad)

    with pytest.raises(SystemExit):
        with mock.patch("builtins.print"):
            publish_cmds.cmd_publish(_publish_args(), db, tmp_path, SCRIPT_DIR)

    entry = wiki_index.get_entry(db, "rec1")
    assert entry["status"] == "failed"
    assert "verify" in (entry.get("error") or "").lower()


def test_publish_record_missing_file_fails(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "rec1", status="running",
                        events=[("ENQUEUE", {}), ("STARTED", {})])

    with pytest.raises(SystemExit):
        with mock.patch("builtins.print"):
            publish_cmds.cmd_publish(_publish_args(), db, tmp_path, SCRIPT_DIR)

    entry = wiki_index.get_entry(db, "rec1")
    assert entry["status"] == "failed"


def test_publish_article_legacy_by_depth(tmp_path, monkeypatch):
    """v3.1：publish --depth brief → 历史文章路径（标记 done，不做 verify_output）。"""
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "art1", status="running",
                        events=[("ENQUEUE", {}), ("STARTED", {})])
    article = paths.article_path("art1", "brief", tmp_path)
    article.parent.mkdir(parents=True, exist_ok=True)
    article.write_text("# Hello\n**Ver:** 1.0-brief\n", encoding="utf-8")

    captured = []
    with mock.patch("builtins.print", captured.append):
        publish_cmds.cmd_publish(_publish_args(id="art1", depth="brief"), db, tmp_path, SCRIPT_DIR)

    data = json.loads(captured[-1])
    assert data["ok"] and data.get("depth") == "brief"
    entry = wiki_index.get_entry(db, "art1")
    assert entry["status"] == "done"


def test_compute_relations_kinds(tmp_path):
    """compute_relations 四种边类型。"""
    from scripts.wiki_index.schema import ensure_schema
    db = tmp_path / "wiki.db"
    ensure_schema(db)
    conftest.seed_entry(db, "a", source_input="https://x.com/p", status="done")
    conftest.seed_entry(db, "b", source_input="https://x.com/p", status="done")
    conftest.seed_entry(db, "c", source_input="https://y.com", status="done")
    L.replace_links(db, "a", [{"url": "https://github.com/o/r"}])
    L.replace_links(db, "c", [{"url": "https://github.com/o/r"}])
    L.set_entry_entities(db, "a", {"company": ["X"], "author": [], "product": [], "series": []})
    L.set_entry_entities(db, "c", {"company": ["X"], "author": [], "product": [], "series": []})
    wiki_index.upsert_task(db, "a", tags="vla,robotics")
    wiki_index.upsert_task(db, "c", tags="vla,robotics")

    edges = REL.compute_relations(db, "a")
    by_target = {}
    for e in edges:
        other = e["b"] if e["a"] == "a" else e["a"]
        by_target.setdefault(other, set()).add(e["kind"])
    assert "same_url" in by_target["b"]
    assert "shared_link" in by_target["c"]
    assert "shared_entity" in by_target["c"]
    assert "tag_overlap" in by_target["c"]


# ---------- v3.7: add-time RECALL 事件 → record preview 注入 ----------

def test_publish_injects_recall_preview(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    _seed_record_entry(db, tmp_path)
    from scripts import wiki_index
    wiki_index.record_event(db, "rec1", "RECALL", {
        "query": "https://mp.weixin.qq.com/s/aaa",
        "matches": [{"id": "rec2", "title": "Old Paper", "score": 85.0,
                     "reasons": [{"kind": "shared_link", "detail": "arxiv.org/abs/2501.0001"}]}],
    })
    from scripts.records.publish_record import publish_record
    publish_record(_publish_args(), db, tmp_path, SCRIPT_DIR)
    record = RS.load_record("rec1", tmp_path)
    assert record.get("preview", {}).get("recall", {}).get("matches")
    m = record["preview"]["recall"]["matches"][0]
    assert m["id"] == "rec2" and m["score"] == 85.0
    assert record["preview"]["recall"]["added_at"]
    # 站点 entries.json 带出 preview
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    rec1 = [e for e in entries if e["id"] == "rec1"][0]
    assert rec1["preview"]["recall"]["matches"][0]["title"] == "Old Paper"


def test_publish_without_recall_event_no_preview(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    _seed_record_entry(db, tmp_path)
    from scripts.records.publish_record import publish_record
    publish_record(_publish_args(), db, tmp_path, SCRIPT_DIR)
    record = RS.load_record("rec1", tmp_path)
    assert "preview" not in record


def test_cmd_add_persists_recall_event(tmp_path, monkeypatch, capsys):
    _patch_ws(tmp_path, monkeypatch)
    from scripts import cli, wiki_index
    monkeypatch.setattr(cli, "_wiki_db_cmd", lambda *a, **kw: {
        "ok": True, "data": {"id": "rec9", "joined_input": "https://x.com/a"}})
    fake_recall = {"matches": [{"id": "rec1", "title": "T", "score": 42.0,
                                "reasons": [{"kind": "entity", "detail": "Figure AI"}]}]}
    monkeypatch.setattr("scripts.records.recall.recall",
                        lambda db, q, limit=5, exclude_id=None: fake_recall)
    args = type("A", (), {"json": True, "quiet": True, "workspace": str(tmp_path),
                          "input": ["https://x.com/a"], "inputs_file": None, "append_to": None,
                          "input_type": "url", "source_type": "generic_web",
                          "source_prompt": None, "id": None, "no_recall": False})()
    assert cli.cmd_add(args) == 0
    events = wiki_index.get_events(paths.db_path(tmp_path), slug="rec9", action="RECALL")
    assert events and json.loads(events[0]["detail"])["matches"][0]["id"] == "rec1"
