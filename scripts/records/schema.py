#!/usr/bin/env python3
"""
scripts/records/schema.py — record.json 的校验、加载与链接工具函数。

手写确定性校验（不依赖 jsonschema）；约束常量来自 references/record_schema.json。
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from scripts import paths
from scripts.wiki_index.schema import VALID_TOPIC_TYPES

RECORD_VERSION = "3.0"

_CONSTRAINTS = None


def _load_constraints() -> dict:
    global _CONSTRAINTS
    if _CONSTRAINTS is None:
        path = paths.references_dir() / "record_schema.json"
        _CONSTRAINTS = json.loads(path.read_text(encoding="utf-8"))
    return _CONSTRAINTS


def link_kinds() -> set:
    return set(_load_constraints()["link_kinds"])


# ============================================================
# URL 工具
# ============================================================

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', 'ref_src', 'source', 'fbclid', 'gclid', 'gclsrc',
    '_ga', '_gl', 'mc_cid', 'mc_eid', 'mkt_tok',
}


def _strip_tracking(query: str) -> str:
    if not query:
        return ""
    pairs = query.split('&')
    cleaned = [p for p in pairs if p and p.split('=', 1)[0] not in TRACKING_PARAMS]
    return '&'.join(cleaned)


def normalize_url(url: str) -> str:
    """规范化用于精确匹配的 URL：scheme/host 小写、去 fragment、去尾部 /、去追踪参数。"""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        query = _strip_tracking(parts.query)
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url.rstrip("/")


_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)

_KIND_RULES = [
    ("github", re.compile(r"(^|\.)github\.com$", re.I)),
    ("arxiv", re.compile(r"(^|\.)arxiv\.org$", re.I)),
    ("huggingface", re.compile(r"(^|\.)(huggingface\.co|modelscope\.cn)$", re.I)),
    ("weixin", re.compile(r"(^|\.)mp\.weixin\.qq\.com$", re.I)),
    ("linkedin", re.compile(r"(^|\.)linkedin\.com$", re.I)),
    ("docs", re.compile(r"(^|\.)(readthedocs\.io)$|(^|\.)docs\.", re.I)),
]


def classify_link_kind(url: str) -> str:
    """按域名把 URL 归类到 link kind。"""
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return "other"
    if not host:
        return "other"
    for kind, rule in _KIND_RULES:
        if rule.search(host):
            return kind
    path = urlsplit(url).path.rstrip("/")
    # 裸域名或仅一层路径 → 项目/公司主页
    if path.count("/") <= 1:
        return "homepage"
    return "other"


# ============================================================
# record.json 校验
# ============================================================

def validate_record(data) -> tuple[bool, list[str]]:
    """校验 record dict，返回 (ok, errors)。errors 为人类可读字符串列表。"""
    c = _load_constraints()
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["record 必须是 JSON object"]

    # v3.1: revision 与 history 为可选 append 字段
    for field in c["required_fields"]:
        if field not in data:
            errors.append(f"missing required field: {field}")
    if errors:
        return False, errors

    if str(data.get("version")) != RECORD_VERSION:
        errors.append(f'version must be "{RECORD_VERSION}", got {data.get("version")!r}')

    if not str(data.get("id") or "").strip():
        errors.append("id must be non-empty")
    if not str(data.get("title") or "").strip():
        errors.append("title must be non-empty")

    tldr = str(data.get("tldr") or "").strip()
    if not tldr:
        errors.append("tldr must be non-empty")
    elif len(tldr) > c["tldr_max_chars"]:
        errors.append(f'tldr too long ({len(tldr)} > {c["tldr_max_chars"]} chars)')

    # v3.3：summary 为可选的段落式解读摘要（X/LinkedIn post 风格）
    summary = data.get("summary")
    if summary is not None:
        if not isinstance(summary, str):
            errors.append(f"summary must be a string, got {type(summary).__name__}")
        elif len(summary) > c.get("summary_max_chars", 600):
            errors.append(f'summary too long ({len(summary)} > {c.get("summary_max_chars", 600)} chars)')

    topic_type = str(data.get("topic_type") or "").strip().lower()
    if topic_type not in VALID_TOPIC_TYPES:
        errors.append(
            f"topic_type must be one of VALID_TOPIC_TYPES, got {data.get('topic_type')!r}"
        )

    tags = data.get("tags")
    if not isinstance(tags, list):
        errors.append("tags must be a list")
    else:
        tags = [t for t in tags if str(t).strip()]
        if not (c["tags_min"] <= len(tags) <= c["tags_max"]):
            errors.append(f'tags count {len(tags)} outside [{c["tags_min"]}, {c["tags_max"]}]')

    entities = data.get("entities")
    if not isinstance(entities, dict):
        errors.append("entities must be an object with 4 buckets")
    else:
        for bucket in c["entity_buckets"]:
            if bucket not in entities:
                errors.append(f"entities missing bucket: {bucket}")
            elif not isinstance(entities[bucket], list):
                errors.append(f"entities.{bucket} must be a list")

    source = data.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        for field in ("direct_source", "original_source"):
            v = source.get(field)
            if v is not None and not isinstance(v, str):
                errors.append(f"source.{field} must be a string, got {type(v).__name__}")

    links = data.get("links")
    if not isinstance(links, list):
        errors.append("links must be a list")
    else:
        kinds = link_kinds()
        roles = set(c["link_roles"])
        origins = set(c["link_origins"])
        seen_urls = set()
        canonical_kinds = set()
        for i, link in enumerate(links):
            if not isinstance(link, dict):
                errors.append(f"links[{i}] must be an object")
                continue
            url = str(link.get("url") or "").strip()
            if not _URL_RE.match(url):
                errors.append(f"links[{i}].url invalid: {url!r}")
            else:
                nu = normalize_url(url)
                if nu in seen_urls:
                    errors.append(f"links[{i}].url duplicate: {url}")
                seen_urls.add(nu)
            kind = link.get("kind", "other")
            if kind not in kinds:
                errors.append(f"links[{i}].kind unknown: {kind!r}")
            role = link.get("role", "related")
            if role not in roles:
                errors.append(f"links[{i}].role unknown: {role!r}")
            if role == "canonical":
                if kind in canonical_kinds:
                    errors.append(f"links[{i}]: duplicate canonical for kind {kind!r}")
                canonical_kinds.add(kind)
            origin = link.get("origin", "explicit")
            if origin not in origins:
                errors.append(f"links[{i}].origin unknown: {origin!r}")

    return (len(errors) == 0), errors


# ============================================================
# record.json 存取
# ============================================================

def load_record(slug: str, wiki_dir=None) -> dict | None:
    """读取 record.json；不存在或损坏返回 None。"""
    path = paths.record_path(slug, wiki_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_record(slug: str, wiki_dir, record: dict) -> Path:
    """写入 record.json（调用方负责先 validate）。"""
    path = paths.record_path(slug, wiki_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
