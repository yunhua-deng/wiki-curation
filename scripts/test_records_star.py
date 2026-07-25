#!/usr/bin/env python3
"""test_records_star.py — star_github 提取规则与标星逻辑契约测试（无网络）。"""
import urllib.error

from scripts.records.star_github import (
    extract_github_repos,
    parse_github_repo,
    star_entry,
    star_repo,
    star_repos,
)


# ============================================================
# parse_github_repo
# ============================================================

def test_parse_plain_repo():
    assert parse_github_repo("https://github.com/Ropedia/S-Agent") == "Ropedia/S-Agent"


def test_parse_strips_subpath_query_fragment():
    assert parse_github_repo("https://github.com/a/b/tree/main") == "a/b"
    assert parse_github_repo("https://github.com/a/b/issues") == "a/b"
    assert parse_github_repo("https://github.com/a/b?tab=readme#x") == "a/b"
    assert parse_github_repo("https://github.com/a/b/") == "a/b"


def test_parse_strips_git_suffix():
    assert parse_github_repo("https://github.com/a/b.git") == "a/b"


def test_parse_rejects_non_github_and_non_repo():
    assert parse_github_repo("https://arxiv.org/abs/1234.5678") is None
    assert parse_github_repo("https://github.com/features") is None
    assert parse_github_repo("https://github.com/topics/robotics") is None
    assert parse_github_repo("https://github.com/owneronly") is None
    assert parse_github_repo("") is None
    assert parse_github_repo(None) is None


# ============================================================
# extract_github_repos
# ============================================================

def _record(direct=None, links=None):
    return {
        "source": {"direct_source": direct or ""},
        "links": links or [],
    }


def test_extract_from_direct_source():
    rec = _record(direct="https://github.com/a/b")
    assert extract_github_repos(rec) == ["a/b"]


def test_extract_canonical_links_only():
    rec = _record(links=[
        {"url": "https://github.com/a/b", "kind": "github", "role": "canonical"},
        {"url": "https://github.com/c/d", "kind": "github", "role": "related"},
        {"url": "https://arxiv.org/abs/1.2", "kind": "arxiv", "role": "canonical"},
    ])
    assert extract_github_repos(rec) == ["a/b"]


def test_extract_dedup_case_insensitive():
    rec = _record(
        direct="https://github.com/A/B",
        links=[{"url": "https://github.com/a/b", "kind": "github", "role": "canonical"}],
    )
    assert extract_github_repos(rec) == ["A/B"]


def test_extract_empty_record():
    assert extract_github_repos({}) == []
    assert extract_github_repos(_record()) == []


# ============================================================
# star_repo / star_repos（mock urlopen）
# ============================================================

class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def test_star_repo_already(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(204))
    assert star_repo("a/b", "tok") == "already"


def test_star_repo_new(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.get_method())
        if req.get_method() == "GET":
            raise _http_error(404)
        return _Resp(204)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert star_repo("a/b", "tok") == "starred"
    assert calls == ["GET", "PUT"]


def test_star_repo_put_404(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(404)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert star_repo("a/b", "tok") == "error: PUT HTTP 404"


def test_star_repo_unauthorized(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(401)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert star_repo("a/b", "tok") == "error: GET HTTP 401"


def test_star_repo_timeout(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise TimeoutError()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert star_repo("a/b", "tok") == "error: timeout"


def test_star_repos_continues_after_failure(monkeypatch):
    outcomes = {"a/b": "starred", "x/y": "error: PUT HTTP 404", "c/d": "already"}
    monkeypatch.setattr("scripts.records.star_github.star_repo",
                        lambda repo, token: outcomes[repo])
    result = star_repos(["a/b", "x/y", "c/d"], "tok")
    assert result["starred"] == ["a/b"]
    assert result["already"] == ["c/d"]
    assert result["failed"] == [{"repo": "x/y", "error": "PUT HTTP 404"}]


# ============================================================
# star_entry（CLI 主逻辑，mock DB 事件）
# ============================================================

def test_star_entry_no_github(tmp_path):
    db = tmp_path / "wiki.db"
    out = star_entry(db, "e1", _record(links=[
        {"url": "https://arxiv.org/abs/1.2", "kind": "arxiv", "role": "canonical"},
    ]))
    assert out == {"ok": True, "id": "e1", "skipped": "no_github"}


def test_star_entry_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rec = _record(direct="https://github.com/a/b")
    out = star_entry(tmp_path / "wiki.db", "e1", rec)
    assert out["skipped"] == "no_token"
    assert out["repos"] == ["a/b"]
    assert out["ok"] is True


def test_star_entry_success_records_event(tmp_path, monkeypatch):
    from scripts.wiki_index.schema import ensure_schema
    db = tmp_path / "wiki.db"
    ensure_schema(db)

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr("scripts.records.star_github.star_repo",
                        lambda repo, token: "starred")
    rec = _record(direct="https://github.com/a/b")
    out = star_entry(db, "e1", rec)
    assert out["starred"] == ["a/b"]
    assert out["failed"] == []

    import sqlite3
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT action, detail FROM events WHERE slug='e1' AND action='STAR'").fetchall()
    conn.close()
    assert len(rows) == 1
    import json
    detail = json.loads(rows[0][1])
    assert detail["starred"] == ["a/b"]
