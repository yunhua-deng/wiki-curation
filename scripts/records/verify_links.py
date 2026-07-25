#!/usr/bin/env python3
"""
scripts/records/verify_links.py — links 表 URL 可达性懒验证（verify-links 命令）。

网络操作被显式隔离在本模块；publish 等主路径不做网络请求。
"""
import os
import sys

from scripts.lib import run_cmd
from scripts.records import links as L

DEFAULT_TIMEOUT = 10
DEFAULT_LIMIT = 20


def _curl() -> str:
    return "curl.exe" if os.name == "nt" else "curl"


def check_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """HEAD 请求判定可达性：2xx/3xx 与 403（反爬但存在）视为可达。"""
    r = run_cmd(
        [_curl(), "-sI", "--http1.1", "-o", os.devnull, "-w", "%{http_code}",
         "--max-time", str(timeout), url],
        timeout=timeout + 5,
    )
    code = (r.get("stdout") or "").strip()[-3:]
    if not code.isdigit():
        return False
    code = int(code)
    return code < 400 or code == 403


def verify_entry_links(db_path, entry_id, timeout: int = DEFAULT_TIMEOUT,
                       limit: int = DEFAULT_LIMIT) -> dict:
    """验证该 entry 所有未验证（verified IS NULL）的链接，回填 verified 标志。"""
    pending = [l for l in L.get_links(db_path, entry_id) if l.get("verified") is None]
    checked = ok_count = fail_count = 0
    results = []
    for link in pending[:limit]:
        reachable = False
        try:
            reachable = check_url(link["url"], timeout=timeout)
        except Exception:
            reachable = False
        L.set_link_verified(db_path, entry_id, link["url"], 1 if reachable else 0)
        checked += 1
        ok_count += 1 if reachable else 0
        fail_count += 0 if reachable else 1
        results.append({"url": link["url"], "verified": reachable})
    return {"id": entry_id, "checked": checked, "ok": ok_count,
            "fail": fail_count, "remaining": max(0, len(pending) - checked),
            "results": results}
