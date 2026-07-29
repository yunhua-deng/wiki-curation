#!/usr/bin/env python3
"""test_records_dive.py — record 深度解读（dive）契约测试。"""
import json
from pathlib import Path

import pytest

from scripts import conftest
from scripts import paths
from scripts.records import schema as RS


def _patch_ws(tmp: Path, monkeypatch):
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


# ---------- Task 1: paths + dest_base ----------

def test_dive_paths(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    base = tmp_path / "artifacts" / "rec1" / "dive"
    assert paths.dive_dir("rec1") == base
    assert paths.dive_raw_dir("rec1") == base / "raw"
    assert paths.dive_md_path("rec1") == base / "dive.md"
    assert paths.dive_json_path("rec1") == base / "dive.json"
    assert paths.dive_status_path("rec1") == base / "status.json"
    assert paths.dive_task_path("rec1") == base / "task.json"


def test_collect_sources_dest_base_override(tmp_path, monkeypatch):
    """dest_base 显式传入时材料落在该目录；不传时维持 paths.raw_dir（回归保护）。"""
    _patch_ws(tmp_path, monkeypatch)
    from scripts.exec import collect_materials as CM

    def fake_handler(subtype, src_dir, input_val, label, download_zip=False):
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "page.html").write_text("<html>ok</html>", encoding="utf-8")
        return {"label": label, "subtype": subtype, "status": "success", "files": ["page.html"]}

    monkeypatch.setattr(CM, "_run_handler", fake_handler)
    sources = [{"input_type": "url", "source_type": "generic_web", "input": "https://example.com/a"}]

    custom = tmp_path / "artifacts" / "rec1" / "dive" / "raw"
    log = CM.collect_sources("rec1", sources, max_depth=1, dest_base=custom)
    assert (custom / "s0" / "page.html").exists()
    assert (custom / "_drill_log.json").exists()
    assert log["summary"]["success"] == 1
    # 默认路径不被污染
    assert not (paths.raw_dir("rec1") / "s0" / "page.html").exists()


# ---------- Task 2: status + select_dive_links ----------

def _link(url, kind="other", role="related", fetched=None):
    return {"url": url, "kind": kind, "role": role, "origin": "explicit", "fetched": fetched}


def test_select_dive_links_canonical_first_and_skip_fetched(tmp_path):
    from scripts.records import dive as DV
    links = [
        _link("https://example.com/related1", role="related"),
        _link("https://github.com/a/b", kind="github", role="canonical"),
        _link("https://example.com/fetched", role="canonical", fetched=1),
        _link("https://arxiv.org/abs/2501.0001", kind="arxiv", role="related"),
    ]
    out = DV.select_dive_links(links)
    urls = [l["url"] for l in out]
    assert urls[0] == "https://github.com/a/b"          # canonical 优先
    assert "https://example.com/fetched" not in urls    # fetched==1 跳过
    assert urls[1:] == ["https://example.com/related1", "https://arxiv.org/abs/2501.0001"]


def test_select_dive_links_dedup_and_cap(tmp_path):
    from scripts.records import dive as DV
    links = [
        _link("https://example.com/a/?utm_source=x", role="canonical"),
        _link("https://example.com/a", role="related"),   # normalize 后重复
        _link("https://example.com/b"),
        _link("https://example.com/c"),
    ]
    out = DV.select_dive_links(links, max_links=2)
    assert [l["url"] for l in out] == ["https://example.com/a/?utm_source=x", "https://example.com/b"]


def test_status_roundtrip(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import dive as DV
    assert DV.read_status("rec1") == {}
    DV.write_status("rec1", None, "collecting")
    st = DV.read_status("rec1")
    assert st["state"] == "collecting" and st["updated_at"]
    DV.write_status("rec1", None, "failed", error="boom")
    st = DV.read_status("rec1")
    assert st["state"] == "failed" and st["error"] == "boom"


# ---------- Task 3: collect_dive + generate_dive_task ----------

VALID_RECORD = {
    "version": "3.0", "id": "rec1", "title": "Helix VLA Project", "date": "2026-07-20",
    "topic_type": "project", "tldr": "Figure 的人形机器人 VLA 项目。",
    "tags": ["robotics", "VLA"],
    "entities": {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []},
    "links": [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical",
         "origin": "explicit", "fetched": 0, "verified": None},
        {"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv", "role": "related",
         "origin": "inferred", "fetched": 1, "verified": None},
    ],
    "source": {"input_type": "url", "source_type": "weixin",
               "direct_source": "https://mp.weixin.qq.com/s/aaa", "original_source": ""},
}


def _seed_record(tmp: Path, record=None):
    RS.save_record("rec1", tmp, record or VALID_RECORD)


def test_collect_dive_happy_path(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    from scripts.records import dive as DV

    calls = {}

    def fake_collect(slug, sources, max_depth=None, dest_base=None):
        calls["sources"] = sources
        calls["dest_base"] = dest_base
        Path(dest_base).mkdir(parents=True, exist_ok=True)
        (Path(dest_base) / "s0").mkdir(exist_ok=True)
        (Path(dest_base) / "s0" / "readme.html").write_text("x", encoding="utf-8")
        return {"levels": [], "summary": {"total_files": 1, "success": 1, "failed": 0, "needs_manual": 0}}

    monkeypatch.setattr("scripts.records.dive._collect_sources", fake_collect)

    task = DV.collect_dive("rec1")
    # fetched==1 的 arxiv 被跳过，只抓 github canonical
    assert [s["input"] for s in calls["sources"]] == ["https://github.com/figure/helix"]
    assert Path(calls["dest_base"]) == paths.dive_raw_dir("rec1", tmp_path)
    assert task["task_mode"] == "dive" and task["slug"] == "rec1"
    assert "深度解读" in task["task"] and "更多内容请看" in task["task"]
    assert paths.dive_task_path("rec1", tmp_path).exists()
    st = DV.read_status("rec1", tmp_path)
    assert st["state"] == "awaiting_agent"
    assert st["detail"]["collected"] == 1


def test_collect_dive_errors(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import dive as DV
    # RECORD_MISSING
    with pytest.raises(DV.DiveError) as ei:
        DV.collect_dive("rec1")
    assert ei.value.code == "RECORD_MISSING"
    # NO_MATERIAL：record 无 links 且无 raw
    rec = dict(VALID_RECORD); rec["links"] = []
    _seed_record(tmp_path, rec)
    with pytest.raises(DV.DiveError) as ei:
        DV.collect_dive("rec1")
    assert ei.value.code == "NO_MATERIAL"
    assert DV.read_status("rec1", tmp_path)["state"] == "failed"
    # DIVE_EXISTS
    _seed_record(tmp_path)
    paths.dive_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.dive_md_path("rec1", tmp_path).write_text("# x", encoding="utf-8")
    with pytest.raises(DV.DiveError) as ei:
        DV.collect_dive("rec1")
    assert ei.value.code == "DIVE_EXISTS"


def test_generate_dive_task_contract(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    from scripts.records import dive as DV
    task = DV.generate_dive_task("rec1")
    assert task["task_mode"] == "dive"
    assert task["taskName"] == "dive-rec1"
    assert task["output_path"] == str(paths.dive_md_path("rec1", tmp_path).resolve())
    body = task["task"]
    for needle in ["record.json", "TL;DR", "核心内容", "分来源摘要", "原始出处",
                   "更多内容请看", "禁止", "dive/raw"]:
        assert needle in body, needle
    assert task["model"]  # 非空
