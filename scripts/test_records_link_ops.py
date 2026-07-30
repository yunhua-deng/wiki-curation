#!/usr/bin/env python3
"""test_records_link_ops.py — 手动添加链接（add_manual_link）契约测试。"""
import json
from pathlib import Path

import pytest

from scripts import conftest
from scripts import paths
from scripts.records import schema as RS
from scripts.records import links as L


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


RECORD = {
    "version": "3.0", "id": "rec1", "title": "Helix VLA Project", "date": "2026-07-20",
    "topic_type": "project", "tldr": "Figure 的人形机器人 VLA 项目。",
    "tags": ["robotics", "VLA"],
    "entities": {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []},
    "links": [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": 1, "verified": None},
    ],
    "source": {"input_type": "url", "source_type": "weixin",
               "direct_source": "https://mp.weixin.qq.com/s/aaa", "original_source": ""},
}


def _seed(tmp: Path):
    RS.save_record("rec1", tmp, RECORD)
    conftest.seed_entry(paths.db_path(tmp), "rec1", status="done")
    L.replace_links(paths.db_path(tmp), "rec1", RECORD["links"])


def test_add_manual_link_happy(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    from scripts.records import link_ops
    result = link_ops.add_manual_link("rec1", "https://arxiv.org/abs/2501.0001",
                                      ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert result["ok"] and result["link"]["kind"] == "arxiv"
    assert result["link"]["origin"] == "manual" and result["link"]["role"] == "related"
    # record.json 已更新且仍通过 schema 校验
    record = RS.load_record("rec1", tmp_path)
    assert len(record["links"]) == 2
    ok, errors = RS.validate_record(record)
    assert ok, errors
    # links 表同步
    db_links = L.get_links(paths.db_path(tmp_path), "rec1")
    assert [l["url"] for l in db_links] == [l["url"] for l in record["links"]]
    # 站点已重建且含新链接
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    rec1 = [e for e in entries if e["id"] == "rec1"][0]
    assert any(l["url"] == "https://arxiv.org/abs/2501.0001" for l in rec1["links"])


def test_add_manual_link_dedup_normalized(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    from scripts.records import link_ops
    # 带追踪参数/尾斜杠，normalize 后与已有链接相同
    with pytest.raises(link_ops.LinkOpError) as ei:
        link_ops.add_manual_link("rec1", "https://github.com/figure/helix/?utm_source=x",
                                 ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert ei.value.code == "LINK_EXISTS"


def test_add_manual_link_canonical_conflict(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    from scripts.records import link_ops
    with pytest.raises(link_ops.LinkOpError) as ei:
        link_ops.add_manual_link("rec1", "https://github.com/other/repo", role="canonical",
                                 ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert ei.value.code == "CANONICAL_CONFLICT"
    # 同 kind 以 related 添加则合法
    result = link_ops.add_manual_link("rec1", "https://github.com/other/repo", role="related",
                                      ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert result["ok"]


def test_add_manual_link_invalid(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    from scripts.records import link_ops
    with pytest.raises(link_ops.LinkOpError) as ei:
        link_ops.add_manual_link("rec1", "not-a-url", ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert ei.value.code == "INVALID_URL"
    with pytest.raises(link_ops.LinkOpError) as ei:
        link_ops.add_manual_link("rec1", "https://a.b/c", role="boss",
                                 ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert ei.value.code == "INVALID_ROLE"
    with pytest.raises(link_ops.LinkOpError) as ei:
        link_ops.add_manual_link("ghost", "https://a.b/c", ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert ei.value.code == "RECORD_MISSING"


def test_add_manual_link_rewires_relations(tmp_path, monkeypatch):
    """新链接与另一条目共享 URL → relations 织出 shared_link 边。"""
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec2", status="done")
    L.replace_links(paths.db_path(tmp_path), "rec2",
                    [{"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv"}])
    from scripts.records import link_ops
    result = link_ops.add_manual_link("rec1", "https://arxiv.org/abs/2501.0001",
                                      ws=tmp_path, db_path=paths.db_path(tmp_path))
    assert result["ok"] and result["relations"] >= 1


def test_cli_add_link(tmp_path, monkeypatch, capsys):
    _patch_ws(tmp_path, monkeypatch)
    _seed(tmp_path)
    from scripts import cli
    args = type("A", (), {"json": True, "quiet": True, "workspace": str(tmp_path),
                          "id": "rec1", "url": "https://huggingface.co/figure/helix",
                          "role": "related", "update_survey": False})()
    assert cli.cmd_add_link(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["data"]["link"]["kind"] == "huggingface"
    assert out["data"]["link"]["origin"] == "manual"

    # 重复 → LINK_EXISTS
    args2 = type("A", (), {"json": True, "quiet": True, "workspace": str(tmp_path),
                           "id": "rec1", "url": "https://huggingface.co/figure/helix",
                           "role": "related", "update_survey": False})()
    assert cli.cmd_add_link(args2) == 1
    out2 = json.loads(capsys.readouterr().out)
    assert out2["error"] == "LINK_EXISTS"
