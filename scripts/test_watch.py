#!/usr/bin/env python3
"""test_watch.py — 特别关注（watch/star）契约测试：store / CLI / API / 站点导出。"""
import json
from pathlib import Path

import pytest

from scripts import conftest, paths


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


# ---------- store 层 ----------

def test_set_watched_and_list(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    db = paths.db_path(tmp_path)
    conftest.seed_entry(db, "a1", status="done")
    conftest.seed_entry(db, "a2", status="done")
    from scripts.wiki_index import store

    e = store.set_watched(db, "a1", True)
    assert e["watched"] == 1 and e["watched_at"]
    assert [x["id"] for x in store.list_watched(db)] == ["a1"]

    e2 = store.set_watched(db, "a1", False)
    assert e2["watched"] == 0 and not e2["watched_at"]
    assert store.list_watched(db) == []


def test_set_watched_missing_entry(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.wiki_index import store
    with pytest.raises(ValueError):
        store.set_watched(paths.db_path(tmp_path), "ghost", True)


# ---------- CLI 层 ----------

def _watch_args(ws, **over):
    base = {"json": True, "quiet": True, "workspace": str(ws),
            "id": None, "on": False, "off": False}
    base.update(over)
    return type("A", (), base)()


def test_cli_watch_toggle_and_list(tmp_path, monkeypatch, capsys):
    _patch_ws(tmp_path, monkeypatch)
    conftest.seed_entry(paths.db_path(tmp_path), "a1", status="done")
    from scripts import cli

    assert cli.cmd_watch(_watch_args(tmp_path, id="a1")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["data"]["watched"] is True and out["data"]["watched_at"]

    # 再调一次（toggle）→ 取消
    assert cli.cmd_watch(_watch_args(tmp_path, id="a1")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["watched"] is False

    # --on 幂等
    assert cli.cmd_watch(_watch_args(tmp_path, id="a1", on=True)) == 0
    capsys.readouterr()  # 清掉第一次输出
    assert cli.cmd_watch(_watch_args(tmp_path, id="a1", on=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["watched"] is True

    # 无 --id → 列表
    assert cli.cmd_watch(_watch_args(tmp_path)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["count"] == 1 and out["data"]["watched"][0]["id"] == "a1"


# ---------- API 层 ----------

def test_api_watch(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    conftest.seed_entry(paths.db_path(tmp_path), "a1", status="done")
    from scripts.site import api

    code, data = api.handle_watch(tmp_path, {"id": "a1"}, client_ip="10.0.0.3")
    assert code == 403
    code, data = api.handle_watch(tmp_path, {"id": "../bad"})
    assert code == 400
    code, data = api.handle_watch(tmp_path, {"id": "ghost"})
    assert code == 404 and data["error"] == "ENTRY_NOT_FOUND"

    code, data = api.handle_watch(tmp_path, {"id": "a1", "on": True})
    assert code == 200 and data["ok"] and data["watched"] is True
    # 站点已重建且带 watched 标记
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    a1 = [e for e in entries if e["id"] == "a1"][0]
    assert a1["watched"] is True and a1["watched_at"]
    # 显式 off
    code, data = api.handle_watch(tmp_path, {"id": "a1", "on": False})
    assert code == 200 and data["watched"] is False
