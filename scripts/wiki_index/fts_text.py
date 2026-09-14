#!/usr/bin/env python3
"""fts_text.py — FTS5 的 CJK 分词适配（索引侧与查询侧共用同一套转换）。

FTS5 默认 unicode61 tokenizer 把连续汉字当作**一个 token**，于是「智能」这种
嵌在「具身智能」里的词永远匹配不到（实测 1/186）。这里在索引与查询两侧做同一套
转换：CJK 逐字切开成独立 token，拉丁/数字串原样保留；查询侧再把每个词包成短语，
从而得到子串级的中文匹配。
"""
import re

# CJK 表意文字（BMP 基本区 + 扩展 A）。注意 Python 的 \w 已包含 CJK，
# 这个字符类只用于"逐字切开"。
_CJK_RE = re.compile('([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])')


def to_index_text(*parts) -> str:
    """索引侧：CJK 逐字切开（每个汉字成为独立 token），其余原样。"""
    text = ' '.join(str(p) for p in parts if p)
    return _CJK_RE.sub(r' \1 ', text)


def to_match_phrase(token: str) -> str:
    """查询侧：把单个查询词转成 FTS5 短语；CJK 会变成逐字短语。"""
    escaped = (token or '').replace('"', '""')
    return '"' + _CJK_RE.sub(r' \1 ', escaped).strip() + '"'


def split_terms(text: str) -> list:
    """把任意输入切成查询词（\\w 已覆盖 CJK 与拉丁）。"""
    return re.findall(r'\w+', text or '')
