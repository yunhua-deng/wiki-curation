#!/usr/bin/env python3
"""
素材收集器 — 配置驱动，递归下钻，落盘 raw/。

来源策略统一来自 references/sources.yaml。
"""
import json
import os
import re
import sys
import time
import argparse
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# 本脚本位于 exec/，需要 scripts/ 根目录才能导入 lib、source_config 等公共模块

from scripts import source_config as sc
from scripts import paths
from scripts.lib import run_cmd


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = paths.get_workspace()
DRILL_LOG_FILE = "_drill_log.json"
FETCH_RESULTS_FILE = "_fetch_results.json"


ARXIV_ID_RE = re.compile(r'arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})', re.I)
ARXIV_ID_RAW_RE = re.compile(r'\b(\d{4}\.\d{4,5})(?:v\d+)?\b')
GITHUB_URL_RE = re.compile(r'https?://github\.com/[\w.-]+/[\w.-]+', re.I)
HF_URL_RE = re.compile(r'https?://huggingface\.co/[\w.-]+/[\w.-]+', re.I)


def _curl() -> str:
    """跨平台 curl 命令：Windows 优先 curl.exe。"""
    return "curl.exe" if os.name == "nt" else "curl"


def _append_fetch_result(dest_dir: Path, result: dict):
    """追加一条抓取结果到 _fetch_results.json。"""
    path = dest_dir / FETCH_RESULTS_FILE
    results = []
    if path.exists():
        try:
            results = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("results", [])
        except Exception:
            pass
    results.append(result)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


# 当前抓取尝试序号（只有渲染必需来源的重试/兜底序列会填充）：
# 填充时 _record_stage 会把 attempt 写进 fetch 记录，0 字节的失败尝试因此可复核。
_FETCH_ATTEMPT: dict = {}


@contextmanager
def _fetch_attempt(index: int):
    """把 handler 的这次调用标为第 index 次尝试（记录进 _fetch_results.json）。"""
    previous = _FETCH_ATTEMPT.get("index")
    _FETCH_ATTEMPT["index"] = index
    try:
        yield
    finally:
        if previous is None:
            _FETCH_ATTEMPT.pop("index", None)
        else:
            _FETCH_ATTEMPT["index"] = previous


def _record_stage(dest_dir: Path, url: str, tool: str, ok: bool, exit_code: int,
                  file_size: int = 0, download_time: float = 0.0, error: str = "",
                  source_type: str = "", extra: dict | None = None):
    record = {
        "url": url,
        "tool": tool,
        "exit_code": exit_code,
        "file_size": file_size,
        "download_time": round(download_time, 3),
        "error": error[:200],
        "status": "success" if ok else "failed",
        "source_type": source_type,
    }
    if "index" in _FETCH_ATTEMPT:
        record["attempt"] = _FETCH_ATTEMPT["index"]
    if extra:
        record.update(extra)
    _append_fetch_result(dest_dir, record)


def _merge_fetch_results(src_dir: Path, dest_dir: Path, source_index: int = 0):
    """把 src_dir/_fetch_results.json 合并到 dest_dir/_fetch_results.json。"""
    src = src_dir / FETCH_RESULTS_FILE
    if not src.exists():
        return
    dest = dest_dir / FETCH_RESULTS_FILE
    results = []
    if dest.exists():
        try:
            results = json.loads(dest.read_text(encoding="utf-8", errors="replace")).get("results", [])
        except Exception:
            pass
    try:
        extra = json.loads(src.read_text(encoding="utf-8", errors="replace")).get("results", [])
    except Exception:
        return
    for r in extra:
        r["source_index"] = source_index
        results.append(r)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# URL extraction for drill-down
# ============================================================

def extract_drill_targets(text: str, allowed_types: list[str], exclude_ids: set = None) -> list[dict]:
    """从文本中提取 allowed_types 内的可下钻目标。"""
    results = []
    seen = set()
    exclude_ids = exclude_ids or set()
    config = sc.load_config()

    def add(subtype, input_val, label, from_level):
        if input_val in seen:
            return
        seen.add(input_val)
        results.append({
            "type": "url", "subtype": subtype, "input": input_val,
            "label": label, "from_level": from_level,
        })

    if "arxiv_paper" in allowed_types:
        for m in ARXIV_ID_RE.finditer(text):
            aid = m.group(1)
            if aid not in exclude_ids:
                add("arxiv_paper", f"https://arxiv.org/abs/{aid}", f"arXiv:{aid}", "content_regex")

    if "github" in allowed_types:
        for m in GITHUB_URL_RE.finditer(text):
            url = m.group(0).rstrip('._-)').rstrip(')')
            add("github", url, f"GitHub:{url.split('/')[-1]}", "content_regex")

    if "huggingface" in allowed_types:
        for m in HF_URL_RE.finditer(text):
            url = m.group(0).rstrip('._-).')
            add("huggingface", url, f"HF:{url.split('/')[-1]}", "content_regex")

    max_children = sc.get_settings(config).get('max_children_per_level', 5)
    return results[:max_children]


# ============================================================
# 渲染必需来源：判定 + 浏览器抓取（共用）
#
# 登录态 / JS 渲染 / 反爬 / 容器型应用的正文无法靠无 JS 的简单 HTTP 抓取稳定获得
# （见 wiki/docs/issues/2026-09-15_004）。判定来自 sources.yaml 的 render_required 块，
# 命中后先走既有轻量路径，失败再回退到这里。
# ============================================================

BROWSER_SESSION = "wiki"          # opencli browser 的 session 名（任意，不是子命令）
BROWSER_PROBE_CMD = ["openclaw", "browser", "--browser-profile", "user", "tabs"]
BROWSER_PROBE_TIMEOUT = 30        # openclaw 连接初始化可能需 15-20 秒
BROWSER_OPEN_TIMEOUT = 30
BROWSER_EXTRACT_TIMEOUT = 60      # 页面加载 + 提取可能较慢
RENDER_RETRIES_DEFAULT = 2        # settings.render_retries 缺省值（重试次数，不含首次）
RENDER_SUFFIX_HTML = "_rendered.html"
RENDER_SUFFIX_MD = "_rendered.md"


def _render_required_config(config: dict | None = None) -> dict:
    """读取 sources.yaml 的 render_required 块；缺失或损坏一律视为空（不抛异常）。"""
    try:
        cfg = config if config is not None else sc.load_config()
        block = (cfg or {}).get('render_required') or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def _url_host(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def is_render_required(subtype: str, url: str = "", config: dict | None = None) -> bool:
    """该来源是否必须经浏览器渲染 / 登录态才能拿到正文。

    判定维度：source subtype → URL 域名（含子域）→ URL 路径片段 / 标记（如 `#!`）。
    配置缺失、字段缺失或配置损坏一律返回 False（宁可不兜底，也不阻断普通来源）。
    """
    block = _render_required_config(config)
    if not block:
        return False
    try:
        canonical = sc.resolve_subtype(subtype, config) if subtype else ""
        subtypes = {str(s).strip().lower() for s in block.get('subtypes') or []}
        if canonical and str(canonical).strip().lower() in subtypes:
            return True

        url_l = (url or "").strip().lower()
        if not url_l:
            return False

        host = _url_host(url_l)
        for dom in block.get('domains') or []:
            d = str(dom).strip().lower()
            if d and (host == d or host.endswith('.' + d)):
                return True

        for pat in list(block.get('path_patterns') or []) + list(block.get('url_markers') or []):
            p = str(pat).strip().lower()
            if p and p in url_l:
                return True
    except Exception:
        return False
    return False


def _http_code_from_text(text: str) -> str:
    """尽力从 CLI/浏览器输出里取真实 HTTP 状态码；取不到返回空串（不猜）。"""
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        for key in ("http_code", "httpCode", "status_code", "statusCode", "status"):
            value = data.get(key)
            if isinstance(value, int) and 100 <= value <= 599:
                return str(value)
            if isinstance(value, str) and len(value) == 3 and value.isdigit() and value[0] in "12345":
                return value
    m = re.search(r'\b([1-5]\d{2})\b', text)
    return m.group(1) if m else ""


def _browser_available() -> tuple[bool, dict]:
    """探测 openclaw 浏览器可用性，返回 (可用, 原始 run_cmd 结果)。"""
    r = run_cmd(list(BROWSER_PROBE_CMD), timeout=BROWSER_PROBE_TIMEOUT)
    return bool(r.get("ok") and "tab:" in (r.get("stdout") or "")), r


def _min_visible_chars() -> int:
    try:
        return int(sc.get_settings().get('min_visible_chars', MIN_VISIBLE_CHARS))
    except Exception:
        return MIN_VISIBLE_CHARS


def _record_browser_stage(dest_dir: Path, url: str, *, ok: bool, exit_code: int, size: int,
                          elapsed: float, error: str, source_type: str, session: str,
                          http_code: str = "", visible_chars: int = 0, html_bytes: int = 0,
                          text_bytes: int = 0, chrome_available: bool = True, probe: str = ""):
    """把一次浏览器抓取尝试记进 _fetch_results.json（rendered=true + 真实字节数）。

    `probe` 记录这次尝试开始时 `openclaw browser tabs` 的探测结论：它**不是**抓取后端，
    只用于把「探测失败」与「抓取失败」区分开。
    """
    _record_stage(dest_dir, url, "browser", ok, exit_code, size, elapsed, error, source_type,
                  extra={"rendered": True, "session": session, "http_code": http_code,
                         "visible_chars": visible_chars, "html_bytes": html_bytes,
                         "text_bytes": text_bytes, "chrome_available": chrome_available,
                         "probe": probe})


def _browser_fetch(dest_dir: Path, url: str, *, html_name: str, md_name: str,
                   session: str = BROWSER_SESSION, source_type: str = "",
                   min_visible: int | None = None, note: str = "") -> dict:
    """用浏览器抓取页面：opencli browser open / extract。

    探测（`openclaw browser tabs`）与抓取（`opencli browser`）**不是同一后端**，因此探测
    只作为证据记录（`probe` 字段），**不再作为闸门**——否则 opencli 明明可用也会被判成
    「浏览器不可用」。最终判定按实际抓取后端：opencli 无法执行（spawn 失败 exit=-2）
    → status="needs_browser"；open/extract 失败或正文过短 → status="failed"。

    落盘 `<html_name>`（extract 返回 HTML 时的原始 HTML）与 `<md_name>`（纯文本；
    extract 直接返回 markdown 时原样写入）。成功与否只看可见正文字符数
    （`_visible_text` + `min_visible`，默认 settings.min_visible_chars），从不看「文件存在」。
    """
    result = {"status": "needs_browser", "files": [], "chrome_available": False,
              "rendered": False, "visible_chars": 0, "text": "", "error": "", "note": note}
    start = time.time()

    probe_ok, probe = _browser_available()
    result["chrome_available"] = probe_ok
    probe_note = "" if probe_ok else (
        "openclaw 探测未返回标签页（仅诊断，不阻断 opencli 抓取）"
        + (probe.get("stderr") or "")[:80])

    dest_dir.mkdir(parents=True, exist_ok=True)
    r_open = run_cmd(["opencli", "browser", session, "open", url], timeout=BROWSER_OPEN_TIMEOUT)
    exit_code = r_open.get("exit_code", -1)
    http_code = _http_code_from_text(r_open.get("stdout") or "")
    if not r_open.get("ok"):
        # 打不开页面 = 浏览器侧不可用（opencli 缺失 exit=-2 / 无法起标签页）→ needs_browser；
        # 「抓取失败」（打开了但 extract 没内容）才判 failed，两者在证据里可区分。
        env_unavailable = exit_code == -2
        result["status"] = "needs_browser"
        result["error"] = (f"opencli 不可用（exit {exit_code}），浏览器路径无法启用"
                           if env_unavailable
                           else f"opencli browser {session} open 失败（exit {exit_code}）：浏览器/标签页不可用")
        result["note"] = note or result["error"] + "，需人工介入"
        _record_browser_stage(dest_dir, url, ok=False, exit_code=exit_code, size=0,
                              elapsed=time.time() - start,
                              error=result["error"] + (r_open.get("stderr") or "")[:100],
                              source_type=source_type, session=session, http_code=http_code,
                              chrome_available=probe_ok, probe=probe_note)
        return result

    r_extract = run_cmd(["opencli", "browser", session, "extract"], timeout=BROWSER_EXTRACT_TIMEOUT)
    exit_code = r_extract.get("exit_code", exit_code)
    payload = r_extract.get("stdout") or ""
    if not r_extract.get("ok") or not payload.strip():
        result["error"] = f"浏览器提取为空（exit {exit_code}）"
        result["note"] = note or result["error"] + "，需人工介入"
        _record_browser_stage(dest_dir, url, ok=False, exit_code=exit_code, size=0,
                              elapsed=time.time() - start,
                              error=result["error"] + (r_extract.get("stderr") or "")[:100],
                              source_type=source_type, session=session, http_code=http_code,
                              chrome_available=probe_ok, probe=probe_note)
        return result

    # opencli 可能把正文包在 JSON 信封里（{"content": ...}）：拆出正文再判 HTML/纯文本。
    if payload.lstrip().startswith('{'):
        try:
            data = json.loads(payload)
            inner = data.get("content") if isinstance(data, dict) else None
            if isinstance(inner, str) and inner.strip():
                payload = inner
        except Exception:
            pass

    looks_html = '<' in payload[:200].lower()
    visible = _visible_text(payload)
    limit = _min_visible_chars() if min_visible is None else int(min_visible)

    files = []
    if looks_html:
        (dest_dir / html_name).write_text(payload, encoding="utf-8")
        files.append(html_name)
    text_body = visible if looks_html else payload
    (dest_dir / md_name).write_text(text_body, encoding="utf-8")
    files.append(md_name)

    ok = len(visible) >= limit
    result.update({
        "status": "success" if ok else "failed",
        "files": files if ok else [],
        "rendered": True,
        "visible_chars": len(visible),
        "text": text_body,
        "error": "" if ok else f"渲染正文过短：{len(visible)} 字符 < {limit}",
    })
    result["note"] = "via opencli browser extract" if ok else (note or result["error"] + "，需人工介入")
    _record_browser_stage(
        dest_dir, url, ok=ok, exit_code=exit_code,
        size=len(payload.encode("utf-8")), elapsed=time.time() - start,
        error=result["error"], source_type=source_type, session=session,
        http_code=http_code, visible_chars=len(visible),
        html_bytes=len(payload.encode("utf-8")) if looks_html else 0,
        text_bytes=len(text_body.encode("utf-8")),
        chrome_available=probe_ok, probe=probe_note,
    )
    return result


# ============================================================
# Handlers
# ============================================================

def _http_code_from_stdout(stdout: str) -> str:
    """curl -w '%{http_code}' 输出末尾是 http code。"""
    return stdout.strip()[-3:] if stdout and stdout.strip() else ""


def handler_arxiv(dest_dir: Path, input_val: str, label: str = "primary") -> dict:
    """下载 arXiv PDF + 摘要页 HTML。"""
    result = {"label": label, "subtype": "arxiv_paper", "status": "failed", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)

    aid = None
    m = ARXIV_ID_RE.search(input_val)
    if m:
        aid = m.group(1)
    elif ARXIV_ID_RAW_RE.search(input_val):
        aid = ARXIV_ID_RAW_RE.search(input_val).group(1)

    if not aid:
        result["error"] = f"Could not extract arXiv ID from: {input_val}"
        return result

    result["arxiv_id"] = aid
    timeout = sc.get_settings().get('fetch_timeout', 60)

    # PDF
    pdf_path = dest_dir / "paper.pdf"
    for url in [f"https://arxiv.org/pdf/{aid}", f"https://export.arxiv.org/pdf/{aid}"]:
        start = time.time()
        r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(pdf_path), url,
                     "--max-time", str(timeout), "-w", "%{http_code}"], timeout=timeout + 10)
        http_code = _http_code_from_stdout(r["stdout"])
        _record_stage(dest_dir, url, "curl", r["ok"] and http_code == "200" and pdf_path.exists() and pdf_path.stat().st_size > 10000,
                      r["exit_code"], pdf_path.stat().st_size if pdf_path.exists() else 0,
                      time.time() - start, r.get("stderr", "")[:200], "arxiv_paper")
        if http_code == "200" and pdf_path.exists() and pdf_path.stat().st_size > 10000:
            result["status"] = "success"
            result["files"].append("paper.pdf")
            result["size_kb"] = pdf_path.stat().st_size // 1024
            break

    # Abstract HTML
    abs_path = dest_dir / "arxiv_abstract.html"
    r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(abs_path),
                 f"https://arxiv.org/abs/{aid}", "--max-time", "30"], timeout=35)
    if r["ok"] and abs_path.exists():
        result["files"].append("arxiv_abstract.html")
        content = abs_path.read_text(encoding="utf-8", errors="replace")
        result["drill_targets"] = extract_drill_targets(content, ["arxiv_paper", "github"], exclude_ids={aid})

    return result


def handler_github(dest_dir: Path, url: str, label: str = "primary") -> dict:
    """获取 GitHub README。"""
    result = {"label": label, "subtype": "github", "url": url, "status": "failed", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2:
        result["error"] = f"Invalid GitHub URL: {url}"
        return result
    owner, repo = path_parts[0], path_parts[1]
    result.update({"owner": owner, "repo": repo})

    readme_path = dest_dir / "github_readme.md"
    for branch in ["main", "master"]:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/README.md"
        start = time.time()
        r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(readme_path), raw_url,
                     "--max-time", "30", "-w", "%{http_code}"], timeout=35)
        http_code = _http_code_from_stdout(r["stdout"])
        ok = (http_code == "200" and readme_path.exists() and readme_path.stat().st_size > 100)
        _record_stage(dest_dir, raw_url, "curl", ok, r["exit_code"],
                      readme_path.stat().st_size if readme_path.exists() else 0,
                      time.time() - start, r.get("stderr", "")[:200], "github")
        if ok:
            result["status"] = "success"
            result["files"].append("github_readme.md")
            result["drill_targets"] = extract_drill_targets(
                readme_path.read_text(encoding="utf-8", errors="replace"), ["arxiv_paper", "github"]
            )
            break

    return result


def handler_weixin(dest_dir: Path, url: str, label: str = "primary") -> dict:
    """微信公众号文章：优先 opencli weixin download。"""
    result = {"label": label, "subtype": "weixin", "url": url, "status": "failed", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    r = run_cmd(["opencli", "weixin", "download", "--url", url,
                 "--output", str(dest_dir.absolute()),
                 "--download-images", "false",
                 "--window", "foreground",
                 "--site-session", "persistent",
                 "--keep-tab", "true"], timeout=150)

    # opencli 在 Windows 上可能把文章同时输出到默认目录 ./weixin-articles/。
    # 我们先在目标目录里查找；确认成功后清理该残留目录，避免污染工作区。
    found_file = None
    for ext in ["md", "html"]:
        for f in dest_dir.rglob(f"*.{ext}"):
            if f.name in (DRILL_LOG_FILE, FETCH_RESULTS_FILE):
                continue
            if f.stat().st_size > 200:
                found_file = f
                result["status"] = "success"
                # 保留相对路径，便于下游按 raw/{slug}/... 定位文件
                rel = f.relative_to(dest_dir).as_posix()
                result["files"].append(rel)
                if ext == "md":
                    content = f.read_text(encoding="utf-8", errors="replace")
                    result["drill_targets"] = extract_drill_targets(content, ["arxiv_paper", "github"])
                break
        if result["status"] == "success":
            break

    if result["status"] == "success":
        weixin_default = Path("weixin-articles")
        if weixin_default.exists():
            try:
                shutil.rmtree(weixin_default)
            except Exception:
                pass

    file_size = found_file.stat().st_size if found_file else 0
    _record_stage(dest_dir, url, "opencli_weixin", result["status"] == "success",
                  r["exit_code"], file_size, time.time() - start,
                  r.get("stderr", "")[:200], "weixin")

    return result


def handler_browser(dest_dir: Path, url: str, label: str = "primary",
                    file_stem: str = "webpage", subtype: str = "webpage") -> dict:
    """通用浏览器渲染抓取：落盘 <file_stem>_rendered.html / <file_stem>_rendered.md。

    既是 fetch.handler: browser 的来源类型入口，也是渲染必需来源的兜底路径实现。
    """
    result = _browser_fetch(
        dest_dir, url,
        html_name=f"{file_stem}{RENDER_SUFFIX_HTML}",
        md_name=f"{file_stem}{RENDER_SUFFIX_MD}",
        source_type=file_stem,
    )
    result.update({"label": label, "subtype": subtype, "url": url})
    if result["status"] == "success":
        result["drill_targets"] = extract_drill_targets(result.get("text", ""), ["arxiv_paper", "github"])
    return result


def handler_linkedin(dest_dir: Path, url: str, label: str = "primary") -> dict:
    """LinkedIn：需要浏览器登录态（走共用浏览器抓取路径）。

    沿用历史判据：只要 extract 拿到非空正文即算成功（LinkedIn 帖子可能很短，
    不以 min_visible_chars 卡它）；产物名保持 linkedin_post.{html,md}。
    """
    out = _browser_fetch(
        dest_dir, url, session="linkedin",
        html_name="linkedin_post.html", md_name="linkedin_post.md",
        source_type="linkedin", min_visible=1, note="需要浏览器登录态",
    )
    result = {
        "label": label, "subtype": "linkedin", "url": url,
        "status": out["status"], "files": out["files"], "error": out["error"],
        "chrome_available": out["chrome_available"], "note": out["note"],
    }
    if out["status"] == "success":
        result["drill_targets"] = extract_drill_targets(out.get("text", ""), ["arxiv_paper", "github"])
    return result


MIN_VISIBLE_CHARS = 800  # 可见正文下限（settings.min_visible_chars 可覆盖）
MIN_HTML_BYTES = 200  # 响应体下限：低于此值视为没抓到内容
SPA_SHELL_MARKERS = (
    'type="module"', 'id="root"', "id='root'", 'id="app"', "id='app'",
    '__next_data__', 'data-reactroot', 'ng-version=',
)


def _visible_text(html: str) -> str:
    """剥离 script/style 与全部标签后的可见正文，用于材料有效性判定。"""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _looks_like_spa_shell(html: str, visible_chars: int, min_visible: int) -> bool:
    """可见正文过短且 HTML 只挂载了 JS 入口 → 疑似 SPA 空壳（正文由 JS 运行时注入）。"""
    if visible_chars >= min_visible:
        return False
    low = html.lower()
    return any(marker in low for marker in SPA_SHELL_MARKERS)


def handler_webpage(dest_dir: Path, url: str, label: str = "primary", file_stem: str = "webpage") -> dict:
    """通用网页：下载原始 HTML，并按「可见正文密度」判定材料是否有效。

    只凭 HTTP 200 + 文件字节数会把 SPA 外壳（Vite/React/Next CSR，正文由 JS 渲染）
    判成 success，材料实质为空却一路放行。这里剥掉 script/style/标签后再看正文长度：
    低于 min_visible_chars 判 failed，并把 spa_shell / visible_chars 写进
    _fetch_results.json 供 orchestration 决策。
    """
    result = {"label": label, "subtype": "webpage", "url": url, "status": "failed", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)
    settings = sc.get_settings()
    timeout = settings.get('fetch_timeout', 60)
    min_visible = int(settings.get('min_visible_chars', MIN_VISIBLE_CHARS))

    page_path = dest_dir / f"{file_stem}.html"
    start = time.time()
    r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(page_path), url,
                 "--max-time", str(timeout), "-w", "%{http_code}"], timeout=timeout + 10)
    http_code = _http_code_from_stdout(r["stdout"])
    size = page_path.stat().st_size if page_path.exists() else 0
    fetched = bool(http_code == "200" and size > MIN_HTML_BYTES)

    html = page_path.read_text(encoding="utf-8", errors="replace") if fetched else ""
    visible = _visible_text(html) if fetched else ""
    spa_shell = fetched and _looks_like_spa_shell(html, len(visible), min_visible)
    ok = fetched and len(visible) >= min_visible

    error = ""
    if fetched and not ok:
        error = (f"疑似 SPA 空壳：可见正文仅 {len(visible)} 字符 < {min_visible}"
                 if spa_shell else f"可见正文过短：{len(visible)} 字符 < {min_visible}")
    elif not fetched:
        error = (f"HTTP {http_code or '?'}" if http_code != "200"
                 else f"响应内容过小（{size} 字节）")

    _record_stage(dest_dir, url, "curl", ok, r["exit_code"],
                  size,
                  time.time() - start, error or (r.get("stderr") or "")[:200], file_stem,
                  extra={"http_code": http_code, "visible_chars": len(visible), "spa_shell": spa_shell})

    if ok:
        result["status"] = "success"
        result["files"].append(f"{file_stem}.html")
        result["drill_targets"] = extract_drill_targets(visible, ["arxiv_paper", "github"])
    else:
        result["error"] = error
        result["visible_chars"] = len(visible)
        result["spa_shell"] = spa_shell
        if spa_shell:
            result["note"] = "疑似 SPA 空壳：正文由 JS 渲染，curl 只拿到外壳，需浏览器抓取"

    return result


def handler_search(dest_dir: Path, query: str, label: str = "primary") -> dict:
    """非 URL 实体搜索：记录 prompt 与占位，具体搜索由子 agent 或外部工具完成。"""
    result = {"label": label, "subtype": "search", "name": query, "status": "partial", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = dest_dir / "prompt.md"
    prompt_path.write_text(f"# Search task\n\nQuery: {query}\n", encoding="utf-8")
    result["files"].append("prompt.md")
    return result


def handler_none(dest_dir: Path, input_val: str, label: str = "primary") -> dict:
    """不抓取的来源（如 local_file / multi_source，素材由用户手动提供）：不联网、不写盘。"""
    return {"label": label, "status": "skipped_local", "files": []}


HANDLERS = {
    "arxiv": handler_arxiv,
    "github": handler_github,
    "weixin": handler_weixin,
    "linkedin": handler_linkedin,
    "webpage": handler_webpage,
    "browser": handler_browser,
    "search": handler_search,
    "none": handler_none,
}

# sources.yaml 里 arxiv_title / project_name 声明的组合 handler 名；实际都走 search。
HANDLER_ALIASES = {
    "search_then_arxiv": "search",
    "search_then_github": "search",
}


# ============================================================
# Core collection orchestration
# ============================================================

# 这些 handler 没有「轻量路径」可重试：linkedin/browser 直接走浏览器，search/none 不抓取。
_SKIP_RENDER_FALLBACK_HANDLERS = {"linkedin", "browser", "search", "none"}


def _render_retries() -> int:
    try:
        return max(0, int(sc.get_settings().get('render_retries', RENDER_RETRIES_DEFAULT)))
    except Exception:
        return RENDER_RETRIES_DEFAULT


def _collect_with_render_fallback(handler, dest_dir: Path, input_val: str, label: str,
                                  kwargs: dict, subtype: str, fetch: dict) -> dict:
    """渲染必需来源：先重试轻量路径（curl / opencli weixin），全失败再浏览器兜底。

    解析顺序与最终状态：
      1. 任一轻量尝试成功 → 原样返回；
      2. 轻量路径全失败 → 走 handler_browser（raw/<stem>_rendered.html / .md）；
      3. 两条路都没有有效正文 → status="needs_browser"（drill log 的 needs_manual
         会计数并带 note），绝不假报 success。
    每次尝试都带 attempt 序号写进 _fetch_results.json。
    """
    retries = _render_retries()
    try:
        budget = float(sc.get_settings().get('fetch_timeout', 60)) / 2
    except Exception:
        budget = 30.0

    result = {}
    attempts = 0
    started = time.time()
    for attempt in range(1, retries + 2):
        with _fetch_attempt(attempt):
            result = handler(dest_dir, input_val, **kwargs)
        attempts = attempt
        if result.get("status") == "success":
            return result
        if time.time() - started >= budget:
            # 单次尝试已耗掉 fetch_timeout 的一半：重试只会重复同样的慢失败
            # （超时/网络不可达），把剩余时间留给浏览器兜底。
            break

    print(f"  ⚠️ {subtype} 为渲染必需来源：轻量路径 {attempts} 次未拿到正文 → 浏览器渲染兜底",
          file=sys.stderr)
    file_stem = fetch.get('file_stem') or subtype.replace('_', '-')
    with _fetch_attempt(attempts + 1):
        rendered = handler_browser(dest_dir, input_val, label=label,
                                   file_stem=file_stem, subtype=subtype)

    if rendered.get("status") == "success":
        print(f"  ✅ {subtype} 浏览器渲染兜底成功", file=sys.stderr)
        return {
            "label": label, "subtype": subtype, "url": input_val,
            "status": "success",
            "files": rendered.get("files", []),
            "drill_targets": rendered.get("drill_targets", []),
            "note": f"轻量路径失败 {attempts} 次 → 浏览器渲染兜底成功",
            "rendered": True,
        }

    cheap_error = result.get("error") or ""
    render_error = rendered.get("error") or rendered.get("note") or "浏览器渲染无有效正文"
    return {
        "label": label, "subtype": subtype, "url": input_val,
        "status": "needs_browser",
        "files": [],
        "error": f"轻量路径: {cheap_error or '无有效正文'}；渲染兜底: {render_error}"[:200],
        "note": (f"轻量路径失败 {attempts} 次且浏览器渲染兜底无有效正文"
                 f"（{render_error}）→ 需人工介入"),
        "chrome_available": rendered.get("chrome_available", False),
    }


def _run_handler(subtype: str, dest_dir: Path, input_val: str, label: str) -> dict:
    """根据 subtype 的 fetch.handler 调用对应 handler。"""
    defn = sc.get_source_type(subtype)
    if not defn:
        return {"label": label, "subtype": subtype, "status": "failed",
                "files": [], "error": f"Unknown subtype: {subtype}"}

    fetch = defn.get('fetch', {})
    handler_name = fetch.get('handler', 'webpage')
    handler_name = HANDLER_ALIASES.get(handler_name, handler_name)
    handler = HANDLERS.get(handler_name)
    if not handler:
        return {"label": label, "subtype": subtype, "status": "failed",
                "files": [], "error": f"Unknown handler: {handler_name}"}

    kwargs = {"label": label}
    if handler_name in ("webpage", "browser"):
        kwargs["file_stem"] = fetch.get('file_stem', subtype.replace('_', '-'))
    if handler_name == "browser":
        kwargs["subtype"] = subtype

    if (is_render_required(subtype, input_val)
            and handler_name not in _SKIP_RENDER_FALLBACK_HANDLERS):
        return _collect_with_render_fallback(handler, dest_dir, input_val, label,
                                             kwargs, subtype, fetch)

    return handler(dest_dir, input_val, **kwargs)


def _result_to_entry(level: int, label: str, subtype: str, input_val: str,
                     result: dict, parent: str = "", file_prefix: str = "") -> dict:
    files = result.get("files", [])
    if level > 1:
        prefix = f"{file_prefix}l{level}_{label.split('-')[-1]}/"
    elif file_prefix:
        prefix = file_prefix
    else:
        prefix = ""
    return {
        "level": level, "label": label, "subtype": subtype, "input": input_val,
        "status": result.get("status", "failed"),
        "files": [prefix + f for f in files],
        "drill_targets": result.get("drill_targets", []),
        "error": result.get("error", ""),
        "note": result.get("note", ""),
        "parent": parent,
    }


def collect_materials(slug: str, input_type: str, source_type: str,
                      input_val: str, max_depth: int = None) -> dict:
    """主入口：收集素材 + 递归下钻。

    Args:
        input_type: url | keywords | local
        source_type: 平台值（arxiv/github/weixin/...），会转回 canonical subtype 用于 handler。
    """
    config = sc.load_config()
    settings = sc.get_settings(config)
    max_depth = max_depth if max_depth is not None else settings.get('max_drill_depth', 3)
    max_children = settings.get('max_children_per_level', 5)

    subtype = sc.to_platform_subtype(source_type)

    started_at = datetime.now(timezone.utc).isoformat()
    dest_dir = paths.raw_dir(slug, WORKSPACE)
    dest_dir.mkdir(parents=True, exist_ok=True)

    drill_log = {
        "slug": slug, "started_at": started_at, "max_depth": max_depth,
        "primary_type": f"{input_type}.{source_type}", "primary_input": input_val,
        "levels": [],
        "summary": {"total_files": 0, "success": 0, "failed": 0, "needs_manual": 0},
    }

    # Level 1
    l1_result = _run_handler(subtype, dest_dir, input_val, "L1-primary")
    l1_entry = _result_to_entry(1, "L1-primary", subtype, input_val, l1_result)
    drill_log["levels"].append({"level": 1, "entries": [l1_entry]})
    drill_log["summary"]["total_files"] += len(l1_result.get("files", []))
    if l1_result["status"] == "success":
        drill_log["summary"]["success"] += 1
    elif l1_result["status"] == "needs_browser":
        drill_log["summary"]["needs_manual"] += 1
    else:
        drill_log["summary"]["failed"] += 1

    seen_ids = set()
    if l1_result.get("arxiv_id"):
        seen_ids.add(l1_result["arxiv_id"])

    # Level 2
    if max_depth >= 2 and l1_result.get("drill_targets"):
        l2_entries = []
        for i, target in enumerate(l1_result["drill_targets"][:max_children]):
            t_subtype = target["subtype"]
            t_input = target["input"]
            if t_subtype == "arxiv_paper":
                m = ARXIV_ID_RE.search(t_input)
                if m and m.group(1) in seen_ids:
                    continue
                if m:
                    seen_ids.add(m.group(1))
            if t_input in seen_ids:
                continue
            seen_ids.add(t_input)

            l2_dir = dest_dir / f"l2_{i+1}"
            t_result = _run_handler(t_subtype, l2_dir, t_input, f"L2-{i+1}")
            entry = _result_to_entry(2, f"L2-{i+1}", t_subtype, t_input, t_result, parent="L1-primary")
            l2_entries.append(entry)
            drill_log["summary"]["total_files"] += len(t_result.get("files", []))
            drill_log["summary"]["success" if t_result["status"] == "success" else "failed"] += 1
        drill_log["levels"].append({"level": 2, "entries": l2_entries})

        # Level 3
        if max_depth >= 3:
            l3_entries = []
            l3_idx = 0
            max_l3_per_parent = settings.get('max_l3_children_per_parent', 2)
            for l2_entry in l2_entries:
                if l3_idx >= max_children:
                    break
                for target in l2_entry.get("drill_targets", [])[:max_l3_per_parent]:
                    l3_idx += 1
                    if l3_idx > max_children:
                        break
                    t_subtype = target["subtype"]
                    t_input = target["input"]
                    l3_dir = dest_dir / f"l3_{l3_idx}"
                    t_result = _run_handler(t_subtype, l3_dir, t_input, f"L3-{l3_idx}")
                    entry = _result_to_entry(3, f"L3-{l3_idx}", t_subtype, t_input, t_result, parent=l2_entry["label"])
                    entry["drill_targets"] = []
                    l3_entries.append(entry)
                    drill_log["summary"]["total_files"] += len(t_result.get("files", []))
                if l3_idx > max_children:
                    break
            if l3_entries:
                drill_log["levels"].append({"level": 3, "entries": l3_entries})

    log_path = dest_dir / DRILL_LOG_FILE
    log_path.write_text(json.dumps(drill_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return drill_log


def collect_sources(slug: str, sources: list[dict], max_depth: int = None,
                    prefix: str = "", dest_base=None) -> dict:
    """多源收集入口：每个 source 放入 {dest_base}/{prefix}s{i}/，仅主源启用下钻。

    sources 元素格式（兼容旧键）：
      {"input_type": "url", "source_type": "arxiv", "input": "..."}
      或旧 {"type": "url", "subtype": "arxiv_paper", "input": "..."}
    prefix 用于追加模式，如 "append_1/"。
    dest_base 默认 paths.raw_dir(slug, WORKSPACE)；特殊场景可显式覆盖。
    """
    config = sc.load_config()
    settings = sc.get_settings(config)
    max_depth = max_depth if max_depth is not None else settings.get('max_drill_depth', 3)
    max_children = settings.get('max_children_per_level', 5)
    max_l3_per_parent = settings.get('max_l3_children_per_parent', 2)

    started_at = datetime.now(timezone.utc).isoformat()
    dest_base = Path(dest_base) if dest_base is not None else paths.raw_dir(slug, WORKSPACE)
    dest_base.mkdir(parents=True, exist_ok=True)

    drill_log = {
        "slug": slug, "started_at": started_at, "max_depth": max_depth,
        "primary_type": "multi_source", "primary_input": "",
        "sources": [s.get("input", "") for s in sources],
        "levels": [],
        "summary": {"total_files": 0, "success": 0, "failed": 0, "needs_manual": 0},
    }

    seen_ids = set()
    prefix = prefix if prefix else ""
    if prefix and not prefix.endswith('/'):
        prefix += '/'

    for i, src in enumerate(sources):
        src_dir = dest_base / prefix / f"s{i}"
        src_dir.mkdir(parents=True, exist_ok=True)
        source_type = src.get("source_type", src.get("subtype", "generic_web"))
        subtype = sc.to_platform_subtype(source_type)
        input_val = src.get("input", "")
        is_primary = (i == 0)
        label = f"L1-s{i}"
        file_prefix = f"{prefix}s{i}/"

        if subtype == 'local_file' or src.get("input_type", src.get("type")) == 'local':
            l1_result = {"label": label, "subtype": "local_file", "status": "skipped_local", "files": []}
        else:
            l1_result = _run_handler(subtype, src_dir, input_val, label)
            _merge_fetch_results(src_dir, dest_base, source_index=i)
        l1_entry = _result_to_entry(
            1, label, subtype, input_val, l1_result, file_prefix=file_prefix
        )
        l1_entry["source_index"] = i
        drill_log["levels"].append({"level": 1, "source_index": i, "entries": [l1_entry]})
        drill_log["summary"]["total_files"] += len(l1_result.get("files", []))
        if l1_result["status"] == "success":
            drill_log["summary"]["success"] += 1
        elif l1_result["status"] == "needs_browser":
            drill_log["summary"]["needs_manual"] += 1
        elif l1_result["status"] == "skipped_local":
            pass
        else:
            drill_log["summary"]["failed"] += 1

        if l1_result.get("arxiv_id"):
            seen_ids.add(l1_result["arxiv_id"])

        # 仅主源做 L2/L3 下钻
        if is_primary and max_depth >= 2 and l1_result.get("drill_targets"):
            l2_entries = []
            for j, target in enumerate(l1_result["drill_targets"][:max_children]):
                t_subtype = target["subtype"]
                t_input = target["input"]
                if t_subtype == "arxiv_paper":
                    m = ARXIV_ID_RE.search(t_input)
                    if m and m.group(1) in seen_ids:
                        continue
                    if m:
                        seen_ids.add(m.group(1))
                if t_input in seen_ids:
                    continue
                seen_ids.add(t_input)

                l2_dir = src_dir / f"l2_{j+1}"
                t_result = _run_handler(t_subtype, l2_dir, t_input, f"L2-{j+1}")
                entry = _result_to_entry(
                    2, f"L2-{j+1}", t_subtype, t_input, t_result,
                    parent=label, file_prefix=file_prefix
                )
                entry["source_index"] = i
                l2_entries.append(entry)
                drill_log["summary"]["total_files"] += len(t_result.get("files", []))
                drill_log["summary"]["success" if t_result["status"] == "success" else "failed"] += 1
            if l2_entries:
                drill_log["levels"].append({"level": 2, "source_index": i, "entries": l2_entries})

            if max_depth >= 3:
                l3_entries = []
                l3_idx = 0
                for l2_entry in l2_entries:
                    if l3_idx >= max_children:
                        break
                    for target in l2_entry.get("drill_targets", [])[:max_l3_per_parent]:
                        l3_idx += 1
                        if l3_idx > max_children:
                            break
                        t_subtype = target["subtype"]
                        t_input = target["input"]
                        l3_dir = src_dir / f"l3_{l3_idx}"
                        t_result = _run_handler(t_subtype, l3_dir, t_input, f"L3-{l3_idx}")
                        entry = _result_to_entry(
                            3, f"L3-{l3_idx}", t_subtype, t_input, t_result,
                            parent=l2_entry["label"], file_prefix=file_prefix
                        )
                        entry["source_index"] = i
                        entry["drill_targets"] = []
                        l3_entries.append(entry)
                        drill_log["summary"]["total_files"] += len(t_result.get("files", []))
                    if l3_idx > max_children:
                        break
                if l3_entries:
                    drill_log["levels"].append({"level": 3, "source_index": i, "entries": l3_entries})

    log_path = dest_base / DRILL_LOG_FILE
    log_path.write_text(json.dumps(drill_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return drill_log


# ============================================================
# CLI
# ============================================================

SAFE_SUBDIR_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def _safe_subdir(name: str):
    """校验 --dest-subdir：只接受单层安全相对目录名（拒绝 .. / 路径分隔符 / 盘符）。"""
    candidate = (name or '').strip()
    if not candidate or '..' in candidate or not SAFE_SUBDIR_RE.fullmatch(candidate):
        return None
    return candidate


def main():
    parser = argparse.ArgumentParser(description="素材收集器 — 递归下钻 + 落盘 raw/")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--input-type", dest="input_type", help="url | keywords | local（单源模式）")
    parser.add_argument("--source-type", dest="source_type", help="平台值如 arxiv/github/weixin（单源模式）")
    parser.add_argument("--type", dest="input_type", help="旧别名，同 --input-type")
    parser.add_argument("--subtype", dest="source_type", help="旧别名，同 --source-type")
    parser.add_argument("--input", help="URL / arXiv ID / 关键词（单源模式）")
    parser.add_argument("--sources-json", help='多源模式：JSON 列表，如 [{"source_type":"arxiv","input":"..."}, ...]')
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--dest-subdir", dest="dest_subdir",
                        help="落盘到 raw/<dest-subdir>/（append 补料用；仅单层安全目录名，多源模式）")
    parser.add_argument("--json", action="store_true", help="输出完整 drill_log JSON")
    args = parser.parse_args()

    dest_base = None
    if args.dest_subdir is not None:
        name = _safe_subdir(args.dest_subdir)
        if name is None:
            parser.error(f"--dest-subdir 只接受单层安全相对目录名（字母数字/._-，不含 ..、/、\\\\）: "
                         f"{args.dest_subdir!r}")
        dest_base = paths.raw_dir(args.slug, WORKSPACE) / name

    if args.sources_json:
        sources = json.loads(args.sources_json)
        log = collect_sources(args.slug, sources, args.max_depth, dest_base=dest_base)
    elif args.input:
        if dest_base is not None:
            parser.error("--dest-subdir 仅支持多源模式（--sources-json）")
        if not args.input_type or not args.source_type:
            parser.error("单源模式需要 --input-type 和 --source-type（或旧 --type/--subtype）")
        log = collect_materials(args.slug, args.input_type, args.source_type, args.input,
                                args.max_depth)
    else:
        parser.error("需要 --input（单源）或 --sources-json（多源）")

    if args.json:
        print(json.dumps(log, ensure_ascii=False, indent=2))
    else:
        s = log["summary"]
        print(f"\n  收集完成: {args.slug}")
        print(f"  主类型: {log['primary_type']}")
        print(f"  文件数: {s['total_files']}  |  成功: {s['success']}  |  失败: {s['failed']}")
        if s["needs_manual"]:
            print(f"  ⚠️ {s['needs_manual']} 个源需要手动")
        for lv in log["levels"]:
            for e in lv["entries"]:
                icon = "✅" if e["status"] == "success" else "❌"
                print(f"  {icon} {e['label']}: {e['subtype']} — {e['input'][:60]}")


if __name__ == "__main__":
    main()
