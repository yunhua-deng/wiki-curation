#!/usr/bin/env python3
"""test_records_survey.py — record 综述（survey）契约测试。"""
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

def test_survey_paths(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    base = tmp_path / "artifacts" / "rec1" / "survey"
    assert paths.survey_dir("rec1") == base
    assert paths.survey_raw_dir("rec1") == base / "raw"
    assert paths.survey_md_path("rec1") == base / "survey.md"
    assert paths.survey_json_path("rec1") == base / "survey.json"
    assert paths.survey_status_path("rec1") == base / "status.json"
    assert paths.survey_task_path("rec1") == base / "task.json"


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

    custom = tmp_path / "artifacts" / "rec1" / "survey" / "raw"
    log = CM.collect_sources("rec1", sources, max_depth=1, dest_base=custom)
    assert (custom / "s0" / "page.html").exists()
    assert (custom / "_drill_log.json").exists()
    assert log["summary"]["success"] == 1
    # 默认路径不被污染
    assert not (paths.raw_dir("rec1") / "s0" / "page.html").exists()


# ---------- Task 2: status + select_survey_links ----------

def _link(url, kind="other", role="related", fetched=None):
    return {"url": url, "kind": kind, "role": role, "origin": "explicit", "fetched": fetched}


def test_select_survey_links_canonical_first_and_skip_fetched(tmp_path):
    from scripts.records import survey as DV
    links = [
        _link("https://example.com/related1", role="related"),
        _link("https://github.com/a/b", kind="github", role="canonical"),
        _link("https://example.com/fetched", role="canonical", fetched=1),
        _link("https://arxiv.org/abs/2501.0001", kind="arxiv", role="related"),
    ]
    out = DV.select_survey_links(links)
    urls = [l["url"] for l in out]
    assert urls[0] == "https://github.com/a/b"          # canonical 优先
    assert "https://example.com/fetched" not in urls    # fetched==1 跳过
    assert urls[1:] == ["https://example.com/related1", "https://arxiv.org/abs/2501.0001"]


def test_select_survey_links_dedup_and_cap(tmp_path):
    from scripts.records import survey as DV
    links = [
        _link("https://example.com/a/?utm_source=x", role="canonical"),
        _link("https://example.com/a", role="related"),   # normalize 后重复
        _link("https://example.com/b"),
        _link("https://example.com/c"),
    ]
    out = DV.select_survey_links(links, max_links=2)
    assert [l["url"] for l in out] == ["https://example.com/a/?utm_source=x", "https://example.com/b"]


def test_status_roundtrip(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import survey as DV
    assert DV.read_status("rec1") == {}
    DV.write_status("rec1", None, "collecting")
    st = DV.read_status("rec1")
    assert st["state"] == "collecting" and st["updated_at"]
    DV.write_status("rec1", None, "failed", error="boom")
    st = DV.read_status("rec1")
    assert st["state"] == "failed" and st["error"] == "boom"


# ---------- Task 3: collect_survey + generate_survey_task ----------

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


def test_collect_survey_happy_path(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    from scripts.records import survey as DV

    calls = {}

    def fake_collect(slug, sources, max_depth=None, dest_base=None):
        calls["sources"] = sources
        calls["dest_base"] = dest_base
        Path(dest_base).mkdir(parents=True, exist_ok=True)
        (Path(dest_base) / "s0").mkdir(exist_ok=True)
        (Path(dest_base) / "s0" / "readme.html").write_text("x", encoding="utf-8")
        return {"levels": [], "summary": {"total_files": 1, "success": 1, "failed": 0, "needs_manual": 0}}

    monkeypatch.setattr("scripts.records.survey._collect_sources", fake_collect)

    task = DV.collect_survey("rec1")
    # fetched==1 的 arxiv 被跳过，只抓 github canonical
    assert [s["input"] for s in calls["sources"]] == ["https://github.com/figure/helix"]
    assert Path(calls["dest_base"]) == paths.survey_raw_dir("rec1", tmp_path)
    assert task["task_mode"] == "survey" and task["slug"] == "rec1"
    assert "综述" in task["task"] and "更多内容请看" in task["task"]
    assert paths.survey_task_path("rec1", tmp_path).exists()
    st = DV.read_status("rec1", tmp_path)
    assert st["state"] == "awaiting_agent"
    assert st["detail"]["collected"] == 1


def test_collect_survey_errors(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import survey as DV
    # RECORD_MISSING
    with pytest.raises(DV.SurveyError) as ei:
        DV.collect_survey("rec1")
    assert ei.value.code == "RECORD_MISSING"
    # NO_MATERIAL：record 无 links 且无 raw
    rec = dict(VALID_RECORD); rec["links"] = []
    _seed_record(tmp_path, rec)
    with pytest.raises(DV.SurveyError) as ei:
        DV.collect_survey("rec1")
    assert ei.value.code == "NO_MATERIAL"
    assert DV.read_status("rec1", tmp_path)["state"] == "failed"
    # SURVEY_EXISTS
    _seed_record(tmp_path)
    paths.survey_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.survey_md_path("rec1", tmp_path).write_text("# x", encoding="utf-8")
    with pytest.raises(DV.SurveyError) as ei:
        DV.collect_survey("rec1")
    assert ei.value.code == "SURVEY_EXISTS"


def test_generate_survey_task_contract(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    from scripts.records import survey as DV
    task = DV.generate_survey_task("rec1")
    assert task["task_mode"] == "survey"
    assert task["taskName"] == "survey-rec1"
    assert task["output_path"] == str(paths.survey_md_path("rec1", tmp_path).resolve())
    body = task["task"]
    for needle in ["record.json", "TL;DR", "核心内容", "分来源摘要", "原始出处",
                   "更多内容请看", "禁止", "survey/raw"]:
        assert needle in body, needle
    assert task["model"] is None  # 模型跟随调用方，skill 不配置


# ---------- Task 4: validate + publish + status/queue ----------

GOOD_SURVEY_MD = """# Helix — 综述
> 生成：2026-07-29 · 基于 2 个来源 · 记录：rec1

## TL;DR
Helix 是 Figure 的 VLA 项目。

## 核心内容
### 架构
双系统架构。[来源](https://github.com/figure/helix)

## 分来源摘要
### github.com
仓库 README 描述了模型结构。更多内容请看：https://github.com/figure/helix

## 原始出处
- https://github.com/figure/helix
- https://arxiv.org/abs/2501.0001
"""


def test_validate_survey_md(tmp_path):
    from scripts.records import survey as DV
    ok, errors = DV.validate_survey_md(GOOD_SURVEY_MD)
    assert ok, errors
    for bad, needle in [
        ("", "空"),
        ("# t\n\n## TL;DR\nx\n\n## 核心内容\nx\n\n## 分来源摘要\nx", "原始出处"),
        ("## TL;DR\nx\n\n## 核心内容\nx\n\n## 分来源摘要\nx\n\n## 原始出处\n- https://a.b", "H1"),
        ("# t\n\n## TL;DR\nx\n\n## 核心内容\nx\n\n## 分来源摘要\nx\n\n## 原始出处\n无链接", "URL"),
    ]:
        ok, errors = DV.validate_survey_md(bad)
        assert not ok
        assert any(needle in e for e in errors), (needle, errors)
    big = GOOD_SURVEY_MD + "x" * (DV.SURVEY_MD_MAX_BYTES + 10)
    ok, errors = DV.validate_survey_md(big)
    assert not ok and any("过大" in e or "KB" in e for e in errors)


def test_publish_survey_happy_path(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    from scripts.records import survey as DV

    survey_dir = paths.survey_dir("rec1", tmp_path)
    survey_dir.mkdir(parents=True, exist_ok=True)
    (survey_dir / "survey.md").write_text(GOOD_SURVEY_MD, encoding="utf-8")
    raw = paths.survey_raw_dir("rec1", tmp_path)
    conftest.write_fetch_results(raw / "s0", [
        {"url": "https://github.com/figure/helix", "status": "success"}])

    result = DV.publish_survey("rec1", tmp_path, paths.db_path(tmp_path))
    assert result["ok"] and result["revision"] == 1
    meta = json.loads(paths.survey_json_path("rec1", tmp_path).read_text(encoding="utf-8"))
    assert meta["title"] == "Helix VLA Project"
    assert meta["sources"] == [{"url": "https://github.com/figure/helix", "status": "success"}]
    assert DV.read_status("rec1", tmp_path)["state"] == "done"
    # 站点索引
    surveys = json.loads((tmp_path / "site" / "data" / "surveys.json").read_text(encoding="utf-8"))
    assert surveys and surveys[0]["slug"] == "rec1"
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    rec1 = [e for e in entries if e["id"] == "rec1"][0]
    assert rec1["has_survey"] is True and rec1["survey"]["date"]
    # 再发布 → revision 自增、created_at 保留
    result2 = DV.publish_survey("rec1", tmp_path, paths.db_path(tmp_path))
    assert result2["revision"] == 2
    meta2 = json.loads(paths.survey_json_path("rec1", tmp_path).read_text(encoding="utf-8"))
    assert meta2["created_at"] == meta["created_at"]


def test_publish_survey_verify_failed(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    from scripts.records import survey as DV
    survey_dir = paths.survey_dir("rec1", tmp_path)
    survey_dir.mkdir(parents=True, exist_ok=True)
    (survey_dir / "survey.md").write_text("# too short", encoding="utf-8")
    with pytest.raises(DV.SurveyError) as ei:
        DV.publish_survey("rec1", tmp_path, paths.db_path(tmp_path))
    assert ei.value.code == "SURVEY_VERIFY_FAILED"
    assert DV.read_status("rec1", tmp_path)["state"] == "failed"


def test_survey_status_and_queue(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import survey as DV
    DV.write_status("a1", tmp_path, "awaiting_agent", detail={"collected": 2})
    DV.write_status("a2", tmp_path, "done")
    (tmp_path / "artifacts" / "a1").mkdir(exist_ok=True)
    (tmp_path / "artifacts" / "a2").mkdir(exist_ok=True)
    queue = DV.list_survey_queue(tmp_path)
    assert [q["id"] for q in queue] == ["a1"]
    st = DV.survey_status("a1", tmp_path)
    assert st["status"]["state"] == "awaiting_agent" and st["has_survey"] is False


# ---------- Task 5: CLI ----------

def _survey_args(ws, **over):
    base = {"json": True, "quiet": True, "workspace": str(ws),
            "id": None, "status": False, "queue": False, "task": False,
            "publish": False, "force": False, "max_links": 5,
            "spawn_if_possible": False}
    base.update(over)
    return type("A", (), base)()


def test_cli_survey_status_and_queue(tmp_path, monkeypatch, capsys):
    _patch_ws(tmp_path, monkeypatch)
    from scripts.records import survey as DV
    DV.write_status("rec1", tmp_path, "awaiting_agent", detail={"collected": 1})
    (tmp_path / "artifacts" / "rec1").mkdir(exist_ok=True)

    from scripts import cli
    args = _survey_args(tmp_path, id="rec1", status=True)
    assert cli.cmd_survey(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["data"]["status"]["state"] == "awaiting_agent"

    args = _survey_args(tmp_path, queue=True)
    assert cli.cmd_survey(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["count"] == 1 and out["data"]["queue"][0]["id"] == "rec1"


def test_cli_survey_error_shape(tmp_path, monkeypatch, capsys):
    _patch_ws(tmp_path, monkeypatch)
    from scripts import cli
    args = _survey_args(tmp_path, id="ghost")
    assert cli.cmd_survey(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"] == "RECORD_MISSING"


def test_manifest_has_survey(capsys):
    from scripts import cli
    args = type("A", (), {"json": True})()
    assert cli.cmd_manifest(args) == 0
    out = json.loads(capsys.readouterr().out)
    names = [c["name"] for c in out["data"]["commands"]]
    assert "survey" in names
    assert out["data"]["version"] == "3.5"


# ---------- v3.6: auto end-to-end + survey_state ----------

def _seed_task_json(tmp: Path):
    task_path = paths.survey_task_path("rec1", tmp)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(json.dumps({"task": "写综述", "taskName": "survey-rec1"}), encoding="utf-8")


def _runner_writes_good(prompt, ws, timeout=900):
    paths.survey_md_path("rec1", ws).parent.mkdir(parents=True, exist_ok=True)
    paths.survey_md_path("rec1", ws).write_text(GOOD_SURVEY_MD, encoding="utf-8")
    return {"ok": True, "stdout": "done", "stderr": ""}


def test_auto_write_survey_happy(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    _seed_task_json(tmp_path)
    from scripts.records import survey as SV
    SV.write_status("rec1", tmp_path, "awaiting_agent")

    result = SV.auto_write_survey("rec1", tmp_path, runner=_runner_writes_good)
    assert result["ok"] and result["written"] and result["published"]["revision"] == 1
    assert SV.read_status("rec1", tmp_path)["state"] == "done"
    surveys = json.loads((tmp_path / "site" / "data" / "surveys.json").read_text(encoding="utf-8"))
    assert surveys and surveys[0]["slug"] == "rec1"


def test_auto_write_survey_no_output(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    _seed_task_json(tmp_path)
    from scripts.records import survey as SV
    SV.write_status("rec1", tmp_path, "awaiting_agent")

    result = SV.auto_write_survey("rec1", tmp_path,
                                  runner=lambda p, ws, timeout=900: {"ok": True, "stdout": "", "stderr": "boom"})
    assert result["ok"] is False and result["reason"] == "no_survey_md"
    st = SV.read_status("rec1", tmp_path)
    assert st["state"] == "awaiting_agent" and "boom" in st.get("error", "")


def test_auto_write_survey_already_written_publishes(tmp_path, monkeypatch):
    """survey.md 已存在（写作成功但发布失败场景）→ 幂等直接发布，不再调用 runner。"""
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    from scripts.records import survey as SV
    paths.survey_md_path("rec1", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    paths.survey_md_path("rec1", tmp_path).write_text(GOOD_SURVEY_MD, encoding="utf-8")

    def _explode(*a, **kw):
        raise AssertionError("runner 不应被调用")

    result = SV.auto_write_survey("rec1", tmp_path, runner=_explode)
    assert result["ok"] and result["written"] is False
    assert SV.read_status("rec1", tmp_path)["state"] == "done"


def test_auto_execute_skips_collect_when_queued(tmp_path, monkeypatch):
    """状态 awaiting_agent（材料就绪）→ --auto 不重采集，直接写作发布。"""
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    _seed_task_json(tmp_path)
    from scripts.records import survey as SV
    SV.write_status("rec1", tmp_path, "awaiting_agent")
    monkeypatch.setattr(SV, "_find_writer", lambda: "claude")

    def _collect_explode(*a, **kw):
        raise AssertionError("awaiting_agent 状态不应重采集")

    monkeypatch.setattr(SV, "collect_survey", _collect_explode)
    result = SV.auto_execute_survey("rec1", tmp_path, runner=_runner_writes_good)
    assert result["ok"] and result["mode"] == "headless-claude"
    assert SV.read_status("rec1", tmp_path)["state"] == "done"


def test_auto_execute_no_writer_stays_queued(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    _seed_task_json(tmp_path)
    from scripts.records import survey as SV
    SV.write_status("rec1", tmp_path, "awaiting_agent")
    monkeypatch.setattr(SV, "_find_writer", lambda: "")
    result = SV.auto_execute_survey("rec1", tmp_path)
    assert result["ok"] is False and result["mode"] == "queued"
    assert SV.read_status("rec1", tmp_path)["state"] == "awaiting_agent"


def test_survey_state_in_entries_json(tmp_path, monkeypatch):
    """build_site：无 survey.md 但有 status.json → survey_state 出现在 entries.json。"""
    _patch_ws(tmp_path, monkeypatch)
    _seed_record(tmp_path)
    conftest.seed_entry(paths.db_path(tmp_path), "rec1", status="done")
    from scripts.records import survey as SV
    from scripts.site.build import build_site
    SV.write_status("rec1", tmp_path, "awaiting_agent")
    build_site(paths.db_path(tmp_path), tmp_path)
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    rec1 = [e for e in entries if e["id"] == "rec1"][0]
    assert rec1["has_survey"] is False
    assert rec1["survey_state"] == "awaiting_agent"
    # 写作中
    SV.write_status("rec1", tmp_path, "writing")
    build_site(paths.db_path(tmp_path), tmp_path)
    entries = json.loads((tmp_path / "site" / "data" / "entries.json").read_text(encoding="utf-8"))
    assert [e for e in entries if e["id"] == "rec1"][0]["survey_state"] == "writing"
