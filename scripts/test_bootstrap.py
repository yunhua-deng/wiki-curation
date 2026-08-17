"""scripts/test_bootstrap.py — cli.py init bootstrap 契约测试。"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.bootstrap import init_workspace

SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "cli.py"

SKELETON_DIRS = ["artifacts", "data", "posts", "tracking", "entities", "failures", "docs"]


def test_init_creates_skeleton(tmp_path):
    ws = tmp_path / "wiki"
    result = init_workspace(ws)
    for d in SKELETON_DIRS:
        assert (ws / d).is_dir(), d
    assert (ws / "data" / "wiki.db").exists()
    assert (ws / "README.md").exists()
    assert (ws / "failures" / "TEMPLATE.md").exists()
    assert (ws / ".gitignore").exists()
    assert result["agents_snippet"]
    assert result["created"]
    assert result["db_path"] == str((ws / "data" / "wiki.db").resolve())


def test_init_db_schema_queryable(tmp_path):
    ws = tmp_path / "wiki"
    init_workspace(ws)
    conn = sqlite3.connect(str(ws / "data" / "wiki.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"entries", "entries_fts", "events", "links", "relations", "schema_version"} <= tables


def test_init_idempotent_no_overwrite(tmp_path):
    ws = tmp_path / "wiki"
    init_workspace(ws)
    readme = ws / "README.md"
    readme.write_text("USER EDIT", encoding="utf-8")
    result = init_workspace(ws)
    assert readme.read_text(encoding="utf-8") == "USER EDIT"
    assert str(readme.resolve()) in result["skipped"]


def test_init_cli_json_contract(tmp_path):
    ws = tmp_path / "wiki"
    r = subprocess.run(
        [sys.executable, str(CLI), "--json", "--workspace", str(ws), "init"],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
    payload = json.loads(r.stdout.decode("utf-8"))
    assert payload["ok"] is True
    for key in ["workspace", "db_path", "created", "skipped", "agents_snippet"]:
        assert key in payload["data"], key


def test_site_build_injects_cli_cmd(tmp_path):
    from scripts import conftest
    from scripts.site.build import build_site

    ws = tmp_path / "wiki"
    init_workspace(ws)
    conftest.seed_entry(ws / "data" / "wiki.db", "2026-08-13_test")
    out = build_site(ws / "data" / "wiki.db", ws, out_dir=tmp_path / "site_out")
    site_js = (Path(out) / "assets" / "site.js").read_text(encoding="utf-8")
    assert "__WIKI_CLI_CMD__" not in site_js
    cli_py = (SCRIPT_DIR / "cli.py").as_posix()
    assert f"python {cli_py}" in site_js
