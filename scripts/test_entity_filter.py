"""scripts/test_entity_filter.py — 实体抑制（suppress）/ 分组（group）/ clean-entities 契约测试。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import conftest
from scripts import entity_filter as EF
from scripts.records import links as L
from scripts.records.clean_entities import clean_entities
from scripts.records import schema as RS
from scripts.wiki_index import ensure_schema

SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "cli.py"


# ---------------------------------------------------------------------------
# suppress：精确 / 正则 / 豁免 / 卫生规则
# ---------------------------------------------------------------------------
def _mini_suppression():
    aliases = {
        "terms": {"具身智能": ["embodied AI"]},
        "entities": {"product": {"GR00T N1.7": ["GR00T"]}, "company": {}, "person": {}},
        "series_roots": {"GR00T": ["GR00T N1.7"]},
        "suppress": ["机器之心", "MIT License"],
        "suppress_patterns": [r"^[A-Za-z]{0,2}\d+([-/][A-Za-z]{0,3}\d+)+$", r"^A100"],
    }
    groups = {"groups": {"A100 Lab": "academia"}, "academia_keywords": ["大学"]}
    return EF.build_suppression(aliases, groups)


def test_suppress_exact_case_insensitive():
    s = _mini_suppression()
    assert EF.is_suppressed("机器之心", s)
    assert EF.is_suppressed("mit license", s)   # 大小写不敏感
    assert EF.is_suppressed(" MIT License ", s)  # strip 后比较
    assert not EF.is_suppressed("Figure AI", s)


def test_suppress_pattern():
    s = _mini_suppression()
    assert EF.is_suppressed("C0-C1", s)
    assert EF.is_suppressed("l1-l5", s)   # re.IGNORECASE
    assert EF.is_suppressed("A100", s)
    assert EF.is_suppressed("A100-80G", s)  # search 语义
    assert not EF.is_suppressed("Helix", s)


def test_suppress_exemption_canonical_and_groups():
    s = _mini_suppression()
    # aliases canonical key（GR00T N1.7 / series_roots 的 GR00T）命中 range pattern 也不抑制
    assert not EF.is_suppressed("GR00T", s)
    # entity_groups.yaml 显式列出的名字命中 pattern 也豁免
    assert not EF.is_suppressed("A100 Lab", s)
    assert not EF.is_suppressed("a100 lab", s)  # 豁免同样大小写不敏感


def test_suppress_hygiene_length():
    s = _mini_suppression()
    assert EF.is_suppressed("", s)
    assert EF.is_suppressed("X", s)               # 长度 ≤1
    assert EF.is_suppressed("y" * 51, s)          # 长度 >50
    assert not EF.is_suppressed("Ab", s)


def test_suppress_real_references():
    """真实 references 配置：媒体名 / 硬件 / 日期 / markdown 碎片被抑制，canonical 豁免。"""
    s = EF.load_suppression()
    assert EF.is_suppressed("机器之心", s)
    assert EF.is_suppressed("C0-C1", s)
    assert EF.is_suppressed("H100-80G", s)
    assert EF.is_suppressed("2026-03-30", s)
    assert EF.is_suppressed("关联 |", s)
    for kept in ("VGGT", "清华大学", "GPT-5", "Qwen3-VL", "SO-100", "GR00T N1.7", "V-JEPA 2"):
        assert not EF.is_suppressed(kept, s), kept


def test_canonicalize_alias_first_then_suppress():
    """先 alias→canonical，再判抑制：别名写法被 canonical 名的 suppress 覆盖。"""
    s = EF.load_suppression()
    out = EF.canonicalize_entities(
        {"company": ["NVIDIA", "机器之心"], "author": [],
         "product": ["GR00T-1.7", "A100", "Qwen3-VL-8B"], "series": []},
        suppression=s)
    assert out["company"] == ["英伟达"]            # alias 归一 + 媒体名剔除
    assert out["product"] == ["GR00T N1.7", "Qwen3-VL"]  # 硬件剔除 + 型号别名合并


# ---------------------------------------------------------------------------
# 分组：默认规则 + 显式映射优先 + 多分组（列表值）
# ---------------------------------------------------------------------------
def test_entity_group_default_rules():
    cfg = {"groups": {}, "academia_keywords": ["大学", "university", "lab"]}
    assert EF.entity_groups_for("翁荔", "author", cfg) == ["person"]
    assert EF.entity_groups_for("某某大学", "company", cfg) == ["academia"]
    assert EF.entity_groups_for("Some University", "company", cfg) == ["academia"]  # 大小写不敏感子串
    assert EF.entity_groups_for("Acme Corp", "company", cfg) == ["company"]
    assert EF.entity_groups_for("Helix", "product", cfg) == ["product"]
    assert EF.entity_groups_for("LingBot", "series", cfg) == ["product"]


def test_entity_group_explicit_mapping_wins():
    cfg = EF.load_entity_groups()
    assert EF.entity_groups_for("蚂蚁灵波", "company", cfg) == ["company"]
    assert EF.entity_groups_for("清华大学", "company", cfg) == ["academia"]
    assert EF.entity_groups_for("GPT-5", "product", cfg) == ["product"]
    assert EF.entity_groups_for("李飞飞", "author", cfg) == ["person"]


def test_entity_groups_multi_value():
    """多分组：yaml 列表值原样返回（允许重叠）；单值归一化为单元素列表。"""
    cfg = EF.load_entity_groups()
    assert EF.entity_groups_for("GR00T N1.7", "product", cfg) == ["oss", "product"]
    assert EF.entity_groups_for("Meta FAIR", "company", cfg) == ["company", "academia"]
    assert EF.entity_groups_for("VGGT", "product", cfg) == ["oss"]  # 纯开源单值
    # 向后兼容单值接口
    assert EF.entity_group("GR00T N1.7", "product", cfg) == "oss"
    assert EF.entity_group("某某大学", "company", cfg) == "academia"


def test_load_entity_groups_normalizes_values(tmp_path):
    """groups 值允许字符串或字符串列表；非法 group 名被丢弃。"""
    p = tmp_path / "g.yaml"
    p.write_text(
        "groups:\n"
        "  A: oss\n"
        "  B: [oss, product]\n"
        "  C: [product, bogus, oss]\n"
        "academia_keywords:\n  - 大学\n",
        encoding="utf-8")
    cfg = EF.load_entity_groups(p)
    assert cfg["groups"]["a"] == ["oss"]
    assert cfg["groups"]["b"] == ["oss", "product"]
    assert cfg["groups"]["c"] == ["product", "oss"]  # bogus 被过滤


# ---------------------------------------------------------------------------
# clean-entities：dry-run / apply
# ---------------------------------------------------------------------------
def _ws_db(tmp_path):
    ws = tmp_path / "wiki"
    (ws / "data").mkdir(parents=True)
    (ws / "artifacts").mkdir(parents=True)
    db = ws / "data" / "wiki.db"
    ensure_schema(db)
    return ws, db


def _seed_record(ws, db, slug, entities):
    conftest.seed_entry(db, slug, status="done")
    record = {
        "version": "2.0", "id": slug, "title": f"t-{slug}", "tldr": "x",
        "summary": "x", "tags": ["robotics"], "topic_type": "paper", "date": "2026-08-01",
        "source": {"input_type": "url", "source_type": "arxiv"},
        "entities": entities, "links": [],
    }
    RS.save_record(slug, ws, record)
    return record


def test_clean_entities_dry_run(tmp_path):
    ws, db = _ws_db(tmp_path)
    rec = _seed_record(ws, db, "2026-08-01_aaaa", {
        "company": ["Nvidia", "机器之心"], "author": [],
        "product": ["A100", "GR00T-1.7"], "series": []})
    data = clean_entities(db, ws, apply=False)
    assert data["scanned"] == 1 and data["changed"] == 1
    assert data["dry_run"] is True and data["site_rebuilt"] is False
    removed = data["removed_entities"]
    assert removed.get("机器之心") == 1 and removed.get("A100") == 1
    assert removed.get("Nvidia") == 1 and removed.get("GR00T-1.7") == 1  # alias 归一也算变化
    # dry-run 不落盘：record.json 与 db 均未变
    assert RS.load_record("2026-08-01_aaaa", ws)["entities"] == rec["entities"]
    assert L.get_entry_entities(db, "2026-08-01_aaaa")["company"] == []


def test_clean_entities_apply(tmp_path):
    ws, db = _ws_db(tmp_path)
    _seed_record(ws, db, "2026-08-01_aaaa", {
        "company": ["Nvidia", "机器之心"], "author": [],
        "product": ["A100", "GR00T-1.7"], "series": []})
    data = clean_entities(db, ws, apply=True)
    assert data["changed"] == 1 and data["site_rebuilt"] is True
    ents = RS.load_record("2026-08-01_aaaa", ws)["entities"]
    assert ents["company"] == ["英伟达"]
    assert ents["product"] == ["GR00T N1.7"]
    assert L.get_entry_entities(db, "2026-08-01_aaaa") == ents
    # 幂等：再跑一遍无变化
    data2 = clean_entities(db, ws, apply=False)
    assert data2["changed"] == 0


def test_clean_entities_skips_invalid_entities(tmp_path):
    ws, db = _ws_db(tmp_path)
    _seed_record(ws, db, "2026-08-01_aaaa", {"company": ["Figure AI"], "author": [],
                                             "product": [], "series": []})
    # 老数据：entities 不是合法 dict → 容错跳过并计数
    bad = RS.load_record("2026-08-01_aaaa", ws)
    bad["entities"] = "not-a-dict"
    RS.save_record("2026-08-01_aaaa", ws, bad)
    data = clean_entities(db, ws, apply=False)
    assert data["scanned"] == 1 and data["skipped"] == 1 and data["changed"] == 0


def test_clean_entities_filter_by_id(tmp_path):
    ws, db = _ws_db(tmp_path)
    _seed_record(ws, db, "2026-08-01_aaaa", {"company": ["机器之心"], "author": [],
                                             "product": [], "series": []})
    _seed_record(ws, db, "2026-08-02_bbbb", {"company": ["机器之心"], "author": [],
                                             "product": [], "series": []})
    data = clean_entities(db, ws, entry_id="2026-08-01_aaaa", apply=False)
    assert data["scanned"] == 1 and data["changed_ids"] == ["2026-08-01_aaaa"]


# ---------------------------------------------------------------------------
# clean-entities：CLI 契约（--json，默认 dry-run）
# ---------------------------------------------------------------------------
def _cli(*args):
    r = subprocess.run([sys.executable, str(CLI), "--json", *args], capture_output=True)
    return r.returncode, json.loads(r.stdout.decode("utf-8"))


def test_cli_clean_entities_dry_run_json(tmp_path):
    ws = tmp_path / "wiki"
    rc, out = _cli("--workspace", str(ws), "init")
    assert rc == 0 and out["ok"]
    db = ws / "data" / "wiki.db"
    conftest.seed_entry(db, "2026-08-01_aaaa", status="done")
    _seed_record(ws, db, "2026-08-01_aaaa", {
        "company": ["机器之心"], "author": [], "product": ["C0-C1"], "series": []})

    rc, out = _cli("--workspace", str(ws), "clean-entities")
    assert rc == 0 and out["ok"], out
    data = out["data"]
    for key in ("scanned", "changed", "removed_entities", "site_rebuilt"):
        assert key in data, key
    assert data["scanned"] == 1 and data["changed"] == 1
    assert data["removed_entities"].get("机器之心") == 1
    assert data["site_rebuilt"] is False
    # dry-run 不写回
    assert "机器之心" in RS.load_record("2026-08-01_aaaa", ws)["entities"]["company"]

    rc, out = _cli("--workspace", str(ws), "clean-entities", "--apply", "--id", "2026-08-01_aaaa")
    assert rc == 0 and out["ok"], out
    assert out["data"]["site_rebuilt"] is True
    assert RS.load_record("2026-08-01_aaaa", ws)["entities"]["company"] == []
