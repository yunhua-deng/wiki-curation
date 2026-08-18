#!/usr/bin/env python3
"""test_site_api.py — wiki 本地服务 record-links API 契约测试（不起真实 socket）。"""
from pathlib import Path

from scripts import conftest, paths
from scripts.records import schema as RS


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


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
    # 正常添加
    code, data = api.handle_add_link(tmp_path, {"id": "rec1", "url": "https://github.com/a/b"})
    assert code == 200 and data["ok"] and data["link"]["kind"] == "github"
