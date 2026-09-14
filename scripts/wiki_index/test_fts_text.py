#!/usr/bin/env python3
"""test_fts_text.py — CJK 分词适配（索引侧/查询侧共用转换）契约测试。"""
from scripts.wiki_index.fts_text import (
    BIGRAM_MIN_CJK, split_terms, to_index_text, to_match_expr, to_match_phrase,
)


def test_to_index_text_splits_cjk():
    assert to_index_text('具身智能').split() == ['具', '身', '智', '能']
    assert to_index_text('机器人').split() == ['机', '器', '人']


def test_to_index_text_keeps_latin_runs():
    assert to_index_text('VLA model 2024').split() == ['VLA', 'model', '2024']


def test_to_index_text_mixed():
    assert to_index_text('VLA模型').split() == ['VLA', '模', '型']


def test_to_index_text_multiple_parts_and_skips_empty():
    assert to_index_text('具身', '', '智能').split() == ['具', '身', '智', '能']
    assert to_index_text().split() == []


def test_to_match_phrase_cjk():
    assert to_match_phrase('智能').split() == ['"智', '能"']
    assert to_match_phrase('VLA模型').split() == ['"VLA', '模', '型"']


def test_to_match_phrase_latin_and_escaping():
    assert to_match_phrase('Helix') == '"Helix"'
    assert to_match_phrase('a"b') == '"a""b"'
    assert to_match_phrase('') == '""'


def test_split_terms():
    assert split_terms('具身智能 VLA-model 2024') == ['具身智能', 'VLA', 'model', '2024']
    assert split_terms('') == []
    assert split_terms(None) == []


# ---------- to_match_expr：长 CJK 串的二字组回退 ----------

def _inner_parts(token):
    expr = to_match_expr(token)
    assert expr.startswith('(') and expr.endswith(')'), expr
    return expr[1:-1].split(' OR ')


def test_to_match_expr_short_run_keeps_single_phrase():
    """阈值以下的 CJK 串仍是单个逐字短语，不展开。"""
    for token in ('智能', '机器人', '具身智能', '抓取策略'):
        assert len(token) < BIGRAM_MIN_CJK
        assert to_match_expr(token) == to_match_phrase(token)
        assert ' OR ' not in to_match_expr(token)


def test_to_match_expr_expands_at_threshold():
    """恰好 BIGRAM_MIN_CJK 字的连续串展开成「整串短语 OR 相邻二字组」。"""
    run = '机器人抓取'
    assert len(run) == BIGRAM_MIN_CJK
    parts = _inner_parts(run)
    assert parts[0] == to_match_phrase(run)
    assert parts[1:] == [to_match_phrase(run[i:i + 2]) for i in range(len(run) - 1)]


def test_to_match_expr_dedupes_bigrams():
    """重复出现的二字组只保留一次。"""
    parts = _inner_parts('机器人机器人')  # 5 个二字组，去重后 3 个
    assert parts[0] == to_match_phrase('机器人机器人')
    assert len(parts) == 1 + 3
    assert len(parts) == len(set(parts))
    assert parts.count(to_match_phrase('机器')) == 1


def test_to_match_expr_latin_unaffected():
    assert to_match_expr('Helix') == '"Helix"'
    assert to_match_expr('VLA') == '"VLA"'
    assert to_match_expr('2024') == '"2024"'
    assert to_match_expr('a"b') == '"a""b"'
    assert to_match_expr('') == '""'


def test_to_match_expr_mixed_latin_and_long_cjk():
    """拉丁前缀不影响后续 CJK 串的二字组展开。"""
    parts = _inner_parts('VLA机器人抓取策略')  # CJK 串 7 字 -> 6 个二字组
    assert parts[0] == to_match_phrase('VLA机器人抓取策略')
    assert len(parts) == 1 + 6
    assert parts[1:] == [to_match_phrase(g) for g in
                         ('机器', '器人', '人抓', '抓取', '取策', '策略')]
