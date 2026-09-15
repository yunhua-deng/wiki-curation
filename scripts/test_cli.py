"""test_cli.py — 测试 skills/wiki-curation/scripts/cli.py 统一入口。

所有测试都在临时 wiki workspace 中运行，不污染项目 wiki/。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
CLI = [sys.executable, str(SCRIPT_DIR / "cli.py")]


@pytest.fixture
def cli_workspace(tmp_path):
    """为 CLI 测试准备隔离的临时 workspace，复制默认 references 和 assets。"""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_ROOT / "references", tmp_path / "references", dirs_exist_ok=True)
    shutil.copytree(SKILL_ROOT / "assets", tmp_path / "assets", dirs_exist_ok=True)
    return tmp_path


def _run(args: list, workspace: Path, timeout: int = 60) -> dict:
    """Run cli.py with args in an isolated workspace and return parsed JSON response."""
    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(workspace)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        CLI + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(SKILL_ROOT),
        env=env,
    )
    if r.returncode != 0 and not r.stdout.strip() and not r.stderr.strip():
        raise AssertionError(f"cli.py exited {r.returncode}: stdout empty")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"cli.py stdout is not JSON: {r.stdout[:500]}") from e


def test_manifest_json(cli_workspace):
    resp = _run(["--json", "manifest"], cli_workspace)
    assert resp.get("ok"), resp
    data = resp.get("data", {})
    assert "version" in data
    assert "commands" in data
    names = {c["name"] for c in data["commands"]}
    assert {"run", "doctor", "stats", "ingest"}.issubset(names)
    # 已废除的命令桩不再出现在清单里
    assert not {"article", "interpret", "verify-output"} & names
    # entities 收敛为只读（--list / --name）
    ent = [c for c in data["commands"] if c["name"] == "entities"][0]
    assert ent["args"] == ["--list", "--name"]


def test_ingest_one_shot_local_file(cli_workspace, tmp_path):
    """ingest = add → pop --limit 1 → run，一次调用输出三步合并结果。"""
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nlocal material\n", encoding="utf-8")
    resp = _run(["--json", "ingest", "--input", str(src), "--input-type", "local"],
                cli_workspace, timeout=180)

    assert resp.get("ok"), resp
    data = resp["data"]
    slug = data["id"]
    assert data["added"]["id"] == slug and data["added"]["status"] == "pending"
    assert "recall" in data["added"]  # add 的自动召回原样带出
    assert [e["id"] for e in data["popped"]] == [slug]
    assert data["run"]["slug"] == slug and data["run"]["task_mode"] == "record"
    assert data["run"]["output_path"].endswith("record.json")

def test_stats_json(cli_workspace):
    resp = _run(["--json", "stats"], cli_workspace)
    assert resp.get("ok"), resp
    data = resp.get("data", {})
    assert "total" in data
    assert "status_counts" in data


def test_classify_url(cli_workspace):
    resp = _run(["--json", "classify", "--input", "https://arxiv.org/abs/2501.12345"], cli_workspace)
    assert resp.get("ok"), resp
    data = resp.get("data", {})
    assert data.get("status") == "ok"
    results = data.get("results", [])
    assert any(r.get("subtype") == "arxiv_paper" for r in results), results


def test_classify_empty(cli_workspace):
    resp = _run(["--json", "classify", "--input", ""], cli_workspace)
    assert resp.get("ok"), resp
    assert resp["data"].get("status") == "unclassifiable"


def test_doctor_quick_json(cli_workspace):
    resp = _run(["--json", "doctor", "--quick"], cli_workspace, timeout=120)
    assert resp.get("ok"), resp
    data = resp.get("data", {})
    assert "grade" in data
    assert "checks" in data


def test_doctor_fix_plan_json(cli_workspace):
    resp = _run(["--json", "doctor", "--quick", "--fix-plan"], cli_workspace, timeout=120)
    assert resp.get("ok"), resp
    data = resp.get("data", {})
    assert "fix_plan" in data
    for action in data["fix_plan"]:
        assert "type" in action
        assert "risk" in action
        assert "command" in action


def test_removed_stub_commands_rejected(cli_workspace):
    """article / interpret / verify-output 三个已废除命令桩已移除，argparse 应拒绝。"""
    for cmd in (["article", "--id", "x"], ["interpret", "--slug", "x"],
                ["verify-output", "--file", "x"]):
        r = subprocess.run(CLI + ["--json"] + cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(SKILL_ROOT),
                           env={**os.environ, "WIKI_WORKSPACE": str(cli_workspace)})
        assert r.returncode != 0, cmd
        assert "invalid choice" in (r.stderr or "")


def test_site_workspace_directory_resolution(cli_workspace):
    """cmd_site 必须把 --workspace 当作目录解析，而不是脚本路径。"""
    import sqlite3
    from unittest.mock import patch

    import scripts.cli as cli
    import scripts.paths as paths

    # 准备最小 wiki.db 让 build_site 能打开（即使被 mock，路径仍会被计算）
    db = paths.db_path(cli_workspace)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        "id TEXT PRIMARY KEY, title TEXT, overview TEXT, "
        "input_type TEXT, source_type TEXT, topic_type TEXT, "
        "status TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()

    class Args:
        workspace = str(cli_workspace)
        export = False
        serve = False
        json = True
        stop = False
        pid_file = None
        port = 8123
        open = False
        quiet = False

    with patch.object(cli, "build_site") as mock_build:
        mock_build.return_value = cli_workspace / "site"
        rc = cli.cmd_site(Args())

    assert rc == 0
    args, kwargs = mock_build.call_args
    db_arg, ws_arg = args
    assert ws_arg == cli_workspace
    assert db_arg == db


def test_run_and_collect_disable_wrapper_retry(cli_workspace):
    """有副作用的确定性步骤不得被包装层盲目重试。

    非零退出码是 run/collect 的正常失败信号（如 MATERIALS_MISSING / COLLECT_FAILED），
    run_cmd 默认 retries=1 会把整条流水线重跑一遍：重复抓取，且可能在 raw/ 下多出
    append_N，或让 2×180s 的超时叠加。这两个命令必须传 retries=0。
    """
    from unittest.mock import patch
    from scripts import cli

    class RunArgs:
        id = "2026-01-01_0001"
        mode = None
        depth = None
        append_to = None
        max_depth = None
        force_collect = False
        accept_manual = False
        json = True
        quiet = True
        workspace = None

    class CollectArgs:
        slug = "2026-01-01_0001"
        input_type = "url"
        source_type = "arxiv"
        input = "https://arxiv.org/abs/2101.00027"
        max_depth = None
        dest_subdir = None
        json = True
        quiet = True
        workspace = None

    calls = []

    def fake_run_cmd(cmd, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "stdout": '{"ok": true, "data": {}}', "stderr": "", "exit_code": 0}

    with patch.object(cli, "run_cmd", fake_run_cmd):
        cli.cmd_run(RunArgs())
        cli.cmd_collect(CollectArgs())

    assert len(calls) == 2
    assert [c.get("retries") for c in calls] == [0, 0]
