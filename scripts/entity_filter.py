#!/usr/bin/env python3
"""scripts/entity_filter.py — 实体抑制（suppress）与分组（group）共享逻辑。

单一实现，供 publish / entity_pages / site entities / entity_summary /
clean-entities 等所有消费方复用，避免各处独立实现再分裂。

抑制规则（按优先级）：
  1. 豁免：entity_aliases.yaml 任何 canonical key（terms / entities 各桶 /
     series_roots 的 key）或 entity_groups.yaml groups 显式列出的名字 → 永不抑制
     （防止 pattern 误杀 canonical）。
  2. suppress 精确名单（strip + casefold 后比较）→ 抑制。
  3. suppress_patterns 任一正则（re.IGNORECASE，search 语义）→ 抑制。
  4. 通用卫生规则（硬编码）：strip 后长度 ≤1 或 >50 → 抑制。

顺序约定：先 alias→canonical 映射，再判抑制（别名写法也能被 canonical 名的
suppress 覆盖）。canonicalize_entities() 是 publish 与 clean-entities 共用的
「alias 归一 + suppress」入口。
"""
import re
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REFERENCES_DIR = SCRIPT_DIR.parent / "references"
ALIASES_PATH = REFERENCES_DIR / "entity_aliases.yaml"
GROUPS_PATH = REFERENCES_DIR / "entity_groups.yaml"

ENTITY_BUCKETS = ("company", "author", "product", "series")
GROUP_VALUES = ("academia", "company", "oss", "product", "person")
MAX_NAME_LEN = 50

_CACHE = {}


def _casefold(name) -> str:
    return str(name or "").strip().casefold()


def _load_yaml(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# suppress
# ---------------------------------------------------------------------------
def _canonical_keys(aliases_data: dict) -> set:
    """aliases yaml 全部 canonical key（casefold）：terms / entities 各桶 / series_roots。"""
    keys = set()
    for canonical in (aliases_data.get("terms") or {}):
        keys.add(_casefold(canonical))
    for _etype, ent_map in (aliases_data.get("entities") or {}).items():
        for canonical in (ent_map or {}):
            keys.add(_casefold(canonical))
    for series in (aliases_data.get("series_roots") or {}):
        keys.add(_casefold(series))
    return keys


def build_suppression(aliases_data: dict, groups_data: dict) -> dict:
    """从已解析的 yaml dict 构建抑制配置（可测试的纯函数）。"""
    exempt = _canonical_keys(aliases_data)
    for name in ((groups_data or {}).get("groups") or {}):
        exempt.add(_casefold(name))
    names = {_casefold(n) for n in (aliases_data.get("suppress") or [])
             if str(n or "").strip()}
    names -= exempt  # canonical 豁免优先
    patterns = []
    for p in (aliases_data.get("suppress_patterns") or []):
        try:
            patterns.append(re.compile(str(p), re.IGNORECASE))
        except re.error:
            continue
    return {"names": names, "patterns": patterns, "exempt": exempt}


def load_suppression(aliases_path=None, groups_path=None) -> dict:
    """加载抑制配置（默认读 skill references/；带缓存）。"""
    aliases_path = Path(aliases_path) if aliases_path else ALIASES_PATH
    groups_path = Path(groups_path) if groups_path else GROUPS_PATH
    key = (str(aliases_path), str(groups_path))
    if key not in _CACHE:
        _CACHE[key] = build_suppression(_load_yaml(aliases_path), _load_yaml(groups_path))
    return _CACHE[key]


def is_suppressed(name, suppression: dict = None) -> bool:
    """判断实体名是否应被抑制。"""
    s = str(name or "").strip()
    if not s:
        return True
    if suppression is None:
        suppression = load_suppression()
    key = s.casefold()
    if key in suppression["exempt"]:
        return False
    if len(s) <= 1 or len(s) > MAX_NAME_LEN:
        return True
    if key in suppression["names"]:
        return True
    return any(p.search(s) for p in suppression["patterns"])


# ---------------------------------------------------------------------------
# alias 归一 + suppress（publish / clean-entities 共用）
# ---------------------------------------------------------------------------
def build_variant_map(aliases_data: dict) -> dict:
    """{variant_lower: canonical} 查找表（terms + entities 各桶）。"""
    variant_map = {}
    for canonical, variants in (aliases_data.get("terms") or {}).items():
        for v in [canonical] + (variants or []):
            variant_map[str(v).strip().lower()] = canonical
    for _etype, ent_map in (aliases_data.get("entities") or {}).items():
        for canonical, variants in (ent_map or {}).items():
            for v in [canonical] + (variants or []):
                variant_map[str(v).strip().lower()] = canonical
    return variant_map


def canonicalize_entities(entities: dict, aliases_data: dict = None,
                          suppression: dict = None) -> dict:
    """对四桶实体做 alias 归一，再丢弃被抑制名（去重保序）。"""
    if aliases_data is None:
        aliases_data = _load_yaml(ALIASES_PATH)
    if suppression is None:
        suppression = load_suppression()
    variant_map = build_variant_map(aliases_data)
    result = {}
    for bucket in ENTITY_BUCKETS:
        vals = (entities or {}).get(bucket) or []
        seen = []
        for v in vals:
            c = variant_map.get(str(v).strip().lower(), str(v).strip())
            if c and not is_suppressed(c, suppression) and c not in seen:
                seen.append(c)
        result[bucket] = seen
    return result


# ---------------------------------------------------------------------------
# 五类分组（entity_groups.yaml，支持多分组：值为字符串或字符串列表）
# ---------------------------------------------------------------------------
def load_entity_groups(path=None) -> dict:
    """加载分组配置：{groups: {name_casefold: [group...]}, academia_keywords: [...]}。

    groups 的值允许字符串或字符串列表（YAML list），统一归一化为列表。
    """
    data = _load_yaml(Path(path) if path else GROUPS_PATH)
    groups = {}
    for name, value in (data.get("groups") or {}).items():
        values = value if isinstance(value, list) else [value]
        valid = []
        for v in values:
            g = str(v or "").strip()
            if g in GROUP_VALUES and g not in valid:
                valid.append(g)
        if valid:
            groups[_casefold(name)] = valid
    keywords = [str(k).casefold() for k in (data.get("academia_keywords") or [])
                if str(k or "").strip()]
    return {"groups": groups, "academia_keywords": keywords}


def entity_groups_for(name, bucket: str, cfg: dict = None) -> list:
    """实体分组列表（允许重叠，如 [oss, product] / [company, academia]）。

    显式 groups 映射优先；否则按默认规则产生单元素列表：
    bucket=author → [person]；bucket=company → 命中 academia_keywords（大小写不敏感
    子串）则 [academia] 否则 [company]；bucket=product/series → [product]。
    """
    if cfg is None:
        cfg = load_entity_groups()
    g = cfg["groups"].get(_casefold(name))
    if g:
        return list(g)
    if bucket == "author":
        return ["person"]
    if bucket == "company":
        n = str(name or "").casefold()
        if any(k in n for k in cfg["academia_keywords"]):
            return ["academia"]
        return ["company"]
    return ["product"]


def entity_group(name, bucket: str, cfg: dict = None) -> str:
    """向后兼容的单值接口：返回 entity_groups_for() 的第一个分组。"""
    return entity_groups_for(name, bucket, cfg)[0]
