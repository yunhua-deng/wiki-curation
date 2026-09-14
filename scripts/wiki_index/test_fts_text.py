#!/usr/bin/env python3
"""test_fts_text.py — CJK 分词适配（索引侧/查询侧共用转换）契约测试。"""
from scripts.wiki_index.fts_text import to_index_text, to_match_phrase, split_terms


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
