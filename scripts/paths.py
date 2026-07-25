#!/usr/bin/env python3
"""
paths.py — Wiki 文件系统布局的统一真相源。

所有脚本对 wiki.db、artifacts、raw、audit、references、assets 的访问都应通过本模块，
禁止再写 `WORKSPACE / "raw"` 之类的硬编码路径。
"""
import os
from pathlib import Path


def get_workspace(fallback_script_path: str = None) -> Path:
    """解析 wiki 工作区根目录（即 wiki/ 本身）。

    优先级：
        1. 环境变量 WIKI_WORKSPACE
        2. fallback_script_path 所在目录的父目录（脚本位于 skills/wiki-curation/scripts/* 时即为 wiki/）
        3. 当前工作目录
    """
    env = os.environ.get('WIKI_WORKSPACE')
    if env:
        return Path(env).resolve()
    if fallback_script_path:
        return Path(fallback_script_path).resolve().parent.parent
    return Path.cwd().resolve()


def _resolve_ws(ws):
    return Path(ws).resolve() if ws is not None else get_workspace()


# ---------------------------------------------------------------------------
# 系统/运行时路径
# ---------------------------------------------------------------------------
def db_path(ws=None) -> Path:
    """wiki.db 运行时路径：wiki/data/wiki.db"""
    return _resolve_ws(ws) / "data" / "wiki.db"


def references_dir() -> Path:
    """references/：agent 需要读取的知识/规则"""
    return Path(__file__).resolve().parent.parent / "references"


def assets_dir() -> Path:
    """assets/：输出模板/静态资源"""
    return Path(__file__).resolve().parent.parent / "assets"


# ---------------------------------------------------------------------------
# 输出产物路径（wiki/artifacts/{slug}/...）
# ---------------------------------------------------------------------------
def artifacts_dir(ws=None) -> Path:
    """所有输出产物根目录：wiki/artifacts"""
    return _resolve_ws(ws) / "artifacts"


def entry_dir(slug: str, ws=None) -> Path:
    """单个条目目录：wiki/artifacts/{slug}"""
    return artifacts_dir(ws) / slug


def source_metadata_path(slug: str, ws=None) -> Path:
    """来源元数据 JSON 路径：wiki/artifacts/{slug}/source_metadata.json"""
    return entry_dir(slug, ws) / "source_metadata.json"


def record_path(slug: str, ws=None) -> Path:
    """结构化记录 JSON 路径：wiki/artifacts/{slug}/record.json"""
    return entry_dir(slug, ws) / "record.json"


def article_path(slug: str, depth: str, ws=None) -> Path:
    """文章 md 绝对路径：wiki/artifacts/{slug}/{slug}_{depth}.md

    用于实际文件读写。子 agent 的 output_path 必须从这里取绝对路径，
    禁止把 article_rel() 的相对路径直接交给外部 agent 写入。
    """
    return entry_dir(slug, ws) / f"{slug}_{depth}.md"


def raw_dir(slug: str, ws=None) -> Path:
    """原始素材目录：wiki/artifacts/{slug}/raw"""
    return entry_dir(slug, ws) / "raw"


def audit_dir(slug: str, ws=None) -> Path:
    """审计报告目录：wiki/artifacts/{slug}/audit"""
    return entry_dir(slug, ws) / "audit"


def audit_json_path(slug: str, depth: str, ws=None) -> Path:
    """审计 JSON 路径：wiki/artifacts/{slug}/audit/{slug}_{depth}_audit.json"""
    return audit_dir(slug, ws) / f"{slug}_{depth}_audit.json"


def audit_md_path(slug: str, depth: str, ws=None) -> Path:
    """审计 Markdown 路径：wiki/artifacts/{slug}/audit/{slug}_{depth}_audit.md"""
    return audit_dir(slug, ws) / f"{slug}_{depth}_audit.md"


# ---------------------------------------------------------------------------
# 相对路径字符串（仅用于 task prompt / Markdown 文档内链接）
# ⚠️ 禁止用于文件写入——写入操作必须使用上面的 article_path / raw_dir / audit_dir 等绝对路径
# ---------------------------------------------------------------------------
def article_rel(slug: str, depth: str) -> str:
    """仅用于 task prompt / 文档内链接；文件 IO 请用 article_path()。"""
    return f"artifacts/{slug}/{slug}_{depth}.md"


def raw_rel(slug: str) -> str:
    """仅用于 task prompt / 文档内链接；文件 IO 请用 raw_dir()。"""
    return f"artifacts/{slug}/raw/"


def record_rel(slug: str) -> str:
    """仅用于 task prompt / 文档内链接；文件 IO 请用 record_path()。"""
    return f"artifacts/{slug}/record.json"


def audit_json_rel(slug: str, depth: str) -> str:
    """仅用于 task prompt / 文档内链接；文件 IO 请用 audit_json_path()。"""
    return f"artifacts/{slug}/audit/{slug}_{depth}_audit.json"


# ---------------------------------------------------------------------------
# 旧版路径（迁移期兼容）
# ---------------------------------------------------------------------------
def legacy_article_path(slug: str, depth: str, ws=None) -> Path:
    return _resolve_ws(ws) / f"{slug}_{depth}.md"


def legacy_raw_dir(slug: str, ws=None) -> Path:
    return _resolve_ws(ws) / "raw" / slug


def legacy_audit_dir(ws=None) -> Path:
    return _resolve_ws(ws) / "audit"


def legacy_audit_json_path(slug: str, depth: str, ws=None) -> Path:
    return legacy_audit_dir(ws) / f"{slug}_{depth}_audit.json"
