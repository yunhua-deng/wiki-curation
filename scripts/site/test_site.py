#!/usr/bin/env python3
"""
scripts/site/test_site.py — wiki 站点生成器测试。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.site.build import build_site, _to_list, _article_url, _raw_url
from scripts.site import serve as serve_mod


@pytest.fixture
def sample_workspace(tmp_path):
    """构造一个最小 wiki 工作区。"""
    wiki_dir = tmp_path / "wiki"
    data_dir = wiki_dir / "data"
    data_dir.mkdir(parents=True)

    db_path = data_dir / "wiki.db"
    from scripts.wiki_index.schema import ensure_schema
    ensure_schema(db_path)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    rows = [
        ("2026-07-01_alpha", "2026-07-01", "1.0", "arXiv", "paper",
         "Alpha Paper", "Overview of alpha with VLA.", "tag-a,tag-b", "", "2026-07-01_alpha_brief.md",
         "brief", "https://arxiv.org/abs/alpha", "url", "arxiv", "done"),
        ("2026-07-01_beta", "2026-07-01", "1.0", "GitHub", "project",
         "Beta Project", "Overview of beta.", "tag-b,tag-c", "", "2026-07-01_beta_brief.md",
         "brief", "https://github.com/beta", "url", "github", "done"),
        ("2026-06-01_gamma", "2026-06-01", "1.0", "WeChat", "article",
         "Gamma Article", "Overview of gamma.", "tag-c", "", "2026-06-01_gamma_brief.md",
         "brief", "https://mp.weixin.qq.com/gamma", "url", "weixin", "done"),
    ]
    conn.executemany('''
        INSERT INTO entries (id, date, ver, sources, topic_type, title, overview, tags, raw, file,
                             depth, source_input, input_type, source_type, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)
    conn.commit()
    conn.close()

    # 创建 markdown 文件，beta 引用 alpha
    (wiki_dir / "artifacts" / "2026-07-01_alpha").mkdir(parents=True)
    (wiki_dir / "artifacts" / "2026-07-01_alpha" / "2026-07-01_alpha_brief.md").write_text(
        "# Alpha\n\nSome content.\n", encoding="utf-8"
    )
    (wiki_dir / "artifacts" / "2026-07-01_beta").mkdir(parents=True)
    (wiki_dir / "artifacts" / "2026-07-01_beta" / "2026-07-01_beta_brief.md").write_text(
        "# Beta\n\nSee also [Alpha](../artifacts/2026-07-01_alpha/2026-07-01_alpha_brief.md).\n",
        encoding="utf-8"
    )
    (wiki_dir / "artifacts" / "2026-06-01_gamma").mkdir(parents=True)
    (wiki_dir / "artifacts" / "2026-06-01_gamma" / "2026-06-01_gamma_brief.md").write_text(
        "# Gamma\n\nAlone.\n", encoding="utf-8"
    )

    return wiki_dir, db_path


def test_to_list():
    assert _to_list("a,b") == ["a", "b"]
    assert _to_list(["a", "b"]) == ["a", "b"]
    assert _to_list(None) == []
    assert _to_list("—") == []


def test_article_and_raw_url():
    e = {"id": "2026-07-01_x", "depth": "brief", "file": "x_brief.md", "raw": "raw/url/"}
    assert _article_url(e) == "artifacts/2026-07-01_x/x_brief.md"
    assert _raw_url(e) == "raw/url/"


def test_build_site_outputs(sample_workspace):
    wiki_dir, db_path = sample_workspace
    out_dir = build_site(db_path, wiki_dir)

    assert (out_dir / "index.html").exists()
    assert (out_dir / "assets" / "site.css").exists()
    assert (out_dir / "assets" / "site.js").exists()

    # 站点已简化为单页结构：旧版多页不再生成，遗留文件也会被清理
    for legacy_page in ("browse.html", "graph.html", "clusters.html", "timeline.html"):
        assert not (out_dir / legacy_page).exists()

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="search"' in index_html
    assert 'id="table-container"' in index_html

    entries = json.loads((out_dir / "data" / "entries.json").read_text(encoding="utf-8"))
    assert len(entries) == 3
    assert entries[0]["id"] == "2026-07-01_beta"  # v3.3: id DESC sort
    assert entries[0]["tags"] == ["tag-b", "tag-c"]

    tags = json.loads((out_dir / "data" / "tags.json").read_text(encoding="utf-8"))
    assert "tag-b" in tags
    assert len(tags["tag-b"]) == 2

    sources = json.loads((out_dir / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources["url"]["arxiv"] == ["2026-07-01_alpha"]
    assert sources["url"]["github"] == ["2026-07-01_beta"]

    # graph.json 已停止生成（graph 链整条移除）；search_index.json 已停止生成
    assert not (out_dir / "data" / "graph.json").exists()
    assert not (out_dir / "data" / "search_index.json").exists()

    # entities.json 是 Phase 1 新增产物
    assert (out_dir / "data" / "entities.json").exists()
    entities = json.loads((out_dir / "data" / "entities.json").read_text(encoding="utf-8"))
    assert "by_entry" in entities
    assert "by_type" in entities
    assert "2026-07-01_alpha" in entities["by_entry"]


    # themes.json 已停止生成；timeline.json 仍在生成；旧版页面清理照常
    # 旧版多页站点遗留的页面会在重建时被清理
    stale = out_dir / "browse.html"
    stale.write_text("stale", encoding="utf-8")
    build_site(db_path, wiki_dir)
    assert not stale.exists()


def test_build_site_record_fields(sample_workspace):
    """v3.0：record 条目导出 has_record/links。"""
    import json
    wiki_dir, db_path = sample_workspace

    # 造一个 record 条目：record.json + links 表 + relations 边
    from scripts.records.schema import save_record
    from scripts.records import links as L
    record = {
        "version": "3.0", "id": "2026-07-01_alpha", "title": "Alpha Paper",
        "date": "2026-07-01", "topic_type": "paper", "tldr": "Alpha 一句话。",
        "tags": ["tag-a", "tag-b"],
        "entities": {"company": [], "author": [], "product": [], "series": []},
        "links": [{"url": "https://arxiv.org/abs/alpha", "kind": "arxiv",
                   "role": "canonical", "origin": "explicit", "fetched": 1, "verified": None}],
        "source": {"input_type": "url", "source_type": "arxiv",
                   "direct_source": "https://arxiv.org/abs/alpha",
                   "original_source": "https://arxiv.org/abs/alpha"},
    }
    save_record("2026-07-01_alpha", wiki_dir, record)
    L.replace_links(db_path, "2026-07-01_alpha", record["links"])
    L.replace_links(db_path, "2026-07-01_beta", [{"url": "https://arxiv.org/abs/alpha", "kind": "arxiv"}])
    L.replace_relations(db_path, "2026-07-01_alpha", [
        {"a": "2026-07-01_alpha", "b": "2026-07-01_beta", "kind": "shared_link",
         "score": 40, "evidence": {"url": "https://arxiv.org/abs/alpha"}},
    ])

    out_dir = build_site(db_path, wiki_dir)

    entries = json.loads((out_dir / "data" / "entries.json").read_text(encoding="utf-8"))
    alpha = [e for e in entries if e["id"] == "2026-07-01_alpha"][0]
    assert alpha["has_record"] is True
    assert len(alpha["links"]) == 1
    assert alpha["links"][0]["kind"] == "arxiv"
    beta = [e for e in entries if e["id"] == "2026-07-01_beta"][0]
    assert beta["has_record"] is False
    assert len(beta["links"]) == 1  # links 表对无 record 条目同样导出


def test_serve_pid_file_lifecycle(sample_workspace, tmp_path):
    """验证 site --serve --pid-file 与 site --stop 生命周期。"""
    wiki_dir, db_path = sample_workspace
    pid_file = tmp_path / "site-serve.pid"
    port = 65432

    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(wiki_dir)
    script_dir = Path(__file__).resolve().parent.parent
    cli = script_dir / "cli.py"

    # 确保端口空闲
    if serve_mod._port_in_use(port):
        pytest.skip(f"port {port} already in use")

    proc = subprocess.Popen(
        [sys.executable, str(cli), "--workspace", str(wiki_dir), "site", "--serve", "--port", str(port), "--pid-file", str(pid_file)],
        cwd=str(script_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # 等待 PID 文件写入
        deadline = time.time() + 5
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.1)
        assert pid_file.exists(), "PID file was not created"
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert pid == proc.pid

        # 服务确实在监听
        assert serve_mod._port_in_use(port)

        # 使用 --stop 终止服务
        stop = subprocess.run(
            [sys.executable, str(cli), "--workspace", str(wiki_dir), "site", "--stop", "--pid-file", str(pid_file)],
            cwd=str(script_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert stop.returncode == 0, f"stop failed: {stop.stdout}\n{stop.stderr}"

        # PID 文件应被删除，原进程应退出
        proc.wait(timeout=5)
        assert not pid_file.exists()
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        pid_file.unlink(missing_ok=True)


def test_render_pages_no_survey_html(tmp_path):
    from scripts.site.templates import render_pages
    out = tmp_path / "site"
    render_pages([], {}, {}, out)
    assert not (out / "survey.html").exists()
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert "site.js" in index_html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_build_related_map_includes_titles(tmp_path):
    from scripts.wiki_index.schema import ensure_schema
    from scripts.records import links as L
    from scripts.site.build import _build_related_map
    db = tmp_path / "data" / "wiki.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(db)
    from scripts import conftest
    conftest.seed_entry(db, "a1", status="done")
    conftest.seed_entry(db, "a2", status="done")
    L.replace_links(db, "a1", [{"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv"}])
    L.replace_links(db, "a2", [{"url": "https://arxiv.org/abs/2501.0001", "kind": "arxiv"}])
    from scripts.records import relations as REL
    REL.rewire_relations(db, "a1")
    entries = [{"id": "a1", "title": "Alpha"}, {"id": "a2", "title": "Beta Paper"}]
    m = _build_related_map(db, entries)
    assert m["a1"][0]["id"] == "a2" and m["a1"][0]["title"] == "Beta Paper"
    # 无 entries 时 title 为空字符串（向后兼容）
    m2 = _build_related_map(db)
    assert m2["a1"][0]["title"] == ""
