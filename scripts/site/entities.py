#!/usr/bin/env python3
"""
scripts/site/entities.py — wiki 实体抽取与关联索引。

从每篇 wiki 文章的标题、概述、标签和正文中抽取四类实体：
  - company: 公司 / 机构
  - author: 作者 / 人物（论文作者、技术博客作者、被提及的关键人物）
  - product: 产品 / 模型 / 框架
  - series: 技术系列 / 产品族 / 开源项目族

抽取结果输出为 entities.json，供前端渲染「同公司/同作者/同产品/同系列」关联面板。
"""
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
REFERENCES_DIR = SCRIPT_DIR.parent.parent / "references"
ALIASES_PATH = REFERENCES_DIR / "entity_aliases.yaml"


# ---------------------------------------------------------------------------
# Alias loading & normalization
# ---------------------------------------------------------------------------

def load_aliases(path: Path = None) -> dict:
    """加载 entity_aliases.yaml，返回结构化的 alias 配置。"""
    path = path or ALIASES_PATH
    if not path.exists():
        return {"terms": {}, "entities": {"company": {}, "product": {}, "person": {}}, "series_roots": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_variant_map(aliases: dict) -> dict:
    """
    把 aliases 配置转成 {variant_lower: canonical} 的快速查找表。
    涵盖 terms 和 entities 下的 company/product/person。
    """
    variant_map = {}

    # 术语同义词
    for canonical, variants in aliases.get("terms", {}).items():
        for v in [canonical] + (variants or []):
            variant_map[_norm_key(v)] = canonical

    # 实体别名
    for entity_type, entities in aliases.get("entities", {}).items():
        for canonical, variants in (entities or {}).items():
            for v in [canonical] + (variants or []):
                variant_map[_norm_key(v)] = canonical

    return variant_map


def _norm_key(text: str) -> str:
    """用于归一化匹配 key：小写、去首尾空格、压缩连续空格。"""
    if not text:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _entity_in_text(text: str, name: str) -> bool:
    """判断 name 是否作为独立实体出现在 text 中。

    - 若 name 包含中文字符，使用子串匹配（中文无显式分词，按常见名称为整体处理）。
    - 若为纯 ASCII 实体名，要求前后不能紧跟字母或数字，避免子串误匹配。
    """
    if not name or not text:
        return False
    if re.search(r"[一-龥]", name):
        return name in text
    pattern = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
    return bool(re.search(pattern, text))


def normalize(text: str, variant_map: dict) -> str:
    """把 text 的任意变体归一到 canonical 名称；没有命中则返回原样。"""
    key = _norm_key(text)
    return variant_map.get(key, text.strip())


def normalize_list(items: list, variant_map: dict) -> list:
    """归一化字符串列表，去重，保留非空项。"""
    seen = set()
    result = []
    for item in items:
        if not item or not str(item).strip():
            continue
        canonical = normalize(item, variant_map)
        key = _norm_key(canonical)
        if key and key not in seen:
            seen.add(key)
            result.append(canonical)
    return result


# ---------------------------------------------------------------------------
# Text reading helpers
# ---------------------------------------------------------------------------

def _read_markdown(slug: str, wiki_dir: Path, file_field: str = "", depth: str = "brief") -> str:
    """读取条目的 markdown 正文（优先 brief，再试 deep）。"""
    candidates = []
    if file_field:
        candidates.append(wiki_dir / "artifacts" / slug / file_field)
    candidates.append(wiki_dir / "artifacts" / slug / f"{slug}_{depth}.md")
    candidates.append(wiki_dir / "artifacts" / slug / f"{slug}_brief.md")
    candidates.append(wiki_dir / "artifacts" / slug / f"{slug}_deep.md")

    for path in candidates:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
    return ""


def _combine_text(entry: dict, wiki_dir: Path) -> str:
    """把条目标题、概述、标签、正文拼成一段文本用于抽取。"""
    parts = [
        entry.get("title") or "",
        entry.get("overview") or "",
        ", ".join(entry.get("tags") or []),
    ]
    md = _read_markdown(entry.get("id", ""), wiki_dir, entry.get("file", ""), entry.get("depth", "brief"))
    # 去掉 markdown 链接语法，保留链接文字
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    parts.append(md)
    return "\n".join(filter(None, parts))


# ---------------------------------------------------------------------------
# Entity extractors
# ---------------------------------------------------------------------------

def _extract_explicit_field(text: str, names: list[str]) -> list[str]:
    """从 Key Info 等显式字段抽取值，例如 **公司**：蚂蚁灵波。"""
    values = []
    for name in names:
        # 支持 **公司**：value 或 **Company**: value，直到行尾或下一个 **
        pattern = rf"\*\*{re.escape(name)}[：:]?\*\*\s*[：:]?\s*(.+?)(?:\n|\*\*|\Z)"
        for m in re.finditer(pattern, text, re.I):
            value = m.group(1).strip()
            # 多个值用逗号/顿号/分号分隔
            for v in re.split(r"[,，;；、]", value):
                v = v.strip()
                if v:
                    values.append(v)
    return values


def _extract_company_patterns(text: str) -> list[str]:
    """基于常见后缀模式发现公司/机构名（仅中文）。

    为避免把“XX公司/XX实验室/XX智能”等普通短语误判为机构，
    只匹配带有明确机构后缀的短语：科技/集团/研究院/大学/学院。
    更通用的机构名（如公司、实验室）通过显式字段和 alias 字典覆盖。
    """
    pattern = r"[一-龥]{2,10}(?:科技|集团|研究院|大学|学院)"
    found = []
    for m in re.finditer(pattern, text):
        name = m.group(0).strip()
        # 过滤以虚词/量词/常见动词开头的误匹配
        if re.match(r"^[的是为由于一与和等被向在从到对给把将让使叫看管说做用有]", name):
            continue
        if len(name) >= 4:
            found.append(name)
    return found


def extract_companies(text: str, aliases: dict, variant_map: dict) -> list[str]:
    """抽取公司/机构实体。"""
    candidates = []
    candidates.extend(_extract_explicit_field(text, ["公司", "Company", "机构", "Organization"]))
    candidates.extend(_extract_company_patterns(text))

    # 加入已知 company 别名表中所有实体的直接匹配
    company_entities = aliases.get("entities", {}).get("company", {})
    for canonical, variants in company_entities.items():
        for name in [canonical] + (variants or []):
            if _entity_in_text(text, name):
                candidates.append(canonical)

    return normalize_list(candidates, variant_map)


def _extract_author_patterns(text: str) -> list[str]:
    """从常见作者/人物署名格式抽取。"""
    values = []
    # **作者**：翁荔 / **Authors**: Kaichen Zhou, ...
    values.extend(_extract_explicit_field(text, ["作者", "Author", "Authors", "撰稿", "Writer"]))

    # 文/翁荔、撰文/翁荔、作者｜翁荔
    for m in re.finditer(r"(?:文|撰文|作者|编辑|编译|整理)\s*[/／|｜]\s*([^\n，,；;]+)", text):
        values.append(m.group(1).strip())

    # 公众号/博客署名行：by 翁荔 / By Lilian Weng
    for m in re.finditer(r"\b[byBY]{2}\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", text):
        values.append(m.group(1).strip())

    return values


def _clean_author(name: str) -> str:
    """去掉作者名后面常见的平台/栏目后缀，如“翁荔（公众号）”。"""
    name = re.sub(r"[（(]\s*(?:博客|公众号|知乎|专栏|微博|推特|Twitter|X)\s*[）)]", "", name)
    name = re.sub(r"\s*(?:博客|公众号|知乎|专栏|微博|推特|Twitter|X)\s*$", "", name)
    return name.strip()


def _is_invalid_author(name: str) -> bool:
    """过滤掉看起来是机构、停用词或非人名的字符串。"""
    name_lower = name.lower()
    inst_keywords = ["大学", "研究院", "实验室", "公司", "科技", "智能", "university", "lab", "institute", "inc", "corp", "research"]
    author_stopwords = {
        # 英文常见误匹配
        "cross references", "references", "summary", "introduction", "conclusion",
        "acknowledgements", "acknowledgment", "thanks", "thank", "appendix", "abstract",
        "figure", "figures", "table", "tables", "method", "methods", "paper",
        "article", "blog", "post", "author", "authors", "writer", "writers",
        "continue reading", "read more", "share", "follow", "subscribe", "path length",
        # 中文常见误匹配
        "谢谢", "感谢", "编辑", "笔者", "作者", "我们", "大家", "读者", "关注",
    }
    if name_lower in author_stopwords:
        return True
    return any(k in name_lower for k in inst_keywords)


def _extract_known_people(text: str, aliases: dict) -> list[str]:
    """从 alias 字典中已知人物列表匹配正文。"""
    found = []
    person_entities = aliases.get("entities", {}).get("person", {})
    for canonical, variants in person_entities.items():
        for name in [canonical] + (variants or []):
            if _entity_in_text(text, name):
                found.append(canonical)
    return found


def extract_authors(text: str, aliases: dict, variant_map: dict) -> list[str]:
    """抽取作者/人物实体，兼容论文与技术博客。"""
    candidates = _extract_author_patterns(text)
    candidates.extend(_extract_known_people(text, aliases))

    # 清理并过滤机构名与停用词
    candidates = [_clean_author(c) for c in candidates]
    candidates = [c for c in candidates if c and not _is_invalid_author(c)]

    return normalize_list(candidates, variant_map)


# 常见不应被识别为产品的英文单词
_PRODUCT_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "have", "has", "had",
    "not", "are", "was", "were", "been", "being", "will", "would", "could",
    "should", "may", "might", "can", "shall", "into", "over", "such", "than",
    "only", "some", "time", "year", "years", "more", "most", "other", "many",
    "their", "there", "these", "those", "when", "where", "what", "which", "who",
    "how", "all", "any", "both", "each", "few", "much", "many", "several",
    "first", "second", "last", "new", "old", "good", "bad", "best", "better",
    "high", "low", "large", "small", "long", "short", "early", "late",
    "based", "using", "via", "novel", "proposed", "approach", "method",
    "model", "models", "paper", "arxiv", "github", "dataset", "benchmark",
    # 文章模板常见章节标题
    "type", "ver", "tags", "materials", "references", "summary", "reflection",
    "origin", "key", "info", "date", "sources", "overview", "title", "abstract",
    "introduction", "conclusion", "background", "related", "work", "works",
    "table", "figure", "figures", "section", "appendix",
    # 常见非产品缩写/短语
    "gpl-3.0", "top-3", "top-5", "top-10", "top-k", "cross-references", "cross references",
}


def _extract_product_patterns(text: str) -> list[str]:
    """基于严格模式发现技术产品/模型名。

    仅保留以下两类：
    1. 与已知 product 别名表命中（支持 LingBot-Vision 等无数字名称）。
    2. 包含连字符/斜杠且至少包含一个数字的版本号（如 DINOv3, PAGE-4D, GR00T N1.7）。
    """
    found = []
    pattern = r"[A-Z][a-zA-Z0-9]*(?:[-/][A-Za-z0-9]+(?:\.\d+)?)+(?:\s+[A-Za-z0-9]+(?:\.\d+)?)?"
    for m in re.finditer(pattern, text):
        name = m.group(0).strip()
        if len(name) < 3 or len(name) > 20:
            continue
        if "\n" in name:
            continue
        lower = name.lower()
        if lower in _PRODUCT_STOPWORDS:
            continue
        # 必须包含数字，或者是带连字符/斜杠的技术名
        if not re.search(r"[0-9]", name):
            continue
        found.append(name)

    return found


def extract_products(text: str, aliases: dict, variant_map: dict) -> list[str]:
    """抽取产品/模型/框架实体。"""
    candidates = []
    candidates.extend(_extract_explicit_field(text, ["产品", "Product", "模型", "Model"]))
    candidates.extend(_extract_product_patterns(text))

    # 已知 product 别名表
    product_entities = aliases.get("entities", {}).get("product", {})
    for canonical, variants in product_entities.items():
        for name in [canonical] + (variants or []):
            if _entity_in_text(text, name):
                candidates.append(canonical)

    return normalize_list(candidates, variant_map)


def derive_series(products: list[str], aliases: dict) -> list[str]:
    """从产品名和 series_roots 配置推导技术系列/产品族。"""
    series_roots = aliases.get("series_roots", {})
    if not series_roots:
        return []

    # 先对产品名做归一
    product_set = {p.strip() for p in products if p.strip()}

    found = []
    for series, members in series_roots.items():
        for member in members or []:
            member = member.strip()
            if member in product_set:
                if series not in found:
                    found.append(series)
                break

    # 启发式：如果产品名前缀与某个 series 名相同，也归到该 series
    for product in product_set:
        for series in series_roots:
            if product != series and product.startswith(series):
                if series not in found:
                    found.append(series)

    return found


# ---------------------------------------------------------------------------
# Entry-level extraction
# ---------------------------------------------------------------------------

def extract_entities(entry: dict, wiki_dir: Path, aliases: dict = None) -> dict:
    """对单个 entry 抽取实体，返回 {company, author, product, series}。"""
    if aliases is None:
        aliases = load_aliases()
    variant_map = _build_variant_map(aliases)

    slug = entry.get("id", "")
    text = _combine_text(entry, wiki_dir)

    # 标签参与实体发现：先把标签归一化，然后看是否命中已知实体
    raw_tags = entry.get("tags") or []
    normalized_tags = normalize_list(raw_tags, variant_map)
    tag_text = ", ".join(normalized_tags)
    full_text = text + "\n" + tag_text

    companies = extract_companies(full_text, aliases, variant_map)
    authors = extract_authors(full_text, aliases, variant_map)
    products = extract_products(full_text, aliases, variant_map)
    series = derive_series(products, aliases)

    # 如果标签本身就是已知实体（如 "翁荔"），确保被包含
    person_entities = aliases.get("entities", {}).get("person", {})
    company_entities = aliases.get("entities", {}).get("company", {})
    product_entities = aliases.get("entities", {}).get("product", {})
    for tag in normalized_tags:
        if tag in person_entities and tag not in authors:
            authors.append(tag)
        if tag in company_entities and tag not in companies:
            companies.append(tag)
        if tag in product_entities and tag not in products:
            products.append(tag)

    # 去重并保持稳定顺序
    def uniq(items):
        seen = set()
        result = []
        for item in items:
            key = _norm_key(item)
            if key and key not in seen:
                seen.add(key)
                result.append(item)
        return result

    return {
        "company": uniq(companies),
        "author": uniq(authors),
        "product": uniq(products),
        "series": uniq(series),
        "normalized_tags": normalized_tags,
    }


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_entity_index(entries: list[dict], wiki_dir: Path, aliases: dict = None) -> dict:
    """为所有 entry 构建实体索引。"""
    if aliases is None:
        aliases = load_aliases()

    wiki_dir = Path(wiki_dir)
    by_entry = {}
    by_type = defaultdict(lambda: defaultdict(list))

    for entry in entries:
        slug = entry.get("id")
        if not slug:
            continue
        entities = extract_entities(entry, wiki_dir, aliases)
        by_entry[slug] = {
            "company": entities["company"],
            "author": entities["author"],
            "product": entities["product"],
            "series": entities["series"],
            "normalized_tags": entities["normalized_tags"],
        }
        for etype in ("company", "author", "product", "series"):
            for entity in entities[etype]:
                by_type[etype][entity].append(slug)

    # 把 defaultdict 转成普通 dict 以便 JSON 序列化
    return {
        "by_entry": by_entry,
        "by_type": {k: dict(v) for k, v in by_type.items()},
    }
