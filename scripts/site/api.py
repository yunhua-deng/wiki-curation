#!/usr/bin/env python3
"""
scripts/site/api.py — wiki 本地服务的薄 JSON API（纯函数，不起 socket）。

当前仅 record-links（add-link）与 watch 两个端点；serve.py 的 SPAHandler 只做 HTTP 壳。
"""
import re
from pathlib import Path

from scripts import paths

ID_RE = re.compile(r"^[\w-]+$")
LOOPBACK_IPS = {"127.0.0.1", "::1"}


def handle_add_link(wiki_dir, payload: dict, client_ip: str = "127.0.0.1"):
    """POST /api/record-links：手动添加链接到 record 图谱。返回 (http_code, json_dict)。"""
    from scripts.records import link_ops

    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may add links"}
    payload = payload or {}
    slug = str(payload.get("id") or "")
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\w-]+$"}
    url = str(payload.get("url") or "").strip()
    role = str(payload.get("role") or "related")
    ws = Path(wiki_dir)
    try:
        result = link_ops.add_manual_link(slug, url, role=role, ws=ws, db_path=paths.db_path(ws))
    except link_ops.LinkOpError as e:
        http = 404 if e.code == "RECORD_MISSING" else (409 if e.code in ("LINK_EXISTS", "CANONICAL_CONFLICT") else 400)
        return http, {"ok": False, "error": e.code, "message": str(e)}
    return 200, {"ok": True, **result}


def handle_watch(wiki_dir, payload: dict, client_ip: str = "127.0.0.1"):
    """POST /api/watch：设置/切换特别关注，并同步重建站点。返回 (http_code, json_dict)。"""
    from scripts.wiki_index import store

    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may toggle watch"}
    payload = payload or {}
    slug = str(payload.get("id") or "")
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\w-]+$"}
    ws = Path(wiki_dir)
    db = paths.db_path(ws)
    cur = store.get_entry(db, slug)
    if not cur:
        return 404, {"ok": False, "error": "ENTRY_NOT_FOUND",
                     "message": f"entry not found: {slug}"}
    target = bool(payload["on"]) if "on" in payload else not bool(cur.get("watched"))
    e = store.set_watched(db, slug, target)
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site
    with PublishLock(timeout=30):
        build_site(db, ws)
    return 200, {"ok": True, "id": slug, "watched": bool(e["watched"]),
                 "watched_at": e.get("watched_at") or ""}
