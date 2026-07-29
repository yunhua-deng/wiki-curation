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
