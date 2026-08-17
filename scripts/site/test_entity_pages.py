"""scripts/site/test_entity_pages.py — 实体页构建契约测试。"""
import json
from pathlib import Path

from scripts import conftest
from scripts.records import links as L
from scripts.site.build import build_site
from scripts.wiki_index import ensure_schema


def _ws(tmp_path):
    ws = tmp_path / "wiki"
    (ws / "data").mkdir(parents=True)
    (ws / "artifacts").mkdir(parents=True)
    return ws


def _seed(db):
    conftest.seed_entry(db, "2026-08-01_aaaa", status="done")
    L.set_entry_entities(db, "2026-08-01_aaaa",
                         {"company": ["Figure AI"], "author": [], "product": ["Helix"], "series": []})
    L.replace_links(db, "2026-08-01_aaaa", [
        {"url": "https://github.com/figure/helix", "kind": "github", "role": "canonical"},
    ])


def test_build_site_writes_entity_pages(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    pages = json.loads((out / "data" / "entity_pages.json").read_text(encoding="utf-8"))
    assert "figure-ai" in pages
    p = pages["figure-ai"]
    assert p["name"] == "Figure AI" and p["type"] == "company"
    assert p["record_count"] == 1
    assert p["records"][0]["id"] == "2026-08-01_aaaa"
    assert p["links"] == [{"url": "https://github.com/figure/helix", "kind": "github"}]
    assert p["summary"] == ""
    assert p["watched"] is False
    # 冻结语义：posts/tracking 数据文件照常生成
    assert (out / "data" / "posts.json").exists()
    assert (out / "data" / "tracking.json").exists()


def test_entity_page_embeds_done_summary(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    edir = ws / "entities" / "figure-ai"
    edir.mkdir(parents=True)
    (edir / "summary.md").write_text("# Figure AI\n\n人形机器人公司，见 2026-08-01_aaaa。", encoding="utf-8")
    (edir / "meta.json").write_text(json.dumps({"status": "done", "revision": 1}), encoding="utf-8")
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    pages = json.loads((out / "data" / "entity_pages.json").read_text(encoding="utf-8"))
    assert "人形机器人公司" in pages["figure-ai"]["summary"]


def test_entity_page_skips_failed_summary(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    edir = ws / "entities" / "figure-ai"
    edir.mkdir(parents=True)
    (edir / "summary.md").write_text("# Figure AI\n\n旧内容", encoding="utf-8")
    (edir / "meta.json").write_text(json.dumps({"status": "failed", "error": "x"}), encoding="utf-8")
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    pages = json.loads((out / "data" / "entity_pages.json").read_text(encoding="utf-8"))
    assert pages["figure-ai"]["summary"] == ""


def test_entity_page_tracking_crosslink(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    tdir = ws / "tracking" / "figure-ai"
    tdir.mkdir(parents=True)
    (tdir / "topic.json").write_text(json.dumps(
        {"slug": "figure-ai", "name": "Figure AI", "kind": "company", "status": "active"}),
        encoding="utf-8")
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    pages = json.loads((out / "data" / "entity_pages.json").read_text(encoding="utf-8"))
    assert pages["figure-ai"]["tracking_slug"] == "figure-ai"
