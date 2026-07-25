#!/usr/bin/env python3
"""test_records_commands.py — recall / verify-links / backfill-records / add 自动召回的 CLI 契约测试。"""
import json
from argparse import Namespace
from unittest import mock

import pytest

from scripts import conftest
from scripts import paths
from scripts.records import links as L
from scripts.records import verify_links as VL


def _make_ws(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ============================================================
# verify-links
# ============================================================

def test_verify_links_marks_reachable(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    conftest.seed_entry(db, "e1", status="done")
    L.replace_links(db, "e1", [
        {"url": "https://github.com/a/b"},
        {"url": "https://dead.example.com/x"},
    ])

    def fake_check(url, timeout=10):
        return "github.com" in url
    monkeypatch.setattr(VL, "check_url", fake_check)

    result = VL.verify_entry_links(db, "e1")
    assert result["checked"] == 2 and result["ok"] == 1 and result["fail"] == 1

    links = {l["url"]: l["verified"] for l in L.get_links(db, "e1")}
    assert links["https://github.com/a/b"] == 1
    assert links["https://dead.example.com/x"] == 0

    # 已验证的不再重复检查
    result2 = VL.verify_entry_links(db, "e1")
    assert result2["checked"] == 0


# ============================================================
# CLI: recall / add 自动召回
# ============================================================

def _cli_args(**kwargs):
    defaults = {"json": True, "quiet": True, "workspace": None}
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_cmd_recall_json(tmp_path, monkeypatch, capsys):
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    conftest.seed_entry(db, "e1", source_input="https://arxiv.org/abs/2501.0001",
                        status="done")
    monkeypatch.setenv("WIKI_WORKSPACE", str(ws))

    from scripts import cli
    rc = cli.cmd_recall(_cli_args(input="https://arxiv.org/abs/2501.0001", limit=5))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert out["data"]["matches"][0]["id"] == "e1"


def test_cmd_add_auto_recall(tmp_path, monkeypatch, capsys):
    """add 成功后 JSON 输出带 recall 字段；--no-recall 时不带。"""
    ws = _make_ws(tmp_path)
    db = paths.db_path(ws)
    conftest.seed_entry(db, "exist1", source_input="https://arxiv.org/abs/2501.0001",
                        status="done")
    monkeypatch.setenv("WIKI_WORKSPACE", str(ws))

    from scripts import cli
    args = _cli_args(input=["https://arxiv.org/abs/2501.0001"], inputs_file=None,
                     append_to=None, input_type="unknown", source_type="unknown",
                     depth="brief", source_prompt=None, id=None, no_recall=False)
    rc = cli.cmd_add(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert "recall" in out["data"]
    assert out["data"]["recall"]["matches"][0]["id"] == "exist1"

    capsys.readouterr()
    args2 = _cli_args(input=["https://example.com/other"], inputs_file=None,
                      append_to=None, input_type="unknown", source_type="unknown",
                      depth="brief", source_prompt=None, id=None, no_recall=True)
    rc2 = cli.cmd_add(args2)
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["ok"]
    assert "recall" not in out2["data"]
