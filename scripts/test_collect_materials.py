"""Tests for collect_materials.py (logic-only, no real network)."""
import json
import shutil
import sys
import time
from pathlib import Path
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


# ---------- 2026-09-15_001：append 补料的落盘子目录 ----------

def _patch_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "WORKSPACE", tmp_path)
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp_path)


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["collect_materials.py"] + argv)
    cm.main()


def test_dest_subdir_places_drill_log_under_subdir(tmp_path, monkeypatch):
    """--dest-subdir append_1：drill log 落 raw/append_1/，不写到 raw/ 顶层。"""
    _patch_workspace(tmp_path, monkeypatch)
    sources = json.dumps([{"input_type": "local", "source_type": "local", "input": "x.pdf"}])
    _run_main(monkeypatch, ["--slug", "s", "--sources-json", sources,
                            "--dest-subdir", "append_1", "--json"])

    raw = paths.raw_dir("s", tmp_path)
    assert (raw / "append_1" / "_drill_log.json").exists()
    assert not (raw / "_drill_log.json").exists()


@pytest.mark.parametrize("bad", ["..", "../evil", "a/b", "a\\b", "C:evil", "", "  "])
def test_main_rejects_unsafe_dest_subdir(tmp_path, monkeypatch, capsys, bad):
    """--dest-subdir 只接受单层安全目录名（拒绝 .. 与路径分隔符）。"""
    _patch_workspace(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["--slug", "s", "--sources-json", "[]", "--dest-subdir", bad])
    assert exc.value.code == 2
    assert "--dest-subdir" in capsys.readouterr().err


def test_main_rejects_dest_subdir_in_single_source_mode(tmp_path, monkeypatch, capsys):
    _patch_workspace(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["--slug", "s", "--input-type", "url", "--source-type", "arxiv",
                                "--input", "https://arxiv.org/abs/2501.12345",
                                "--dest-subdir", "append_1"])
    assert exc.value.code == 2
    assert "--sources-json" in capsys.readouterr().err


def test_safe_subdir_accepts_plain_component():
    assert cm._safe_subdir("append_12") == "append_12"
    assert cm._safe_subdir(" append_1 ") == "append_1"
    assert cm._safe_subdir("a..b") is None


# ---------- 2026-09-15_004 / 2026-07-29_008：渲染必需来源判定 + 浏览器兜底 ----------

WEIXIN_URL = "https://mp.weixin.qq.com/s/vj05N6eXFuC30S2jw2DnMg"
HF_SPACE_URL = "https://huggingface.co/spaces/HuggingEnvs/geoguesser-article"

RENDERED_ARTICLE = ("<html><head><title>t</title></head><body><article>"
                    + "".join(f"<p>paragraph {i} about robot learning and VLA models.</p>"
                              for i in range(40))
                    + "</article></body></html>")
TINY_RENDERED = "<html><body><p>hi</p></body></html>"


def test_handler_browser_registered():
    assert cm.HANDLERS["browser"] is cm.handler_browser


@pytest.mark.parametrize("subtype,url,expected", [
    ("weixin", WEIXIN_URL, True),                       # subtype 命中
    ("weixin_paper", "https://example.com/a", True),    # 别名先解析回 weixin
    ("generic_web", WEIXIN_URL, True),                  # 仅域名命中
    ("linkedin", "https://www.linkedin.com/posts/yy", True),
    ("huggingface", HF_SPACE_URL, True),                # HF Space 外壳页
    ("hf_model_card", "https://huggingenvs-geoguesser-article.hf.space/", True),  # 容器地址
    ("generic_web", "https://example.com/app#!route", True),                      # hashbang SPA
    ("arxiv_paper", "https://arxiv.org/abs/2501.12345", False),
    ("github", "https://github.com/user/repo", False),
    ("generic_web", "https://example.com/post", False),
    ("generic_web", "https://notlinkedin.com/post", False),   # 域名不做整串子串匹配
    ("__nope__", "https://example.com/post", False),          # 未知 subtype 不炸
])
def test_is_render_required(subtype, url, expected):
    assert cm.is_render_required(subtype, url) is expected


def test_is_render_required_degrades_without_config(monkeypatch):
    """配置缺失 / 结构异常 / 读取报错 → 一律 False（不阻断普通来源）。"""
    assert cm.is_render_required("weixin", WEIXIN_URL, config={}) is False
    assert cm.is_render_required("weixin", WEIXIN_URL, config={"render_required": None}) is False
    assert cm.is_render_required("weixin", WEIXIN_URL, config={"render_required": "oops"}) is False
    assert cm.is_render_required("weixin", WEIXIN_URL,
                                 config={"render_required": {}}) is False

    monkeypatch.setattr(cm.sc, "load_config", lambda *a, **k: {})
    assert cm.is_render_required("weixin", WEIXIN_URL) is False

    def boom(*a, **k):
        raise RuntimeError("broken yaml")

    monkeypatch.setattr(cm.sc, "load_config", boom)
    assert cm.is_render_required("weixin", WEIXIN_URL) is False


def test_render_required_falls_back_to_browser(tmp_path, monkeypatch):
    """渲染必需 + 轻量路径全失败 → 浏览器兜底，level-1 成功且带 rendered 记录。"""
    _patch_workspace(tmp_path, monkeypatch)
    calls = []

    def fake_run_cmd(cmd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["opencli", "weixin"]:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}   # 0 字节
        if cmd[0] == "openclaw":
            return {"ok": True, "exit_code": 0, "stdout": "tab: abc", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": '{"page": "x", "http_code": 200}', "stderr": ""}
        if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
            return {"ok": True, "exit_code": 0, "stdout": RENDERED_ARTICLE, "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    log = cm.collect_materials("render_fallback", "url", "weixin", WEIXIN_URL, max_depth=1)

    raw = paths.raw_dir("render_fallback", tmp_path)
    l1 = log["levels"][0]["entries"][0]
    assert l1["status"] == "success"
    assert l1["files"] == ["weixin_rendered.html", "weixin_rendered.md"]
    assert (raw / "weixin_rendered.html").exists()
    assert (raw / "weixin_rendered.md").exists()
    assert log["summary"] == {"total_files": 2, "success": 1, "failed": 0, "needs_manual": 0}

    recs = _fetch_results(raw)
    assert [r["attempt"] for r in recs] == [1, 2, 3, 4]        # 3 次轻量 + 1 次渲染
    assert [r["file_size"] for r in recs if r["tool"] == "opencli_weixin"] == [0, 0, 0]
    rendered = recs[-1]
    assert rendered["tool"] == "browser" and rendered["rendered"] is True
    assert rendered["status"] == "success" and rendered["http_code"] == "200"
    assert rendered["file_size"] > 0 and rendered["text_bytes"] > 0
    assert ["opencli", "browser", cm.BROWSER_SESSION, "open", WEIXIN_URL] in calls


def test_render_required_all_paths_fail_surfaces_manual(tmp_path, monkeypatch):
    """两条路都没有正文 → 绝不 success，needs_manual 显式呈现，证据含 URL 与字节数。"""
    _patch_workspace(tmp_path, monkeypatch)

    def fake_run_cmd(cmd, timeout=None):
        if cmd[:2] == ["opencli", "weixin"]:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        if cmd[0] == "openclaw":
            return {"ok": True, "exit_code": 0, "stdout": "tab: abc", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": "HTTP 403", "stderr": ""}
        if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
            return {"ok": False, "exit_code": 4, "stdout": "", "stderr": "extract failed"}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    log = cm.collect_materials("render_fail", "url", "weixin", WEIXIN_URL, max_depth=1)

    raw = paths.raw_dir("render_fail", tmp_path)
    l1 = log["levels"][0]["entries"][0]
    assert l1["status"] == "needs_browser"
    assert l1["status"] != "success"
    assert l1["files"] == []
    assert "人工" in l1["note"]
    assert log["summary"]["success"] == 0
    assert log["summary"]["needs_manual"] == 1

    recs = _fetch_results(raw)
    assert all(r["url"] == WEIXIN_URL for r in recs)
    assert all("file_size" in r for r in recs)
    assert [r["attempt"] for r in recs] == [1, 2, 3, 4]
    assert recs[-1]["rendered"] is True
    assert recs[-1]["status"] == "failed"
    assert recs[-1]["http_code"] == "403"


def test_weixin_zero_byte_then_retry_succeeds(tmp_path, monkeypatch):
    """微信 0 字节 → 重试第二次拿到正文：success 且 _fetch_results.json 有两条 attempt。"""
    _patch_workspace(tmp_path, monkeypatch)
    calls = []
    body = "# WeChat article\n\n" + ("正文内容" * 200)

    def fake_run_cmd(cmd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["opencli", "weixin"]:
            if sum(1 for c in calls if c[:2] == ["opencli", "weixin"]) > 1:
                out = Path(cmd[cmd.index("--output") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "weixin_article.md").write_text(body, encoding="utf-8")
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no browser"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    log = cm.collect_materials("weixin_retry", "url", "weixin", WEIXIN_URL, max_depth=1)

    raw = paths.raw_dir("weixin_retry", tmp_path)
    l1 = log["levels"][0]["entries"][0]
    assert l1["status"] == "success"
    assert l1["files"] == ["weixin_article.md"]
    assert log["summary"]["success"] == 1

    recs = _fetch_results(raw)
    assert [r["attempt"] for r in recs] == [1, 2]
    assert recs[0]["status"] == "failed" and recs[0]["file_size"] == 0
    assert recs[1]["status"] == "success"
    assert recs[1]["file_size"] == (raw / "weixin_article.md").stat().st_size
    assert not any(c[0] == "openclaw" for c in calls)


def test_render_retry_gives_up_after_slow_attempt(tmp_path, monkeypatch):
    """一次尝试就耗尽 fetch_timeout 的一半 → 不再重试，把时间留给浏览器兜底。"""
    monkeypatch.setattr(cm.sc, "get_settings",
                        lambda cfg=None: {"fetch_timeout": 0.02, "min_visible_chars": 5,
                                          "render_retries": 5, "max_children_per_level": 5})
    calls = []

    def fake_run_cmd(cmd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["opencli", "weixin"]:
            time.sleep(0.05)
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        if cmd[0] == "openclaw":
            return {"ok": True, "exit_code": 0, "stdout": "tab: abc", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
            return {"ok": True, "exit_code": 0,
                    "stdout": "<html><body><p>hello world</p></body></html>", "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    dest = tmp_path / "raw" / "slow"
    result = cm._run_handler("weixin", dest, WEIXIN_URL, "L1-primary")

    assert sum(1 for c in calls if c[:2] == ["opencli", "weixin"]) == 1
    assert result["status"] == "success"
    assert [r["attempt"] for r in _fetch_results(dest)] == [1, 2]


def test_browser_rendered_text_threshold_decides(tmp_path, monkeypatch):
    """渲染产物按可见正文长度判成败：过短不得 success，达标才算。"""
    monkeypatch.setattr(cm.sc, "get_settings",
                        lambda cfg=None: {"min_visible_chars": 800, "max_children_per_level": 5})

    def fake_for(payload):
        def _run(cmd, timeout=None):
            if cmd[0] == "openclaw":
                return {"ok": True, "exit_code": 0, "stdout": "tab: abc", "stderr": ""}
            if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
                return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
            if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
                return {"ok": True, "exit_code": 0, "stdout": payload, "stderr": ""}
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "unexpected"}
        return _run

    tiny_dir = tmp_path / "raw" / "tiny"
    monkeypatch.setattr(cm, "run_cmd", fake_for(TINY_RENDERED))
    tiny = cm.handler_browser(tiny_dir, HF_SPACE_URL, file_stem="hf_model_card",
                              subtype="huggingface")
    assert tiny["status"] != "success"
    assert tiny["visible_chars"] < cm.MIN_VISIBLE_CHARS
    assert "渲染正文过短" in tiny["error"]
    assert tiny["files"] == []
    assert (tiny_dir / "hf_model_card_rendered.html").exists()
    assert (tiny_dir / "hf_model_card_rendered.md").exists()
    assert _fetch_results(tiny_dir)[-1]["rendered"] is True

    big_dir = tmp_path / "raw" / "big"
    monkeypatch.setattr(cm, "run_cmd", fake_for(RENDERED_ARTICLE))
    big = cm.handler_browser(big_dir, HF_SPACE_URL, file_stem="hf_model_card",
                             subtype="huggingface")
    assert big["status"] == "success"
    assert big["files"] == ["hf_model_card_rendered.html", "hf_model_card_rendered.md"]
    assert big["visible_chars"] >= cm.MIN_VISIBLE_CHARS


def test_plain_rendered_markdown_writes_only_md(tmp_path, monkeypatch):
    """extract 返回 markdown（无 HTML）时只落盘 <stem>_rendered.md。"""
    def fake_run_cmd(cmd, timeout=None):
        if cmd[0] == "openclaw":
            return {"ok": True, "exit_code": 0, "stdout": "tab: abc", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
        if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
            return {"ok": True, "exit_code": 0,
                    "stdout": json.dumps({"content": "x" * 900}), "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)
    dest = tmp_path / "raw" / "rendered_md"
    result = cm.handler_browser(dest, HF_SPACE_URL, file_stem="hf_model_card")

    assert result["status"] == "success"
    assert result["files"] == ["hf_model_card_rendered.md"]
    assert (dest / "hf_model_card_rendered.md").read_text(encoding="utf-8") == "x" * 900
    assert not (dest / "hf_model_card_rendered.html").exists()


def test_non_render_required_source_keeps_curl_path(tmp_path, monkeypatch):
    """非渲染必需来源（arxiv）：不碰浏览器，记录结构不变（无 attempt 字段）。"""
    calls = []

    def fake_run_cmd(cmd, timeout=None):
        calls.append(list(cmd))
        return {"ok": False, "exit_code": 7, "stdout": "", "stderr": "no network"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)

    dest = tmp_path / "raw" / "arxiv_only"
    result = cm._run_handler("arxiv_paper", dest, "https://arxiv.org/abs/2501.12345", "L1-primary")

    assert result["status"] == "failed"
    assert len(calls) == 3                                        # pdf x2 + abs，与今日一致
    assert all(c[0] == cm._curl() for c in calls)
    assert not any(c[0] in ("opencli", "openclaw") for c in calls)
    recs = _fetch_results(dest)
    assert recs
    assert all("attempt" not in r for r in recs)


# ---------- 配置→handler 契约：search_then_* / none / unknown ----------

def test_search_then_handlers_alias_to_search():
    """sources.yaml 的 search_then_* 组合 handler 名映射到既有 handler_search。"""
    assert cm.HANDLERS[cm.HANDLER_ALIASES["search_then_arxiv"]] is cm.handler_search
    assert cm.HANDLERS[cm.HANDLER_ALIASES["search_then_github"]] is cm.handler_search


@pytest.mark.parametrize("subtype", ["arxiv_title", "project_name"])
def test_run_handler_config_handlers_resolve(tmp_path, subtype):
    """声明了 search_then_* 的来源类型真正可解析，不再报 Unknown handler。"""
    result = cm._run_handler(subtype, tmp_path / subtype, "帮我找几篇论文推荐", "L1")
    assert "Unknown handler" not in (result.get("error") or "")
    assert result["subtype"] == "search"


def test_run_handler_none_is_skipped_local(tmp_path):
    """handler: none 的来源（local_file / multi_source）注册后返回 skipped_local，不写盘。"""
    dest = tmp_path / "ms"
    result = cm._run_handler("multi_source", dest, "x", "L1")
    assert result["status"] == "skipped_local"
    assert result["files"] == []
    assert not dest.exists()


def test_to_platform_subtype_unknown_falls_back_to_generic_web():
    """默认/未知平台值 unknown 解析为 generic_web，存量 source_type=unknown 不再 Unknown subtype。"""
    from scripts import source_config as sc
    assert sc.to_platform_subtype("unknown") == "generic_web"
    assert sc.resolve_subtype("unknown") == "generic_web"
    assert sc.resolve_subtype("") == "generic_web"
    assert sc.get_source_type("unknown") == sc.get_source_type("generic_web")


# ---------- 2026-09-15_005：可用性探测后端 ≠ 实际抓取后端 ----------

def test_browser_fetch_runs_opencli_even_when_probe_fails(tmp_path, monkeypatch):
    """openclaw 探测失败不得阻断 opencli 抓取（探测与抓取是两个后端）。

    修复前：探测失败即 return needs_browser，opencli 永不被调用；本机正是这种环境。
    """
    calls = []

    def fake_run_cmd(cmd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["openclaw", "browser"]:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no tabs"}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": '{"page": "abc"}', "stderr": ""}
        if cmd == ["opencli", "browser", cm.BROWSER_SESSION, "extract"]:
            return {"ok": True, "exit_code": 0, "stdout": RENDERED_ARTICLE, "stderr": ""}
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": ""}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)
    dest = tmp_path / "raw"
    with cm._fetch_attempt(1):
        result = cm.handler_browser(dest, HF_SPACE_URL, file_stem="hf_model_card", subtype="huggingface")

    assert ["opencli", "browser", cm.BROWSER_SESSION, "open", HF_SPACE_URL] in calls
    assert result["status"] == "success"
    assert result["chrome_available"] is False           # 探测确实失败，但不再拦路
    assert (dest / "hf_model_card_rendered.md").exists()
    rec = json.loads((dest / "_fetch_results.json").read_text(encoding="utf-8"))["results"][-1]
    assert rec["status"] == "success" and rec["rendered"] is True and rec["attempt"] == 1
    assert rec["probe"]                                   # 探测结论单独留痕
    assert rec["chrome_available"] is False


def test_browser_fetch_needs_browser_when_opencli_missing(tmp_path, monkeypatch):
    """opencli 根本跑不起来（exit -2）→ needs_browser，且证据能区分探测失败与抓取失败。"""
    def fake_run_cmd(cmd, timeout=None):
        if cmd[:2] == ["openclaw", "browser"]:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no tabs"}
        return {"ok": False, "exit_code": -2, "stdout": "", "stderr": "系统找不到指定的文件"}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)
    dest = tmp_path / "raw"
    result = cm.handler_browser(dest, HF_SPACE_URL, file_stem="hf_model_card", subtype="huggingface")

    assert result["status"] == "needs_browser"
    assert "opencli 不可用" in result["error"]
    rec = json.loads((dest / "_fetch_results.json").read_text(encoding="utf-8"))["results"][-1]
    assert rec["status"] == "failed" and rec["exit_code"] == -2
    assert "probe" in rec


def test_browser_fetch_extract_empty_is_distinguishable_from_probe_failure(tmp_path, monkeypatch):
    """页面打开成功但 extract 无正文：状态仍是 needs_browser（需人工），但证据与「探测失败」可区分。"""
    def fake_run_cmd(cmd, timeout=None):
        if cmd[:2] == ["openclaw", "browser"]:
            return {"ok": True, "exit_code": 0, "stdout": "tab: 1", "stderr": ""}
        if cmd[:4] == ["opencli", "browser", cm.BROWSER_SESSION, "open"]:
            return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
        return {"ok": True, "exit_code": 0, "stdout": "   ", "stderr": ""}

    monkeypatch.setattr(cm, "run_cmd", fake_run_cmd)
    dest = tmp_path / "raw"
    result = cm.handler_browser(dest, HF_SPACE_URL, file_stem="hf_model_card", subtype="huggingface")

    assert result["status"] == "needs_browser"
    assert result["chrome_available"] is True            # 探测正常 → 不是环境问题
    assert "提取为空" in result["error"]
    rec = json.loads((dest / "_fetch_results.json").read_text(encoding="utf-8"))["results"][-1]
    assert rec["probe"] == ""                            # 探测正常时留痕为空，与探测失败区分
    assert rec["chrome_available"] is True

