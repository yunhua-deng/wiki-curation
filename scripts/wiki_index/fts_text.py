#!/usr/bin/env python3
"""fts_text.py — FTS5 的 CJK 分词适配（索引侧与查询侧共用同一套转换）。

FTS5 默认 unicode61 tokenizer 把连续汉字当作**一个 token**，于是「智能」这种
嵌在「具身智能」里的词永远匹配不到（实测 1/186）。这里在索引与查询两侧做同一套
转换：CJK 逐字切开成独立 token，拉丁/数字串原样保留；查询侧再把每个词包成短语，
从而得到子串级的中文匹配。过长的无空格 CJK 串（>= BIGRAM_MIN_CJK 字）另见
to_match_expr：整串短语过于严格，需额外 OR 上相邻二字组。
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


# 无空格 CJK 串达到该长度时，额外展开相邻二字组（见 to_match_expr）。
BIGRAM_MIN_CJK = 5

_CJK_RUN_RE = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')


def to_match_expr(token: str) -> str:
    """单个查询词的 FTS5 表达式。

    短词仍用整词短语（保持精确）。长度 >= BIGRAM_MIN_CJK 的 CJK 连续串额外
    OR 上它的相邻二字组——中文查询通常不带空格，"机器人抓取策略" 会被当成一个
    要求整串连续出现的短语而召回为空；展开二字组后即可召回含"机器人"/"抓取"/
    "策略" 的条目。二字组的查询形式同样是逐字短语（"机 器"）。
    """
    parts = [to_match_phrase(token)]
    for run in _CJK_RUN_RE.findall(token or ''):
        if len(run) < BIGRAM_MIN_CJK:
            continue
        for i in range(len(run) - 1):
            parts.append(to_match_phrase(run[i:i + 2]))
    parts = list(dict.fromkeys(parts))          # 去重，保持顺序
    return parts[0] if len(parts) == 1 else '(' + ' OR '.join(parts) + ')'
