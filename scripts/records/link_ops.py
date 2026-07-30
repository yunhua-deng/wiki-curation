#!/usr/bin/env python3
"""
scripts/records/link_ops.py — 手动添加链接到 record 的链接图谱（确定性元数据操作）。

与 append（agent 内容合并）分层：这里只动 links 元数据——URL 校验、kind 按域名分类、
normalize_url 去重、links 表替换、relations 重织、站点重建；不触碰 tldr/summary/tags。
手动添加的链接以 origin="manual" 标记，区别于材料提取的 explicit/inferred。
"""
import re

from scripts import paths
from scripts.records import schema as RS
from scripts.records.schema import normalize_url, classify_link_kind


class LinkOpError(Exception):
    """带机器可读码的链接操作错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
VALID_ROLES = {"canonical", "related"}


def add_manual_link(slug: str, url: str, role: str = "related", ws=None, db_path=None) -> dict:
    """把一条手动发现的链接加入 record 的链接图谱并重建站点。

    Raises: LinkOpError(RECORD_MISSING / INVALID_URL / INVALID_ROLE /
            LINK_EXISTS / CANONICAL_CONFLICT)
    Returns: {"ok", "link", "links", "relations"}
    """
    from pathlib import Path
    from scripts.records import links as L
    from scripts.records import relations as REL
    from scripts import wiki_index

    ws = Path(ws) if ws is not None else paths.get_workspace()
    db_path = Path(db_path) if db_path is not None else paths.db_path(ws)

    record = RS.load_record(slug, ws)
    if not record:
        raise LinkOpError("RECORD_MISSING", f"record.json not found: {slug}")
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise LinkOpError("INVALID_URL", f"url must start with http(s)://: {url!r}")
    if role not in VALID_ROLES:
        raise LinkOpError("INVALID_ROLE", f"role must be one of {sorted(VALID_ROLES)}, got {role!r}")

    nu = normalize_url(url)
    links = record.get("links") or []
    for l in links:
        if normalize_url(str(l.get("url") or "")) == nu:
            raise LinkOpError("LINK_EXISTS", f"链接已存在: {l.get('url')}")
    kind = classify_link_kind(url)
    if role == "canonical" and any(l.get("kind") == kind and l.get("role") == "canonical" for l in links):
        raise LinkOpError("CANONICAL_CONFLICT",
                          f"kind={kind} 已有 canonical 链接；请以 role=related 添加，或与 agent 对话调整")

    link = {"url": url, "kind": kind, "role": role,
            "origin": "manual", "fetched": 0, "verified": None}
    links.append(link)
    record["links"] = links
    # 发布前再校验一次（origin=manual 已入 schema 枚举）
    ok, errors = RS.validate_record(record)
    if not ok:
        raise LinkOpError("VERIFY_FAILED", f"record 校验失败: {'; '.join(errors)}")
    RS.save_record(slug, ws, record)

    n_links = L.replace_links(db_path, slug, links)
    n_relations = REL.rewire_relations(db_path, slug)
    try:
        wiki_index.record_event(db_path, slug, "LINK_ADD", {"url": url, "role": role, "origin": "manual"})
    except Exception:
        pass

    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site
    with PublishLock(timeout=30):
        build_site(db_path, ws)

    return {"ok": True, "link": link, "links": n_links, "relations": n_relations}
