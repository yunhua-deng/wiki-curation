"""skills/wiki-curation/scripts/conftest.py — pytest-native shared fixtures."""
import os
from pathlib import Path

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
