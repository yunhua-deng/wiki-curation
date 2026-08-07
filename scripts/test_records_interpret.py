#!/usr/bin/env python3
"""test_records_interpret.py — record 提取任务生成契约测试。"""
import json
from pathlib import Path

from scripts import conftest
from scripts import paths
from scripts.records.interpret_record import generate_record_task


def _seed_raw(ws, slug):
    raw = paths.raw_dir(slug, ws)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "arxiv_abstract.html").write_text("<html>abstract</html>", encoding="utf-8")
    (raw / "paper.pdf").write_bytes(b"%PDF-1.4 fake" * 3000)


def test_record_task_envelope(patch_workspace):
    slug = "2026-07-21_9999"
    db = paths.db_path(patch_workspace)
    conftest.seed_entry(db, slug)
    _seed_raw(patch_workspace, slug)

    result = generate_record_task(slug, "arxiv")

    assert result["task_mode"] == "record"
    assert result["mode"] == "run"  # harness spawn 语义，保持 "run"
    assert result["taskName"] == f"record-{slug}"
    assert result["slug"] == slug
    assert result["output_path"].endswith("record.json")
    assert result["depth"] is None
    assert "model" not in result  # skill 不感知模型，跟随调用方
    # envelope 与文章任务对齐的共有字段
    for key in ("task", "cleanup", "context", "wiki_type", "type_label", "raw_files"):
        assert key in result, f"missing envelope key: {key}"


def test_record_task_prompt_content(patch_workspace):
    slug = "2026-07-21_9998"
    db = paths.db_path(patch_workspace)
    conftest.seed_entry(db, slug)
    _seed_raw(patch_workspace, slug)

    task = generate_record_task(slug, "arxiv")["task"]

    # 双通道 URL 嗅探指令
    assert "explicit" in task and "inferred" in task
    # 输出契约
    assert "record.json" in task
    # 实体归一化参照
    assert "entity_aliases.yaml" in task
    # 禁止编造 / 禁止 git
    assert "编造" in task
    assert "git" in task.lower()
    # 合法枚举写进 prompt
    assert "canonical" in task and "related" in task


def test_generate_task_cli_record_mode(patch_workspace):
    """generate_task.py --mode record 的 JSON 输出契约。"""
    import os
    import sys
    from scripts.lib import run_cmd
    slug = "2026-07-21_9997"
    db = paths.db_path(patch_workspace)
    conftest.seed_entry(db, slug)
    _seed_raw(patch_workspace, slug)

    script = Path(__file__).parent / "exec" / "generate_task.py"
    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(patch_workspace)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    r = run_cmd(
        [sys.executable, str(script), "--slug", slug, "--source-type", "arxiv",
         "--mode", "record", "--json"],
        timeout=60, env=env,
    )
    assert r["ok"], f"stderr: {r.get('stderr', '')[:500]}"
    data = json.loads(r["stdout"])
    assert data["task_mode"] == "record"
    assert data["mode"] == "run"
    assert data["taskName"] == f"record-{slug}"
