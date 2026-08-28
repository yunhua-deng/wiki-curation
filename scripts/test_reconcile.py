"""test_reconcile.py — reconcile 对账命令契约测试。"""
import json

from scripts import paths
from scripts.conftest import seed_entry
from scripts.reconcile import reconcile


def _write_record(ws, slug, content=b'{"id": "x"}'):
    rp = paths.record_path(slug, ws)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_bytes(content)


def test_done_when_record_exists(patch_workspace):
    ws = patch_workspace
    seed_entry(paths.db_path(ws), "2026-08-28_aaaa", status="running")
    _write_record(ws, "2026-08-28_aaaa")

    data = reconcile(["2026-08-28_aaaa"], ws)

    assert data["checked"] == 1
    assert data["all_done"] is True
    assert [d["id"] for d in data["done"]] == ["2026-08-28_aaaa"]
    assert data["done"][0]["record_bytes"] > 0
    assert data["missing"] == []
    # running 且 record 已存在 → 提示 publish
    assert data["next_cmds"] == [
        "python scripts/cli.py --json publish --id 2026-08-28_aaaa"]


def test_missing_when_record_absent(patch_workspace):
    ws = patch_workspace
    seed_entry(paths.db_path(ws), "2026-08-28_bbbb", status="running")

    data = reconcile(["2026-08-28_bbbb"], ws)

    assert data["all_done"] is False
    assert [m["id"] for m in data["missing"]] == ["2026-08-28_bbbb"]
    assert data["missing"][0]["status"] == "running"
    assert data["next_cmds"] == []


def test_empty_record_counts_as_missing(patch_workspace):
    ws = patch_workspace
    seed_entry(paths.db_path(ws), "2026-08-28_cccc", status="running")
    _write_record(ws, "2026-08-28_cccc", content=b"")

    data = reconcile(["2026-08-28_cccc"], ws)
    assert data["all_done"] is False


def test_already_published_needs_no_next_cmd(patch_workspace):
    ws = patch_workspace
    seed_entry(paths.db_path(ws), "2026-08-28_dddd", status="done")
    _write_record(ws, "2026-08-28_dddd")

    data = reconcile(["2026-08-28_dddd"], ws)
    assert data["all_done"] is True
    assert data["next_cmds"] == []


def test_default_scope_is_running_entries(patch_workspace):
    ws = patch_workspace
    db = paths.db_path(ws)
    seed_entry(db, "2026-08-28_eee1", status="running")
    seed_entry(db, "2026-08-28_eee2", status="pending")
    seed_entry(db, "2026-08-28_eee3", status="running")
    _write_record(ws, "2026-08-28_eee3")

    data = reconcile(None, ws)

    assert data["checked"] == 2  # pending 条目不在对账范围
    assert [d["id"] for d in data["done"]] == ["2026-08-28_eee3"]
    assert [m["id"] for m in data["missing"]] == ["2026-08-28_eee1"]


def test_unknown_slug_reported_with_null_status(patch_workspace):
    ws = patch_workspace
    data = reconcile(["2099-01-01_zzzz"], ws)
    assert data["missing"][0]["status"] is None


def test_cli_json_contract(patch_workspace, capsys):
    from scripts.reconcile import main
    seed_entry(paths.db_path(patch_workspace), "2026-08-28_ffff", status="running")
    _write_record(patch_workspace, "2026-08-28_ffff")

    rc = main(["--json", "--id", "2026-08-28_ffff"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["ok"] is True
    assert out["data"]["all_done"] is True
