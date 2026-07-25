#!/usr/bin/env python3
"""test_records_tier_adaptation.py — list/sync/doctor 的记录层适配契约测试。"""
import json
from argparse import Namespace
from unittest import mock

from scripts import conftest
from scripts import paths
from scripts.records import links as L
from scripts.records.schema import save_record, RECORD_VERSION
from scripts.store import commands as store_cmds
from scripts.wiki_index.store import sync_with_files


def _make_ws(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_record_only(db, ws, slug):
    conftest.seed_entry(db, slug, source_input="https://x.com/p", status="done")
    save_record(slug, ws, {
        "version": RECORD_VERSION, "id": slug, "title": "T", "date": "",
        "topic_type": "article", "tldr": "t", "tags": ["a"],
        "entities": {"company": [], "author": [], "product": [], "series": []},
        "links": [{"url": "https://github.com/a/b", "kind": "github",
                   "role": "canonical", "origin": "explicit"}],
        "source": {"input_type": "url", "source_type": "generic_web",
                   "direct_source": "https://x.com/p", "original_source": ""},
    })
    L.replace_links(db, slug, [{"url": "https://github.com/a/b", "kind": "github"}])


def test_sync_basic(tmp_path):
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    conftest.seed_entry(db, "e1", source_input="https://x.com", status="done")
    report = sync_with_files(db, ws)
    assert report["db_total"] == 1 and report["both"] is not None


def test_list_json_includes_record_fields(tmp_path, capsys):
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    _seed_record_only(db, ws, "rec_only")
    conftest.seed_entry(db, "plain", source_input="https://z.com", status="pending")

    args = Namespace(limit=None, topic_type=None, input_type=None, source_type=None,
                     status=None, all=False, queue=False, human=False, json=True)
    store_cmds.cmd_list(args, db)
    rows = json.loads(capsys.readouterr().out)
    by_id = {r["id"]: r for r in rows}
    assert by_id["rec_only"]["has_record"] is True
    assert by_id["rec_only"]["links_count"] == 1
    assert by_id["plain"]["has_record"] is False
    assert by_id["plain"]["links_count"] == 0


def test_doctor_record_tier_check(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    _seed_record_only(db, ws, "rec_only")
    conftest.seed_entry(db, "no_artifacts", source_input="https://y.com", status="done")

    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: ws)
    monkeypatch.setattr("scripts.paths.db_path", lambda _=None: db)

    from scripts import doctor
    result = doctor.check_record_tier()
    assert result["passed"] is False
    assert "no_artifacts" in result["details"]
    assert "rec_only" not in result["details"]
