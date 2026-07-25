#!/usr/bin/env python3
"""
输入源分类器 — 纯规则引擎，零 LLM 调用。

根据 URL 域名/路径或文本中的关键词/实体/缩写，判断输入对应的来源类型。
规则来源：references/sources.yaml（单一真相源）。
"""
import re
import json
import argparse
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# 允许从仓库任意位置被直接调用

from scripts import source_config as sc
URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]]+', re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """从文本中提取所有 URL。"""
    return URL_PATTERN.findall(text)


def _normalize_domain(netloc: str) -> str:
    d = netloc.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def classify_url(url: str, config: Optional[dict] = None) -> Optional[dict]:
    """根据 sources.yaml 分类单个 URL。"""
    result = sc.classify_url(url, config)
    if not result:
        return None
    subtype, defn, extras = result
    return {
        "url": url,
        "type": "url",
        "input_type": "url",
        "subtype": subtype,
        "source_type": sc.resolve_platform(subtype),
        "confidence": "low" if defn.get('classification', {}).get('fallback') else "high",
        "label_cn": defn.get('label_cn', subtype),
        "source_text": url,
        "matched_rule": f"domain:{subtype}",
        **extras,
    }


def classify_non_url(text: str, config: Optional[dict] = None) -> list[dict]:
    """根据 sources.yaml 分类非 URL 输入。"""
    hits = sc.classify_non_url(text, config)
    results = []
    for h in hits:
        results.append({
            "type": "non_url",
            "input_type": "keywords",
            "subtype": h['subtype'],
            "source_type": sc.resolve_platform(h['subtype']),
            "confidence": h['confidence'],
            "label_cn": h['definition'].get('label_cn', h['subtype']),
            "source_text": h['source_text'],
            "matched_rule": h['matched_rule'],
            **h['extras'],
        })
    return results


def classify(text: str, config: Optional[dict] = None) -> dict:
    """
    主分类函数。
    输入: 用户消息字符串
    输出: 结构化分类结果
    """
    text = text.strip()
    if not text:
        return {
            "status": "unclassifiable",
            "results": [],
            "suggestions": [],
            "needs_confirm": True,
            "error": "empty input",
        }

    all_results = []
    urls = extract_urls(text)

    if urls:
        for url in urls:
            r = classify_url(url, config)
            if r:
                all_results.append(r)
        # 额外检查文中 arXiv ID
        non_url_results = classify_non_url(text, config)
        for nr in non_url_results:
            if nr["subtype"] == "arxiv_id":
                existing_ids = {r.get("arxiv_id") for r in all_results if r.get("arxiv_id")}
                if nr.get("arxiv_id") not in existing_ids:
                    all_results.append(nr)
    else:
        all_results = classify_non_url(text, config)

    if not all_results:
        return {
            "status": "unclassifiable",
            "results": [],
            "suggestions": ["请提供 URL 或更具体的描述（如 arXiv ID、公司名、论文标题等）"],
            "needs_confirm": True,
        }

    high_count = sum(1 for r in all_results if r["confidence"] == "high")
    medium_count = sum(1 for r in all_results if r["confidence"] == "medium")
    low_count = sum(1 for r in all_results if r["confidence"] == "low")
    total = len(all_results)

    needs_confirm = False
    if total > 1 and high_count == 0:
        needs_confirm = True
    elif low_count == total and total > 0:
        needs_confirm = True

    status = "ok" if not needs_confirm else "ambiguous"

    suggestions = []
    if needs_confirm:
        suggestions = [
            {"index": i, "subtype": r["subtype"], "label": r["label_cn"],
             "url": r.get("url", ""), "confidence": r["confidence"]}
            for i, r in enumerate(all_results)
        ]

    return {
        "status": status,
        "results": all_results,
        "suggestions": suggestions,
        "needs_confirm": needs_confirm,
    }


def main():
    parser = argparse.ArgumentParser(description="Wiki source classifier (pure rules, zero LLM)")
    parser.add_argument("--input", "-i", type=str, help="User input text")
    parser.add_argument("--domain", "-d", type=str, help="Classify a single domain name")
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.domain:
        result = classify_url(f"https://{args.domain}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.input is not None:
        result = classify(args.input)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
