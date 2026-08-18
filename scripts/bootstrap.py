#!/usr/bin/env python3
"""bootstrap.py — cli.py init：把任意目录变为 wiki 工作区。

创建 wiki/ 骨架目录、初始化 wiki.db、复制模板文件（幂等，不覆盖已有文件），
并返回 AGENTS.md 接入片段文本。不写工作区根 AGENTS.md、不 git init、不装依赖。
"""
from pathlib import Path

from scripts import paths
from scripts.wiki_index import ensure_schema

SKELETON_DIRS = ["artifacts", "data", "entities", "failures", "docs"]

# assets/templates/ 下的源文件名 → wiki/ 下的目标相对路径
TEMPLATES = {
    "wiki-README.md": "README.md",
    "failures-TEMPLATE.md": "failures/TEMPLATE.md",
    "gitignore": ".gitignore",
}


def init_workspace(target) -> dict:
    """在 target（wiki/ 根目录）执行 bootstrap，返回结果 dict。

    幂等：已存在的目录 / 文件 / wiki.db 不覆盖，记入 skipped。
    模板文件缺失（skill 安装损坏）时抛 FileNotFoundError，由 cli 层转为 ok:false。
    """
    target = Path(target).resolve()
    created, skipped = [], []

    for d in SKELETON_DIRS:
        p = target / d
        if p.is_dir():
            skipped.append(str(p))
        else:
            p.mkdir(parents=True, exist_ok=True)
            created.append(str(p))

    db = target / "data" / "wiki.db"
    db_existed = db.exists()
    ensure_schema(db)  # 幂等：CREATE IF NOT EXISTS + 迁移跳过已应用版本
    (skipped if db_existed else created).append(str(db))

    tpl_dir = paths.assets_dir() / "templates"
    # 模板缺失时在此抛 FileNotFoundError
    snippet = (tpl_dir / "agents-snippet.md").read_text(encoding="utf-8")
    for src_name, rel_dest in TEMPLATES.items():
        dest = target / rel_dest
        if dest.exists():
            skipped.append(str(dest))
            continue
        src = tpl_dir / src_name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(str(dest))

    return {
        "workspace": str(target),
        "db_path": str(db),
        "created": created,
        "skipped": skipped,
        "agents_snippet": snippet,
    }
