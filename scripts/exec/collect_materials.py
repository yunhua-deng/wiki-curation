#!/usr/bin/env python3
"""
素材收集器 — 配置驱动，递归下钻，落盘 raw/。

来源策略统一来自 references/sources.yaml。
"""
import json
import os
import re
import time
import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# 本脚本位于 exec/，需要 scripts/ 根目录才能导入 lib、source_config 等公共模块

from scripts import source_config as sc
from scripts import lib
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


def _record_stage(dest_dir: Path, url: str, tool: str, ok: bool, exit_code: int,
                  file_size: int = 0, download_time: float = 0.0, error: str = "",
                  source_type: str = ""):
    _append_fetch_result(dest_dir, {
        "url": url,
        "tool": tool,
        "exit_code": exit_code,
        "file_size": file_size,
        "download_time": round(download_time, 3),
        "error": error[:200],
        "status": "success" if ok else "failed",
        "source_type": source_type,
    })


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


def handler_github(dest_dir: Path, url: str, label: str = "primary", download_zip: bool = False) -> dict:
    """获取 GitHub README；deep 时可选源码 zip。"""
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

    # Optional zip for deep interpretation
    if download_zip and result["status"] == "success":
        zip_path = dest_dir / "code.zip"
        if not (zip_path.exists() and zip_path.stat().st_size > 1024):
            for base in [f"https://github.com/{owner}/{repo}/archive/refs/heads",
                         f"https://mirror.ghproxy.com/https://github.com/{owner}/{repo}/archive/refs/heads"]:
                for branch in ["main", "master"]:
                    start = time.time()
                    r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(zip_path),
                                 f"{base}/{branch}.zip", "--max-time", "120", "-w", "%{http_code}"],
                                timeout=130)
                    http_code = _http_code_from_stdout(r["stdout"])
                    ok = (http_code == "200" and zip_path.exists() and zip_path.stat().st_size > 1024)
                    _record_stage(dest_dir, f"{base}/{branch}.zip", "curl", ok, r["exit_code"],
                                  zip_path.stat().st_size if zip_path.exists() else 0,
                                  time.time() - start, r.get("stderr", "")[:200], "github_zip")
                    if ok:
                        size_mb = zip_path.stat().st_size / (1024 * 1024)
                        if size_mb > 80:
                            zip_path.unlink()
                            result["note"] = f"zip 过大 ({size_mb:.0f}MB > 80MB)，跳过源码"
                        else:
                            result["files"].append("code.zip")
                        break
                else:
                    continue
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


def handler_linkedin(dest_dir: Path, url: str, label: str = "primary") -> dict:
    """LinkedIn：需要浏览器登录态。"""
    result = {"label": label, "subtype": "linkedin", "url": url, "status": "needs_browser",
              "files": [], "note": "需要浏览器登录态"}
    # openclaw browser 初始化连接可能需要 15-20 秒，给足 30 秒外层超时。
    r = run_cmd(["openclaw", "browser", "--browser-profile", "user", "tabs"], timeout=30)
    chrome_open = r["ok"] and "tab:" in r["stdout"]
    result["chrome_available"] = chrome_open

    if chrome_open:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 使用 opencli browser 的 session 机制打开 URL 并提取 markdown。
        # "linkedin" 是 session 名称（任意即可），不是子命令。
        r1 = run_cmd(["opencli", "browser", "linkedin", "open", url], timeout=30)
        if r1["ok"]:
            # LinkedIn 页面加载和提取可能较慢，给 60 秒超时。
            r2 = run_cmd(["opencli", "browser", "linkedin", "extract"], timeout=60)
            if r2["ok"] and r2["stdout"].strip():
                content = r2["stdout"]
                post_path = dest_dir / ("linkedin_post.html" if '<' in content[:200].lower() else "linkedin_post.md")
                post_path.write_text(content, encoding="utf-8")
                result.update({"status": "success", "files": [post_path.name], "note": "via opencli browser extract"})
                text = content
                if content.strip().startswith('{'):
                    try:
                        text = json.loads(content).get("content", content)
                    except Exception:
                        pass
                result["drill_targets"] = extract_drill_targets(text, ["arxiv_paper", "github"])
    return result


def handler_webpage(dest_dir: Path, url: str, label: str = "primary", file_stem: str = "webpage") -> dict:
    """通用网页：下载原始 HTML。"""
    result = {"label": label, "subtype": "webpage", "url": url, "status": "failed", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)
    timeout = sc.get_settings().get('fetch_timeout', 60)

    page_path = dest_dir / f"{file_stem}.html"
    start = time.time()
    r = run_cmd([_curl(), "-sL", "--http1.1", "-o", str(page_path), url,
                 "--max-time", str(timeout), "-w", "%{http_code}"], timeout=timeout + 10)
    http_code = _http_code_from_stdout(r["stdout"])
    ok = (http_code == "200" and page_path.exists() and page_path.stat().st_size > 200)
    _record_stage(dest_dir, url, "curl", ok, r["exit_code"],
                  page_path.stat().st_size if page_path.exists() else 0,
                  time.time() - start, r.get("stderr", "")[:200], file_stem)

    if ok:
        result["status"] = "success"
        result["files"].append(f"{file_stem}.html")
        html = page_path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S | re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S | re.I)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        result["drill_targets"] = extract_drill_targets(text, ["arxiv_paper", "github"])

    return result


def handler_search(dest_dir: Path, query: str, label: str = "primary") -> dict:
    """非 URL 实体搜索：记录 prompt 与占位，具体搜索由子 agent 或外部工具完成。"""
    result = {"label": label, "subtype": "search", "name": query, "status": "partial", "files": []}
    dest_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = dest_dir / "prompt.md"
    prompt_path.write_text(f"# Search task\n\nQuery: {query}\n", encoding="utf-8")
    result["files"].append("prompt.md")
    return result


HANDLERS = {
    "arxiv": handler_arxiv,
    "github": handler_github,
    "weixin": handler_weixin,
    "linkedin": handler_linkedin,
    "webpage": handler_webpage,
    "search": handler_search,
}


# ============================================================
# Core collection orchestration
# ============================================================

def _run_handler(subtype: str, dest_dir: Path, input_val: str, label: str,
                 download_zip: bool = False) -> dict:
    """根据 subtype 的 fetch.handler 调用对应 handler。"""
    defn = sc.get_source_type(subtype)
    if not defn:
        return {"label": label, "subtype": subtype, "status": "failed",
                "files": [], "error": f"Unknown subtype: {subtype}"}

    fetch = defn.get('fetch', {})
    handler_name = fetch.get('handler', 'webpage')
    handler = HANDLERS.get(handler_name)
    if not handler:
        return {"label": label, "subtype": subtype, "status": "failed",
                "files": [], "error": f"Unknown handler: {handler_name}"}

    kwargs = {"label": label}
    if handler_name == "webpage":
        kwargs["file_stem"] = fetch.get('file_stem', subtype.replace('_', '-'))
    if handler_name == "github":
        kwargs["download_zip"] = download_zip

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
                      input_val: str, max_depth: int = None,
                      download_zip: bool = False) -> dict:
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
    l1_result = _run_handler(subtype, dest_dir, input_val, "L1-primary", download_zip=download_zip)
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
            t_result = _run_handler(t_subtype, l2_dir, t_input, f"L2-{i+1}", download_zip=False)
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
                    t_result = _run_handler(t_subtype, l3_dir, t_input, f"L3-{l3_idx}", download_zip=False)
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
                    download_zip: bool = False, prefix: str = "", dest_base=None) -> dict:
    """多源收集入口：每个 source 放入 {dest_base}/{prefix}s{i}/，仅主源启用下钻。

    sources 元素格式（兼容旧键）：
      {"input_type": "url", "source_type": "arxiv", "input": "..."}
      或旧 {"type": "url", "subtype": "arxiv_paper", "input": "..."}
    prefix 用于追加模式，如 "append_1/"。
    dest_base 默认 paths.raw_dir(slug, WORKSPACE)；dive 等场景可显式覆盖。
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
            l1_result = _run_handler(
                subtype, src_dir, input_val, label,
                download_zip=(download_zip if is_primary else False)
            )
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
                t_result = _run_handler(t_subtype, l2_dir, t_input, f"L2-{j+1}", download_zip=False)
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
                        t_result = _run_handler(t_subtype, l3_dir, t_input, f"L3-{l3_idx}", download_zip=False)
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
    parser.add_argument("--download-zip", action="store_true", help="仅对 github handler 下载 code.zip")
    parser.add_argument("--json", action="store_true", help="输出完整 drill_log JSON")
    args = parser.parse_args()

    if args.sources_json:
        sources = json.loads(args.sources_json)
        log = collect_sources(args.slug, sources, args.max_depth, download_zip=args.download_zip)
    elif args.input:
        if not args.input_type or not args.source_type:
            parser.error("单源模式需要 --input-type 和 --source-type（或旧 --type/--subtype）")
        log = collect_materials(args.slug, args.input_type, args.source_type, args.input,
                                args.max_depth, download_zip=args.download_zip)
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
