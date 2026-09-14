"""scripts/test_entities.py — 实体只读查询（entities / aggregate）契约测试。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import conftest
from scripts.entities import (EntityError, aggregate_entity, entity_index,
                              find_entity, flatten_entities)
from scripts.records import links as L
from scripts.wiki_index import ensure_schema


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "data" / "wiki.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(p)
    return p


def _seed(db):
    conftest.seed_entry(db, "2026-08-01_aaaa", status="done")
    conftest.seed_entry(db, "2026-08-02_bbbb", status="done")
    L.set_entry_entities(db, "2026-08-01_aaaa",
                         {"company": ["Figure AI"], "author": ["Brett Adcock"],
                          "product": ["Helix"], "series": []})
    L.set_entry_entities(db, "2026-08-02_bbbb",
                         {"company": ["Figure AI"], "author": [], "product": [], "series": []})


def test_entity_index_and_find(db):
    _seed(db)
    idx = entity_index(db)
    assert idx["Figure AI"]["type"] == "company"
    assert sorted(idx["Figure AI"]["entries"]) == ["2026-08-01_aaaa", "2026-08-02_bbbb"]
    assert find_entity(db, "figure ai")["name"] == "Figure AI"  # 大小写不敏感
    assert find_entity(db, "No Such Entity") is None


def test_aggregate_entity(db):
    _seed(db)
    L.replace_links(db, "2026-08-01_aaaa", [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical"},
        {"url": "https://example.com/x", "kind": "other", "role": "related"},
    ])
    agg = aggregate_entity(db, "Figure AI")
    assert agg["slug"] == "figure-ai"
    assert agg["type"] == "company"
    assert "watched" not in agg
    assert {r["id"] for r in agg["records"]} == {"2026-08-01_aaaa", "2026-08-02_bbbb"}
    assert sum(t["count"] for t in agg["timeline"]) == 2
    co_names = [c["name"] for c in agg["co_entities"]]
    assert "Helix" in co_names and "Brett Adcock" in co_names
    assert agg["links"] == [{"url": "https://github.com/figure/helix", "kind": "github"}]


def test_aggregate_not_found_suggests(db):
    _seed(db)
    with pytest.raises(EntityError) as ei:
        aggregate_entity(db, "Figure A")  # difflib 相近
    assert ei.value.code == "ENTITY_NOT_FOUND"
    assert "Figure AI" in str(ei.value)


def test_flatten_entities():
    assert flatten_entities({"company": ["A"], "author": ["B"], "product": [], "series": []}) == ["A", "B"]
    assert flatten_entities(None) == []


# ---------- cli.py entities 子命令契约测试 ----------

SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "cli.py"


def _cli(*args):
    r = subprocess.run([sys.executable, str(CLI), "--json", *args], capture_output=True)
    return r.returncode, json.loads(r.stdout.decode("utf-8"))


def test_cli_entities_list_and_name(tmp_path):
    ws = tmp_path / "wiki"
    rc, out = _cli("--workspace", str(ws), "init")
    assert rc == 0 and out["ok"]
    conftest.seed_entry(ws / "data" / "wiki.db", "2026-08-01_aaaa", status="done")
    L.set_entry_entities(ws / "data" / "wiki.db", "2026-08-01_aaaa",
                         {"company": ["Figure AI"], "author": [], "product": [], "series": []})

    rc, out = _cli("--workspace", str(ws), "entities", "--list")
    assert rc == 0 and out["ok"] and out["data"]["count"] == 1
    assert out["data"]["entities"][0]["name"] == "Figure AI"
    assert "watched" not in out["data"]["entities"][0]

    # 无参默认等同 --list
    rc, out = _cli("--workspace", str(ws), "entities")
    assert rc == 0 and [e["name"] for e in out["data"]["entities"]] == ["Figure AI"]

    rc, out = _cli("--workspace", str(ws), "entities", "--name", "figure ai")
    assert rc == 0 and out["data"]["slug"] == "figure-ai" and out["data"]["type"] == "company"


def test_cli_entities_not_found(tmp_path):
    ws = tmp_path / "wiki"
    _cli("--workspace", str(ws), "init")
    rc, out = _cli("--workspace", str(ws), "entities", "--name", "Nobody")
    assert rc == 1 and out["ok"] is False and out["error"] == "ENTITY_NOT_FOUND"


def test_cli_entities_removed_flags_rejected(tmp_path):
    """实体 watch / 摘要子命令已移除，argparse 应直接拒绝。"""
    ws = tmp_path / "wiki"
    _cli("--workspace", str(ws), "init")
    for flag in (["--watch", "Figure AI"], ["--unwatch", "Figure AI"], ["--watched"],
                 ["--summary", "--name", "Figure AI"]):
        r = subprocess.run([sys.executable, str(CLI), "--json", "--workspace", str(ws),
                            "entities", *flag], capture_output=True)
        assert r.returncode != 0, flag
