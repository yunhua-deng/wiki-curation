#!/usr/bin/env python3
"""
scripts/records/dive.py — record 深度解读（dive）的核心确定性逻辑。

职责边界（对齐 record 提取）：系统负责链接选择、素材采集、任务生成、
结构校验、发布与状态机；dive.md 由执行 task 的 agent 手写，脚本永不调 LLM。

状态机（artifacts/{slug}/dive/status.json）：
  collecting → awaiting_agent → done | failed
  awaiting_agent 即队列（网页/CLI 触发统一表达）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import paths
from scripts.records import schema as RS
from scripts.records.schema import normalize_url

DIVE_MD_MAX_BYTES = 40 * 1024
REQUIRED_H2 = ["TL;DR", "核心内容", "分来源摘要", "原始出处"]
DEFAULT_MAX_LINKS = 5

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"


class DiveError(Exception):
    """带机器可读码的 dive 错误。code 供 CLI/API 输出；next_cmd 指引下一步。"""

    def __init__(self, code: str, message: str, next_cmd: str = None):
        super().__init__(message)
        self.code = code
        self.next_cmd = next_cmd


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================
# 状态机
# ============================================================

def read_status(slug: str, ws=None) -> dict:
    """读取 dive 状态；不存在/损坏返回 {}。"""
    path = paths.dive_status_path(slug, ws)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_status(slug: str, ws, state: str, detail: dict = None, error: str = None) -> None:
    """写 dive 状态机。error 为 None 时清除旧 error 字段。"""
    path = paths.dive_status_path(slug, ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "updated_at": _now_iso()}
    if detail is not None:
        payload["detail"] = detail
    if error:
        payload["error"] = error
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 链接选择
# ============================================================

def select_dive_links(record_links: list, max_links: int = DEFAULT_MAX_LINKS) -> list:
    """从 record links 选出本次 dive 要抓的链接。

    canonical 优先、related 次之（各组内保持原顺序）；跳过 fetched==1
    （record 原 raw/ 已有材料，不重复抓取）；normalize_url 去重；封顶 max_links。
    """
    canonical, related = [], []
    seen = set()
    for link in record_links or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not url or link.get("fetched") == 1:
            continue
        nu = normalize_url(url)
        if not nu or nu in seen:
            continue
        seen.add(nu)
        (canonical if link.get("role") == "canonical" else related).append(dict(link))
    return (canonical + related)[:max_links]
