#!/usr/bin/env python3
"""test_site_api.py — wiki 本地服务 dive API 契约测试（不起真实 socket）。"""
import json
from pathlib import Path

import pytest

from scripts import conftest, paths
from scripts.records import schema as RS


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


RECORD = {
    "version": "3.0", "id": "rec1", "title": "T", "date": "", "topic_type": "project",
    "tldr": "t", "tags": ["a"],
    "entities": {"company": [], "author": [], "product": [], "series": []},
    "links": [], "source": {"input_type": "url", "source_type": "generic_web"},
}


def test_handle_dive_request_validation(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.site import api
    # 非 loopback
    code, data = api.handle_dive_request(tmp_path, {"id": "rec1"}, client_ip="10.0.0.5")
    assert code == 403 and data["error"] == "FORBIDDEN"
    # 非法 id（路径穿越）
    code, data = api.handle_dive_request(tmp_path, {"id": "../etc"})
    assert code == 400 and data["error"] == "INVALID_ID"
    # record 不存在
    code, data = api.handle_dive_request(tmp_path, {"id": "rec1"})
    assert code == 404 and data["error"] == "RECORD_MISSING"


def test_handle_dive_request_conflicts_and_accept(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("rec1", tmp_path, RECORD)
    from scripts.records import dive as DV
    from scripts.site import api
    spawned = []
    spawner = lambda ws, slug, force=False: spawned.append((ws, slug, force))
    # collecting → 409 DIVE_RUNNING
    DV.write_status("rec1", tmp_path, "collecting")
    code, data = api.handle_dive_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409 and data["error"] == "DIVE_RUNNING"
    # awaiting_agent → 409 DIVE_RUNNING；force → 受理且 force 透传
    DV.write_status("rec1", tmp_path, "awaiting_agent")
    code, _ = api.handle_dive_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409
    code, data = api.handle_dive_request(tmp_path, {"id": "rec1", "force": True}, spawner=spawner)
    assert code == 202 and data["ok"] and spawned, data
    assert spawned[-1][2] is True  # force 必须透传给 spawner（E2E 回归：否则子进程 DIVE_EXISTS）
    # 已有 dive.md → 409 DIVE_EXISTS
    paths.dive_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.dive_md_path("rec1", tmp_path).write_text("# x", encoding="utf-8")
    code, data = api.handle_dive_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409 and data["error"] == "DIVE_EXISTS"


def test_handle_dive_status(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import dive as DV
    from scripts.site import api
    code, data = api.handle_dive_status(tmp_path, "../bad")
    assert code == 400
    code, data = api.handle_dive_status(tmp_path, "rec1")
    assert code == 200 and data["ok"] and data["has_dive"] is False and data["status"] == {}
    DV.write_status("rec1", tmp_path, "awaiting_agent")
    code, data = api.handle_dive_status(tmp_path, "rec1")
    assert data["status"]["state"] == "awaiting_agent"


def test_default_spawner_force_flag(tmp_path, monkeypatch):
    """_default_spawner 必须在 force=True 时给子进程命令追加 --force。"""
    from scripts.site import api
    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append(cmd)

    monkeypatch.setattr("scripts.site.api.subprocess.Popen", FakePopen)
    api._default_spawner(tmp_path, "rec1", force=True)
    assert "--force" in popen_calls[0]
    popen_calls.clear()
    api._default_spawner(tmp_path, "rec1", force=False)
    assert "--force" not in popen_calls[0]
    assert "--spawn-if-possible" in popen_calls[0]
