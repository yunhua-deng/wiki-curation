"""Tests for collect_materials.py (logic-only, no real network)."""
import json
import shutil
from unittest import mock

import pytest

from scripts import paths
from scripts.exec import collect_materials as cm


def test_extract_drill_targets():
    text = """
    论文在 https://arxiv.org/abs/2605.26112
    代码开源于 https://github.com/user/repo
    """
    targets = cm.extract_drill_targets(text, ["arxiv_paper", "github"])
    subtypes = [t["subtype"] for t in targets]
    assert "arxiv_paper" in subtypes
    assert "github" in subtypes


def test_fetch_results_round_trip(tmp_path):
    d = tmp_path
    cm._record_stage(d, "https://example.com", "curl", True, 0,
                     file_size=1024, download_time=1.23, error="", source_type="webpage")
    path = d / "_fetch_results.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["results"]) == 1
    assert data["results"][0]["url"] == "https://example.com"
    assert data["results"][0]["status"] == "success"


def test_handler_registry():
    """Handler registry contains core handlers keyed by handler name."""
    required = ["arxiv", "github", "weixin", "linkedin", "webpage", "search"]
    for h in required:
        assert h in cm.HANDLERS


def test_unknown_subtype(tmp_path):
    """未知 subtype 返回 failed 结果，不抛异常。"""
    d = tmp_path
    result = cm._run_handler("__nonexistent__", d, "https://example.com", "L1")
    assert result["status"] == "failed"


def test_collect_materials_structure(tmp_path, monkeypatch):
    """max_depth=1 avoids network drill."""
    orig_workspace = cm.WORKSPACE
    monkeypatch.setattr(cm, "WORKSPACE", tmp_path)
    try:
        log = cm.collect_materials(
            "test_dummy_output", "url", "arxiv",
            "https://arxiv.org/abs/2605.26112", max_depth=1
        )
        for key in ["slug", "started_at", "levels", "summary"]:
            assert key in log
    finally:
        monkeypatch.setattr(cm, "WORKSPACE", orig_workspace)
        shutil.rmtree(paths.raw_dir("test_dummy_output", tmp_path), ignore_errors=True)


def test_handler_linkedin_timeouts_and_commands(tmp_path, monkeypatch):
    """handler_linkedin 使用正确的外部命令和超时。"""
    calls = []

    def fake_run_cmd(cmd, timeout=None):
        calls.append((cmd, timeout))
        if cmd[0] == "openclaw":
            return {"ok": True, "exit_code": 0, "stdout": "tab: some-tab-id", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", "linkedin", "open"]:
            return {"ok": True, "exit_code": 0, "stdout": '{"page": "abc"}', "stderr": ""}
        if cmd == ["opencli", "browser", "linkedin", "extract"]:
            return {"ok": True, "exit_code": 0, "stdout": "# LinkedIn post\n\nSome content.", "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": ""}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    dest = tmp_path / "raw" / "2026-07-09_1260"
    result = cm.handler_linkedin(dest, "https://www.linkedin.com/in/example/")

    assert result["status"] == "success"
    assert result["files"] == ["linkedin_post.md"]
    assert (dest / "linkedin_post.md").exists()

    assert calls[0] == (["openclaw", "browser", "--browser-profile", "user", "tabs"], 30)
    assert calls[1][0] == ["opencli", "browser", "linkedin", "open", "https://www.linkedin.com/in/example/"]
    assert calls[1][1] == 30
    assert calls[2] == (["opencli", "browser", "linkedin", "extract"], 60)


def test_handler_linkedin_needs_browser_when_chrome_unavailable(tmp_path, monkeypatch):
    """当 openclaw browser tabs 失败时保持 needs_browser 状态。"""
    def fake_run_cmd(cmd, timeout=None):
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "timeout"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    dest = tmp_path / "raw" / "2026-07-09_1260"
    result = cm.handler_linkedin(dest, "https://www.linkedin.com/in/example/")

    assert result["status"] == "needs_browser"
    assert result["chrome_available"] is False


# ---------- 2026-09-14_001：SPA 外壳不得判 success ----------

SPA_SHELL_HTML = """<!doctype html>
<html><head><title>microSLAM</title>
<meta name="description" content="Turning plain monocular RGB videos into interactive RL environments.">
</head>
<body>
    <div id="root"></div>
    <script type="module" crossorigin src="/assets/index-D7zAKniS.js"></script>
</body></html>
"""


def _fake_curl(html: str, http_code: str = "200"):
    def _run(cmd, timeout=None):
        path = cmd[cmd.index("-o") + 1]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return {"ok": True, "exit_code": 0, "stdout": http_code, "stderr": ""}
    return _run


def _fetch_results(dest) -> list:
    return json.loads((dest / "_fetch_results.json").read_text(encoding="utf-8"))["results"]


def test_handler_webpage_spa_shell_is_failed(tmp_path, monkeypatch):
    """SPA 外壳（正文由 JS 渲染）→ failed + spa_shell 标记，不再判 success。"""
    monkeypatch.setattr(cm, "run_cmd", _fake_curl(SPA_SHELL_HTML))
    dest = tmp_path / "raw" / "spa"
    result = cm.handler_webpage(dest, "https://www.microagi.ai/articles/microslam")

    assert result["status"] == "failed"
    assert result["spa_shell"] is True
    assert result["visible_chars"] < cm.MIN_VISIBLE_CHARS
    assert "SPA 空壳" in result["error"]

    rec = _fetch_results(dest)[0]
    assert rec["status"] == "failed"
    assert rec["spa_shell"] is True
    assert rec["visible_chars"] == result["visible_chars"]
    assert rec["http_code"] == "200"


def test_handler_webpage_static_article_succeeds(tmp_path, monkeypatch):
    """普通静态正文页不受影响（不误杀）。"""
    body = "".join(f"<p>paragraph {i} about robot learning and VLA models.</p>" for i in range(40))
    html = f"<html><head><title>t</title></head><body><article>{body}</article></body></html>"
    monkeypatch.setattr(cm, "run_cmd", _fake_curl(html))
    dest = tmp_path / "raw" / "static"
    result = cm.handler_webpage(dest, "https://example.com/post")

    assert result["status"] == "success"
    assert result["files"] == ["webpage.html"]
    assert len(cm._visible_text(html)) >= cm.MIN_VISIBLE_CHARS
    assert _fetch_results(dest)[0]["status"] == "success"


def test_handler_webpage_short_text_without_spa_marker(tmp_path, monkeypatch):
    """正文过短但不是 SPA 形态 → failed，且不误标 spa_shell。"""
    padded = '<html><body><div class="' + "p" * 240 + '">短</div></body></html>'
    monkeypatch.setattr(cm, "run_cmd", _fake_curl(padded))
    dest = tmp_path / "raw" / "short"
    result = cm.handler_webpage(dest, "https://example.com/short")

    assert result["status"] == "failed"
    assert result["spa_shell"] is False
    assert "可见正文过短" in result["error"]


def test_handler_webpage_min_visible_chars_is_configurable(tmp_path, monkeypatch):
    """阈值走 settings.min_visible_chars，可按需放宽/收紧。"""
    monkeypatch.setattr(cm, "run_cmd", _fake_curl(SPA_SHELL_HTML))
    monkeypatch.setattr(cm.sc, "get_settings",
                        lambda cfg=None: {"fetch_timeout": 60, "min_visible_chars": 5})
    dest = tmp_path / "raw" / "threshold"
    result = cm.handler_webpage(dest, "https://example.com/spa")
    assert result["status"] == "success"


def test_handler_webpage_http_error_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "run_cmd", _fake_curl("<html><body>gone</body></html>", http_code="404"))
    dest = tmp_path / "raw" / "404"
    result = cm.handler_webpage(dest, "https://example.com/gone")
    assert result["status"] == "failed"
    assert _fetch_results(dest)[0]["http_code"] == "404"
