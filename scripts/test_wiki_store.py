#!/usr/bin/env python3
"""test_wiki_store.py — wiki_index store 的 FTS 搜索契约测试（CJK 子串召回）。"""
import json
import sqlite3

import pytest

from scripts.wiki_index.schema import ensure_schema
from scripts.wiki_index import store


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "wiki.db"
    ensure_schema(path)
    return path


def test_search_cjk_substring_roundtrip(db):
    """CJK 子串召回：嵌在长词里的中文词也能命中（字符级短语匹配）。"""
    store.upsert_task(db, "e1", title="具身智能机器人抓取",
                      overview="Figure 的人形机器人", tags="robotics", status="done")
    for q in ('智能', '机器人', '具身智能'):
        ids = [e["id"] for e in store.search(db, q)]
        assert "e1" in ids, f"query {q!r} should find e1, got {ids}"


def test_search_latin_still_works(db):
    """Latin 查询不发生回归。"""
    store.upsert_task(db, "e1", title="Helix VLA paper",
                      overview="Vision-Language-Action for humanoid", status="done")
    store.upsert_task(db, "e2", title="具身智能机器人", overview="中文条目", status="done")
    ids = [e["id"] for e in store.search(db, "Helix")]
    assert ids == ["e1"]


def test_search_no_match(db):
    store.upsert_task(db, "e1", title="具身智能机器人", status="done")
    assert store.search(db, "完全无关的zzzqqq") == []


def test_search_long_cjk_query_without_spaces(db):
    """无空格中文长查询（>= BIGRAM_MIN_CJK 字）：整串短语只命中连续出现的条目，
    二字组回退后还能召回只含部分词的条目（e2 修复前召回为空）。"""
    store.upsert_task(db, "e1", title="机器人抓取策略综述",
                      overview="具身智能抓取任务的方法梳理", tags="robotics", status="done")
    store.upsert_task(db, "e2", title="具身智能机器人抓取与规划策略综述",
                      overview="抓取与策略的联合学习", tags="robotics", status="done")
    assert {e["id"] for e in store.search(db, "机器人抓取策略")} == {"e1", "e2"}
    assert {e["id"] for e in store.search(db, "机器人")} == {"e1", "e2"}   # 短查询不回归
    assert {e["id"] for e in store.search(db, "robotics")} == {"e1", "e2"}  # 拉丁查询不回归


def test_search_multi_term_keeps_and_semantics(db):
    """多词查询是 AND 语义；其中一个词展开成二字组 OR 组后表达式仍须可解析。"""
    store.upsert_task(db, "e1", title="机器人抓取策略综述",
                      overview="VLA 抓取任务", tags="VLA", status="done")
    store.upsert_task(db, "e2", title="机器人抓取策略综述",
                      overview="仅中文条目", tags="robotics", status="done")
    assert {e["id"] for e in store.search(db, "VLA 机器人抓取策略")} == {"e1"}


def test_reinsert_same_id_leaves_no_fts_orphan(db):
    """同一 id 重复写入（OR REPLACE 改 rowid）不得留下孤儿 fts 行。"""
    store.upsert_task(db, "e1", title="第一次标题", overview="first", tags="t", status="done")
    store.upsert_task(db, "e1", title="第二次标题", overview="second", tags="t", status="done")

    conn = sqlite3.connect(str(db))
    n_entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM entries_fts WHERE rowid NOT IN (SELECT rowid FROM entries)"
    ).fetchone()[0]
    conn.close()

    assert (n_entries, n_fts, orphans) == (1, 1, 0)
    assert [e["id"] for e in store.search(db, "第二次标题")] == ["e1"]


def test_v10_migration_cleans_existing_fts_orphans(tmp_path):
    """v10 迁移清除历史孤儿 fts 行，且版本只记录一次。"""
    path = tmp_path / "wiki.db"
    ensure_schema(path)
    store.upsert_task(path, "e1", title="机器人抓取", status="done")

    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO entries_fts (rowid, id, search_text) VALUES (999999, 'ghost', 'ghost')")
    conn.execute("DELETE FROM schema_version WHERE version = 'v10_fts_orphans'")
    conn.commit()
    conn.close()

    ensure_schema(path)

    conn = sqlite3.connect(str(path))
    ghosts = conn.execute("SELECT COUNT(*) FROM entries_fts WHERE id = 'ghost'").fetchone()[0]
    recorded = conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 'v10_fts_orphans'"
    ).fetchone()[0]
    conn.close()
    assert ghosts == 0
    assert recorded == 1


def test_v11_migration_normalizes_depth_placeholders(tmp_path):
    """v11 迁移把 depth 的 '—' / 空串 / NULL 归一为 'brief'，保留 'deep'。"""
    path = tmp_path / "wiki.db"
    ensure_schema(path)
    store.upsert_task(path, "e1", title="a", status="done")

    conn = sqlite3.connect(str(path))
    conn.executemany(
        "INSERT INTO entries (id, status, depth) VALUES (?, 'done', ?)",
        [("e_dash", "—"), ("e_empty", ""), ("e_null", None), ("e_deep", "deep")],
    )
    conn.execute("DELETE FROM schema_version WHERE version = 'v11_depth_placeholders'")
    conn.commit()
    conn.close()

    ensure_schema(path)

    # 迁移之后新建的行（upsert_task 不传 depth）也必须被收掉：归一化每次 ensure_schema 都跑
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO entries (id, status) VALUES ('e_late', 'done')")
    conn.commit()
    conn.close()
    ensure_schema(path)

    conn = sqlite3.connect(str(path))
    depths = dict(conn.execute("SELECT id, depth FROM entries WHERE id LIKE 'e_%'"))
    recorded = conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 'v11_depth_placeholders'"
    ).fetchone()[0]
    conn.close()
    assert depths["e_dash"] == "brief"
    assert depths["e_empty"] == "brief"
    assert depths["e_null"] == "brief"
    assert depths["e_late"] == "brief"     # 迁移之后新建的行同样归一
    assert depths["e_deep"] == "deep"      # legacy 文章管线遗留，保留含义
    assert recorded == 1


def test_rebuild_index_restores_entries_links_and_relations(tmp_path):
    """`sync --rebuild` 必须真的从 artifacts/*/record.json 重建索引（此前是空实现）。"""
    ws = tmp_path / "wiki"
    (ws / "artifacts").mkdir(parents=True)
    db = ws / "data" / "wiki.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(db)

    def write(slug, title, links, tags):
        d = ws / "artifacts" / slug
        d.mkdir(parents=True)
        (d / "record.json").write_text(json.dumps({
            "version": "3.0", "id": slug, "title": title,
            "date": "2026-09-15", "topic_type": "paper",
            "tldr": f"{title} tldr", "summary": "x" * 50, "tags": tags,
            "entities": {"company": [], "author": [], "product": [], "series": []},
            "links": links,
            "source": {"input_type": "url", "source_type": "arxiv_paper",
                       "direct_source": "https://arxiv.org/abs/2501.00001",
                       "original_source": "https://arxiv.org/abs/2501.00001"},
        }, ensure_ascii=False), encoding="utf-8")

    shared = "https://github.com/acme/repo"
    write("2026-09-15_0001", "机器人抓取策略", [{"url": shared, "kind": "github", "role": "canonical", "origin": "explicit"}], ["抓取"])
    write("2026-09-15_0002", "世界模型综述", [{"url": shared, "kind": "github", "role": "related", "origin": "explicit"}], ["世界模型"])
    # 只有 raw/ 没有 record.json 的目录必须被忽略
    (ws / "artifacts" / "2026-09-15_9999" / "raw").mkdir(parents=True)

    count, fts_count = store.rebuild_index(db, ws)
    assert count == 2 and fts_count == 2

    conn = sqlite3.connect(str(db))
    ids = {r[0] for r in conn.execute("SELECT id FROM entries WHERE status='done'")}
    assert ids == {"2026-09-15_0001", "2026-09-15_0002"}
    links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    fts = conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
    conn.close()
    assert links == 2                      # 两条链接都入库
    assert relations >= 1                  # 同 URL → same_url 结构边
    assert fts == 2
    # 检索引擎也要能用（FTS 真的写了内容）
    assert store.search(db, "抓取")


def test_rebuild_index_keeps_running_entry_status(tmp_path):
    """preserve_meta=True 时，重建不得把 running/pending 的条目改成 done。"""
    ws = tmp_path / "wiki"
    (ws / "artifacts" / "2026-09-15_0003").mkdir(parents=True)
    db = ws / "data" / "wiki.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(db)
    (ws / "artifacts" / "2026-09-15_0003" / "record.json").write_text(json.dumps({
        "version": "3.0", "id": "2026-09-15_0003", "title": "t", "date": "2026-09-15",
        "topic_type": "paper", "tldr": "t", "tags": ["a"],
        "entities": {"company": [], "author": [], "product": [], "series": []},
        "links": [], "source": {"input_type": "url", "source_type": "arxiv_paper"},
    }, ensure_ascii=False), encoding="utf-8")
    store.upsert_task(db, "2026-09-15_0003", status="running", owner="kimi")

    store.rebuild_index(db, ws, preserve_meta=True)
    assert store.get_entry(db, "2026-09-15_0003")["status"] == "running"

    store.rebuild_index(db, ws, preserve_meta=False)
    assert store.get_entry(db, "2026-09-15_0003")["status"] == "done"
