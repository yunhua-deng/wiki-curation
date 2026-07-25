#!/usr/bin/env python3
"""
source_config.py — Load and query references/sources.yaml.
Single source of truth for source types, classification, fetching, and checklists.
"""
import re
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path(__file__).resolve().parent.parent / "references" / "sources.yaml"


def _load_yaml(path: Path) -> dict:
    import yaml
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_config(path: Optional[Path] = None) -> dict:
    """Load sources.yaml."""
    return _load_yaml(path or CONFIG_PATH)


def get_aliases(config: Optional[dict] = None) -> dict:
    """Return alias → subtype mapping."""
    cfg = config or load_config()
    return cfg.get('aliases', {})


def resolve_subtype(subtype: str, config: Optional[dict] = None) -> str:
    """Resolve alias or legacy name to canonical subtype."""
    aliases = get_aliases(config)
    return aliases.get(subtype, subtype)


PLATFORM_NAMES = {
    'arxiv', 'github', 'weixin', 'huggingface', 'linkedin', 'zhihu', 'reddit',
    'twitter_x', 'youtube', 'bilibili', 'podcast', 'blog', 'news', 'webpage',
    'docs', 'company', 'product', 'project', 'researcher', 'concept',
    'comparison', 'trend', 'local', 'multi_source', 'unknown',
}


def to_platform_subtype(platform: str, config: Optional[dict] = None) -> str:
    """把存储用的平台值（如 arxiv）转回实际抓取的 canonical subtype（如 arxiv_paper）。"""
    return resolve_subtype(platform, config)


def resolve_platform(subtype: str, config: Optional[dict] = None) -> str:
    """把 canonical subtype 映射回用于存储的平台名。"""
    if subtype in PLATFORM_NAMES:
        return subtype
    aliases = get_aliases(config)
    for alias, canonical in aliases.items():
        if canonical == subtype and alias in PLATFORM_NAMES:
            return alias
    return subtype


def get_source_types(config: Optional[dict] = None) -> dict:
    """Return canonical source_type definitions."""
    cfg = config or load_config()
    return cfg.get('source_types', {})


def get_settings(config: Optional[dict] = None) -> dict:
    cfg = config or load_config()
    return cfg.get('settings', {})


def get_source_type(subtype: str, config: Optional[dict] = None) -> Optional[dict]:
    """Get definition for a canonical subtype."""
    subtype = resolve_subtype(subtype, config)
    return get_source_types(config).get(subtype)


def classify_url(url: str, config: Optional[dict] = None) -> Optional[dict]:
    """Classify a URL using sources.yaml rules. Returns (subtype, definition, extras)."""
    from urllib.parse import urlparse
    cfg = config or load_config()
    types = get_source_types(cfg)
    parsed = urlparse(url)
    domain = parsed.netloc.lower().strip()
    if domain.startswith('www.'):
        domain = domain[4:]
    path = parsed.path.lower()

    best = None
    best_priority = float('inf')

    for subtype, defn in types.items():
        cls = defn.get('classification', {})
        if cls.get('input_type') != 'url':
            continue
        if cls.get('fallback'):
            priority = 999
        else:
            priority = 0

        matched = False
        # Exact domains
        for d in cls.get('domains', []):
            dnorm = d.lower().strip()
            if dnorm in domain:
                path_patterns = cls.get('path_patterns', [])
                if path_patterns:
                    if any(p in path for p in path_patterns):
                        matched = True
                        break
                else:
                    matched = True
                    break

        # Fuzzy domains
        if not matched:
            for pattern in cls.get('fuzzy_domains', []):
                if re.search(pattern, domain, re.I):
                    matched = True
                    break

        if matched and priority < best_priority:
            best_priority = priority
            extras = {}
            for name, pat in cls.get('extract_patterns', {}).items():
                m = re.search(pat, url, re.I)
                if m:
                    extras[name] = m.group(1)
            best = (subtype, defn, extras)

    if best is None:
        # Fallback to generic_web
        generic = types.get('generic_web')
        if generic:
            return ('generic_web', generic, {})
    return best


def classify_non_url(text: str, config: Optional[dict] = None) -> list[dict]:
    """Classify non-URL text using keyword / entity / abbreviation rules."""
    cfg = config or load_config()
    types = get_source_types(cfg)
    results = []

    # arXiv ID pattern
    for subtype, defn in types.items():
        cls = defn.get('classification', {})
        if cls.get('input_type') != 'keywords':
            continue
        for pat in cls.get('patterns', []):
            for m in re.finditer(pat, text, re.I):
                results.append({
                    'subtype': subtype,
                    'definition': defn,
                    'confidence': 'high',
                    'matched_rule': f'pattern:{subtype}',
                    'source_text': m.group(0),
                    'extras': {k: v for k, v in m.groupdict().items() if v},
                })

    # Keywords / entities / abbreviations
    keyword_hits = []
    for subtype, defn in types.items():
        cls = defn.get('classification', {})
        if cls.get('input_type') != 'keywords':
            continue
        priority = 50
        matched = False
        source_text = ''

        for kw in cls.get('keywords', []):
            if re.search(kw, text, re.I):
                matched = True
                source_text = kw
                priority = min(priority, 20)

        for ent in cls.get('named_entities', []):
            if re.search(r'\b' + re.escape(ent) + r'\b', text, re.I):
                matched = True
                source_text = ent
                priority = min(priority, 15)

        for abbr in cls.get('abbreviations', []):
            if re.search(r'\b' + re.escape(abbr) + r'\b', text, re.I):
                matched = True
                source_text = abbr
                priority = min(priority, 25)

        if matched:
            keyword_hits.append((priority, subtype, defn, source_text))

    # Deduplicate and sort by priority
    seen = {r['subtype'] for r in results}
    keyword_hits.sort(key=lambda x: x[0])
    for priority, subtype, defn, source_text in keyword_hits:
        if subtype in seen:
            continue
        seen.add(subtype)
        confidence = 'medium' if priority < 50 else 'low'
        results.append({
            'subtype': subtype,
            'definition': defn,
            'confidence': confidence,
            'matched_rule': f'keyword:{subtype}',
            'source_text': source_text,
            'extras': {},
        })

    return results


def get_label_cn(subtype: str, config: Optional[dict] = None) -> str:
    defn = get_source_type(subtype, config)
    return defn.get('label_cn', subtype) if defn else subtype
