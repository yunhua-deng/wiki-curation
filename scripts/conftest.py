"""skills/wiki-curation/scripts/conftest.py — pytest-native shared fixtures."""
import json
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


@pytest.fixture
def tmp_workspace(tmp_path):
    """Return a temporary wiki workspace with required subdirectories."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "references").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def patch_workspace(tmp_workspace, monkeypatch):
    """Patch scripts.paths.get_workspace to return the temp workspace."""
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp_workspace)
    return tmp_workspace


def mock_run_cmd(responses):
    """Build a stand-in for lib.run_cmd.

    responses may be a single dict (returned every time) or a list popped per call.
    Each dict shape: {"ok": bool, "stdout": str, "stderr": str, "exit_code": int}.
    """
    if isinstance(responses, dict):
        responses = [responses]
    responses = list(responses)

    def _run_cmd(cmd, timeout=120, retries=1, backoff=2.0):
        if not responses:
            return {"ok": False, "stdout": "", "stderr": "no more mock responses", "exit_code": -1}
        return responses.pop(0)

    return _run_cmd


def write_fetch_results(dest_dir: Path, results: list[dict]):
    """Write a _fetch_results.json stub into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "_fetch_results.json").write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def seed_entry(db_path: Path, slug: str, source_input: str = "https://arxiv.org/abs/2605.26112",
               input_type: str = "url", source_type: str = "arxiv",
               topic_type: str = "paper", depth: str = "brief", status: str = "pending", events=None):
    """Insert a wiki entry plus optional audit events into db_path."""
    from scripts import wiki_index
    wiki_index.upsert_task(
        db_path, slug,
        source_input=source_input,
        source_prompt=source_input,
        input_type=input_type,
        source_type=source_type,
        topic_type=topic_type,
        depth=depth,
        status=status,
    )
    for action, detail in (events or []):
        wiki_index.record_event(db_path, slug, action, detail)


require_ci = pytest.mark.skipif(
    not os.environ.get("CI_RUN") and not os.environ.get("RUN_CI_TESTS"),
    reason="requires CI environment; set CI_RUN=1 to enable",
)
