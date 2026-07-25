#!/usr/bin/env python3
"""
scripts/records/star_github.py — publish 后自动标星 canonical GitHub 仓库。

与 verify_links 同构：publish 保持纯本地确定性，标星是独立的懒式网络命令，
由编排层在 publish 成功后调用（cli.py star --id <slug>）。

凭据：GITHUB_TOKEN 环境变量（classic PAT + public_repo scope）。
fine-grained PAT（github_pat_...）不支持 starring API（403）。
"""
import json
import os
import re
import socket
import urllib.error
import urllib.request

GITHUB_HOST_RE = re.compile(r"^https?://github\.com/", re.IGNORECASE)
GIT_SUFFIX_RE = re.compile(r"\.git$", re.IGNORECASE)

# github.com 下的保留一级路径，不是仓库 owner
RESERVED_OWNERS = {
    "features", "topics", "marketplace", "orgs", "settings", "explore",
    "trending", "collections", "pricing", "about", "login", "join",
    "sponsors", "customer-stories", "readme", "search", "notifications",
}

TIMEOUT = int(os.environ.get("WIKI_STAR_TIMEOUT", "8"))


def parse_github_repo(url: str) -> str | None:
    """从 GitHub URL 提取 'owner/repo'；非仓库 URL 返回 None。

    取路径前两段作 owner/repo，更深的子路径（/tree/...、/issues）忽略，
    只取仓库根。剥离 query/fragment，repo 去尾部 .git。
    """
    if not url or not GITHUB_HOST_RE.match(url.strip()):
        return None
    rest = GITHUB_HOST_RE.sub("", url.strip())
    rest = rest.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], GIT_SUFFIX_RE.sub("", parts[1])
    if not owner or not repo:
        return None
    if owner.lower() in RESERVED_OWNERS:
        return None
    return f"{owner}/{repo}"


def extract_github_repos(record: dict) -> list[str]:
    """从 record.json 提取标星候选：direct_source + canonical github links。

    大小写不敏感去重（GitHub owner/repo 不区分大小写），保留首次出现的写法。
    """
    candidates = []
    source = record.get("source") or {}
    direct = source.get("direct_source")
    if isinstance(direct, str):
        candidates.append(direct)
    for link in record.get("links") or []:
        if link.get("kind") == "github" and link.get("role") == "canonical":
            u = link.get("url")
            if isinstance(u, str):
                candidates.append(u)

    seen = set()
    repos = []
    for u in candidates:
        r = parse_github_repo(u)
        if r and r.lower() not in seen:
            seen.add(r.lower())
            repos.append(r)
    return repos


def _request(method: str, url: str, token: str) -> int:
    """单次 HTTP 请求，返回状态码；网络异常抛出由调用方归类。"""
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wiki-curation-star")
    if method == "PUT":
        req.add_header("Content-Length", "0")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status


def star_repo(repo: str, token: str) -> str:
    """标星单个 repo，返回 'starred' | 'already' | 错误描述（以 'error:' 开头）。"""
    base = f"https://api.github.com/user/starred/{repo}"
    try:
        try:
            status = _request("GET", base, token)
            if status == 204:
                return "already"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pass  # 未标星 → 走 PUT
            else:
                return f"error: GET HTTP {e.code}"
        try:
            _request("PUT", base, token)
            return "starred"
        except urllib.error.HTTPError as e:
            return f"error: PUT HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"error: network: {e.reason}"
    except (TimeoutError, OSError, socket.timeout):
        return "error: timeout"


def star_repos(repos: list[str], token: str) -> dict:
    """批量标星，返回 {starred, already, failed} 三个列表。单 repo 失败不中断。"""
    starred, already, failed = [], [], []
    for repo in repos:
        result = star_repo(repo, token)
        if result == "starred":
            starred.append(repo)
        elif result == "already":
            already.append(repo)
        else:
            failed.append({"repo": repo, "error": result.removeprefix("error: ")})
    return {"starred": starred, "already": already, "failed": failed}


def star_entry(db_path, entry_id: str, record: dict) -> dict:
    """star 命令主逻辑：提取候选 → 标星 → 写 STAR 事件。"""
    from scripts import wiki_index

    repos = extract_github_repos(record)
    if not repos:
        return {"ok": True, "id": entry_id, "skipped": "no_github"}

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return {"ok": True, "id": entry_id, "skipped": "no_token", "repos": repos}

    result = star_repos(repos, token)
    out = {"ok": True, "id": entry_id, **result}
    try:
        wiki_index.record_event(db_path, entry_id, "STAR", {
            "starred": result["starred"],
            "already": result["already"],
            "failed": result["failed"],
        })
    except Exception:
        pass  # 事件写入失败不影响标星结果输出
    return out
