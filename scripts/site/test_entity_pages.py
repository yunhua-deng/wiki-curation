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
    # 冻结管线移除：posts/tracking/surveys 数据文件不再生成
    assert not (out / "data" / "posts.json").exists()
    assert not (out / "data" / "tracking.json").exists()
    assert not (out / "data" / "surveys.json").exists()


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


def test_nav_has_only_records_and_entities(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="nav-entities"' in html
    assert 'id="nav-posts"' not in html
    assert 'id="nav-tracking"' not in html
    assert 'id="entities-view"' in html
    # 冻结管线移除：posts/tracking 视图容器不再生成
    assert 'id="posts-view"' not in html and 'id="tracking-view"' not in html


def test_doc_reader_supports_entity_kind(tmp_path):
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    doc = (out / "doc.html").read_text(encoding="utf-8")
    assert "entities/" in doc and "summary.md" in doc
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "entity_pages.json" in site_js
    assert "nav-entities" in site_js


def test_records_view_has_no_survey_entry(tmp_path):
    """综述冻结：records 视图（site.js）不再出现任何综述入口/触发逻辑。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "data-surveyid" not in site_js
    assert "col-survey" not in site_js
    assert "/api/survey" not in site_js
    assert "survey.html" not in site_js
    # add-link 的 CLI fallback 仍保留占位符（build.py 注入依赖）
    assert "python " in site_js and "add-link" in site_js


def test_doc_reader_has_no_survey_entry(tmp_path):
    """doc.html 记录独立页不再出现综述按钮/触发逻辑；survey.html 已随冻结管线移除。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    doc = (out / "doc.html").read_text(encoding="utf-8")
    assert "surveyCellD" not in doc
    assert "survey-go" not in doc
    assert "addsurvey" not in doc
    assert "/api/survey" not in doc
    # survey.html 不再生成
    assert not (out / "survey.html").exists()


def test_entities_view_has_search_controls(tmp_path):
    """entities 视图提供搜索/watched-only 控制条；类型下拉已被五组分区取代。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="ent-search"' in html
    assert 'id="ent-filter-watch"' in html
    assert 'id="ent-filter-type"' not in html  # 分组已取代 type 筛选
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "ent-search" in site_js
    assert "ent-filter-watch" in site_js
    assert "ent-filter-type" not in site_js


def test_entities_view_grouped_sections(tmp_path):
    """entities 视图：五组分区标题 + 低频（record_count==1）默认隐藏 toggle + group 字段。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    pages = json.loads((out / "data" / "entity_pages.json").read_text(encoding="utf-8"))
    assert pages["figure-ai"]["group"] == "company"   # company bucket 默认规则
    assert pages["helix"]["group"] == "product"       # product bucket 默认规则
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    for label in ("高校与研究机构", "科技公司", "开源项目", "商业产品", "人物"):
        assert label in site_js, label
    assert "ent-toggle" in site_js                    # 低频实体展开 toggle
    assert "显示仅出现 1 次的实体" in site_js
    css = (out / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".ent-toggle" in css


def test_entity_detail_structured(tmp_path):
    """实体详情面板：结构化渲染（时间线柱图/按月分组记录/共现 chips 交叉导航/深链）。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "tl-chart" in site_js          # 时间线柱状图
    assert "ent-rec-group" in site_js     # 记录按月分组
    assert "co-ent" in site_js            # 共现实体交叉导航 chips
    assert "getParam('e')" in site_js     # ?v=entities&e=<slug> 深链
    css = (out / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".tl-bar" in css
    assert ".ent-rec-group" in css
    # survey 残留样式已清理
    assert ".survey-btn" not in css
    assert ".col-survey" not in css


def test_entity_ux_followups(tmp_path):
    """终审 follow-up：escapeHtml 转义引号（chip 属性安全）、时间线升序、canonical 域名分组。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "&quot;" in site_js            # escapeHtml 转义双引号
    assert "a.month < b.month" in site_js  # 时间线柱状图升序（旧→新）
    assert "byDomain" in site_js           # canonical 链接按域名分组
    doc = (out / "doc.html").read_text(encoding="utf-8")
    assert "&quot;" in doc                 # doc.html 的 esc() 同步修复


def test_ux_polish_stats_chips_modal(tmp_path):
    """UX 调整：stats 去掉 posts/tracking；record 实体 chips 改跳实体页（不再发起 track）；实体详情改弹出卡片。"""
    ws = _ws(tmp_path)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    _seed(db)
    out = build_site(db, ws, out_dir=tmp_path / "site_out")
    site_js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert " posts</div>" not in site_js and " tracking</div>" not in site_js  # stats 移除 posts/tracking
    assert "/api/track" not in site_js       # chip 不再发起跟踪
    assert "?v=entities&e=" in site_js       # chip 跳实体页深链
    assert "ent-modal" in site_js            # 实体详情弹出卡片
    doc = (out / "doc.html").read_text(encoding="utf-8")
    assert "/api/track" not in doc           # doc.html 记录页 chip 同步改
    assert "?v=entities&e=" in doc
    css = (out / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".ent-modal" in css
