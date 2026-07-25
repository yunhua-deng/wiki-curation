#!/usr/bin/env python3
"""
scripts/wiki_index/metadata.py — 老条目 metadata.json 的读取（v3.2 只读）。

metadata.json 已停止生成（record.json 是唯一事实源）；本模块仅为
backfill 等迁移工具提供老条目的 metadata.json 读取。
"""
import json
from pathlib import Path


def load_entry_metadata(slug: str, wiki_dir: Path) -> dict | None:
    """读取单条目的 metadata.json；不存在或损坏返回 None。"""
    wiki_dir = Path(wiki_dir)
    path = wiki_dir / "artifacts" / slug / "metadata.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
