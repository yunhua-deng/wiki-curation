#!/usr/bin/env python3
"""test_site_api.py — wiki 本地服务 survey API 契约测试（不起真实 socket）。"""
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


def test_handle_survey_request_validation(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.site import api
    # 非 loopback
    code, data = api.handle_survey_request(tmp_path, {"id": "rec1"}, client_ip="10.0.0.5")
    assert code == 403 and data["error"] == "FORBIDDEN"
    # 非法 id（路径穿越）
    code, data = api.handle_survey_request(tmp_path, {"id": "../etc"})
    assert code == 400 and data["error"] == "INVALID_ID"
    # record 不存在
    code, data = api.handle_survey_request(tmp_path, {"id": "rec1"})
    assert code == 404 and data["error"] == "RECORD_MISSING"


def test_handle_survey_request_conflicts_and_accept(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    RS.save_record("rec1", tmp_path, RECORD)
    from scripts.records import survey as DV
    from scripts.site import api
    spawned = []
    spawner = lambda ws, slug, force=False: spawned.append((ws, slug, force))
    # collecting → 409 SURVEY_RUNNING
    DV.write_status("rec1", tmp_path, "collecting")
    code, data = api.handle_survey_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409 and data["error"] == "SURVEY_RUNNING"
    # awaiting_agent → 409 SURVEY_RUNNING；force → 受理且 force 透传
    DV.write_status("rec1", tmp_path, "awaiting_agent")
    code, _ = api.handle_survey_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409
    code, data = api.handle_survey_request(tmp_path, {"id": "rec1", "force": True}, spawner=spawner)
    assert code == 202 and data["ok"] and spawned, data
    assert spawned[-1][2] is True  # force 必须透传给 spawner（E2E 回归：否则子进程 SURVEY_EXISTS）
    # 已有 survey.md → 409 SURVEY_EXISTS
    paths.survey_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.survey_md_path("rec1", tmp_path).write_text("# x", encoding="utf-8")
    code, data = api.handle_survey_request(tmp_path, {"id": "rec1"}, spawner=spawner)
    assert code == 409 and data["error"] == "SURVEY_EXISTS"


def test_handle_survey_status(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import survey as DV
    from scripts.site import api
    code, data = api.handle_survey_status(tmp_path, "../bad")
    assert code == 400
    code, data = api.handle_survey_status(tmp_path, "rec1")
    assert code == 200 and data["ok"] and data["has_survey"] is False and data["status"] == {}
    DV.write_status("rec1", tmp_path, "awaiting_agent")
    code, data = api.handle_survey_status(tmp_path, "rec1")
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
    assert "--auto" in popen_calls[0]  # 网页触发默认端到端（采集→写作→发布）


RECORD2 = {
    "version": "3.0", "id": "rec1", "title": "T", "date": "", "topic_type": "project",
    "tldr": "t", "tags": ["a"],
    "entities": {"company": [], "author": [], "product": [], "series": []},
    "links": [{"url": "https://example.com/a", "kind": "other", "role": "canonical",
               "origin": "explicit", "fetched": 1, "verified": None}],
    "source": {"input_type": "url", "source_type": "generic_web"},
}


def test_handle_add_link_flow(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    RS.save_record("rec1", tmp_path, RECORD2)
    from scripts.site import api
    spawned = []
    spawner = lambda ws, slug, force=False: spawned.append((ws, slug, force))
    # 非 loopback
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "https://x.com/y"}, client_ip="10.0.0.2")
    assert code == 403
    # 非法 id / 缺 url / 非法 url
    code, data = api.handle_add_link(tmp_path, {"id": "../x", "url": "https://x.com/y"})
    assert code == 400
    code, data = api.handle_add_link(tmp_path, {"id": "rec1"})
    assert code == 400 and data["error"] == "INVALID_URL"
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "ftp://x"})
    assert code == 400 and data["error"] == "INVALID_URL"
    # 重复链接 → 409 LINK_EXISTS
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "https://example.com/a/"})
    assert code == 409 and data["error"] == "LINK_EXISTS"
    # 正常添加（不联动综述）→ 不触发 spawner
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "https://github.com/a/b"},
                                     spawner=spawner)
    assert code == 200 and data["ok"] and data["link"]["kind"] == "github"
    assert not spawned
    # 联动综述：已有 survey.md → spawner 带 force=True；没有则 force=False
    paths.survey_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.survey_md_path("rec1", tmp_path).write_text("# x", encoding="utf-8")
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "https://arxiv.org/abs/1.2",
                                                "update_survey": True}, spawner=spawner)
    assert code == 200 and data.get("survey", {}).get("state") == "collecting"
    assert spawned and spawned[-1][2] is True


def test_handle_track(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    conftest.seed_entry(paths.db_path(tmp_path), "a1", status="done")
    from scripts.site import api
    spawned = []
    spawner = lambda ws, cmd: spawned.append(cmd)
    # 非 loopback / 缺 name / 非法 name
    code, data = api.handle_track(tmp_path, {"name": "Yunzhu Li"}, client_ip="10.0.0.9")
    assert code == 403
    code, data = api.handle_track(tmp_path, {})
    assert code == 400 and data["error"] == "MISSING_NAME"
    code, data = api.handle_track(tmp_path, {"name": "x" * 100})
    assert code == 400
    # 创建 → 202 + spawner 收到 track --auto 命令
    code, data = api.handle_track(tmp_path, {"name": "Yunzhu Li", "kind": "person"},
                                  spawner=spawner)
    assert code == 202 and data["ok"] and data["slug"] == "yunzhu-li"
    assert spawned and "--auto" in spawned[-1] and "--name" in spawned[-1]
    # 已存在（模拟子进程已创建 topic）→ 200 幂等，不再 spawn
    from scripts import tracking as TR
    TR.create_topic("Yunzhu Li", ws=tmp_path, db_path=paths.db_path(tmp_path))
    code, data = api.handle_track(tmp_path, {"name": "Yunzhu Li"}, spawner=spawner)
    assert code == 200 and data["exists"] is True
    assert len(spawned) == 1
