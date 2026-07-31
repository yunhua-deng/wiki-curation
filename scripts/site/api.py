#!/usr/bin/env python3
"""
scripts/site/api.py — wiki 本地服务的薄 JSON API（纯函数，不起 socket）。

当前仅 survey 发起/状态两个端点；serve.py 的 SPAHandler 只做 HTTP 壳。
spawner 可注入（测试），默认 _default_spawner 起分离子进程跑 cli.py survey。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts import paths
from scripts.records import survey as DV

ID_RE = re.compile(r"^[\w-]+$")
LOOPBACK_IPS = {"127.0.0.1", "::1"}


def _default_spawner(wiki_dir: Path, slug: str, force: bool = False) -> None:
    """分离子进程执行 `cli.py --json survey --id <slug> --auto`（端到端）。"""
    wiki_dir = Path(wiki_dir)
    cli = Path(__file__).resolve().parent.parent / "cli.py"
    log_dir = paths.survey_dir(slug, wiki_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "collect.log", "ab")
    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(wiki_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, str(cli), "--json", "survey", "--id", slug, "--auto"]
    if force:
        cmd.append("--force")
    kwargs = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                  env=env, cwd=str(wiki_dir.parent), close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def handle_survey_request(wiki_dir, payload: dict, client_ip: str = "127.0.0.1", spawner=None):
    """POST /api/survey。返回 (http_code, json_dict)。"""
    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may trigger surveys"}
    payload = payload or {}
    slug = str(payload.get("id") or "")
    force = bool(payload.get("force"))
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\\w-]+$"}
    ws = Path(wiki_dir)
    if not paths.record_path(slug, ws).exists():
        return 404, {"ok": False, "error": "RECORD_MISSING",
                     "message": f"record.json not found: {slug}"}
    state = (DV.read_status(slug, ws) or {}).get("state")
    # 冲突判定优先级：进行中的进程 > 已有 survey 页面 > 已排队任务
    if state in ("collecting", "writing") and not force:
        return 409, {"ok": False, "error": "SURVEY_RUNNING", "state": state,
                     "message": f"survey is {state} right now"}
    if paths.survey_md_path(slug, ws).exists() and not force:
        return 409, {"ok": False, "error": "SURVEY_EXISTS",
                     "message": "survey already exists (pass force=true to regenerate)"}
    if state == "awaiting_agent" and not force:
        return 409, {"ok": False, "error": "SURVEY_RUNNING", "state": state,
                     "message": "survey is queued for an agent"}
    (spawner or _default_spawner)(ws, slug, force=force)
    return 202, {"ok": True, "id": slug, "state": "collecting"}


def handle_survey_status(wiki_dir, slug: str):
    """GET /api/survey/status?id=<slug>。返回 (http_code, json_dict)。"""
    slug = str(slug or "")
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\\w-]+$"}
    ws = Path(wiki_dir)
    data = DV.survey_status(slug, ws)
    data["ok"] = True
    return 200, data


def handle_add_link(wiki_dir, payload: dict, client_ip: str = "127.0.0.1", spawner=None):
    """POST /api/record-links：手动添加链接到 record 图谱，可选联动更新综述。

    update_survey=true 时复用 survey 端到端管线：已有 survey.md → force 重生成；
    否则首次自动综述。返回 (http_code, json_dict)。
    """
    from scripts.records import link_ops

    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may add links"}
    payload = payload or {}
    slug = str(payload.get("id") or "")
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\w-]+$"}
    url = str(payload.get("url") or "").strip()
    role = str(payload.get("role") or "related")
    update_survey = bool(payload.get("update_survey"))
    ws = Path(wiki_dir)
    try:
        result = link_ops.add_manual_link(slug, url, role=role, ws=ws, db_path=paths.db_path(ws))
    except link_ops.LinkOpError as e:
        http = 404 if e.code == "RECORD_MISSING" else (409 if e.code in ("LINK_EXISTS", "CANONICAL_CONFLICT") else 400)
        return http, {"ok": False, "error": e.code, "message": str(e)}
    data = {"ok": True, **result}
    if update_survey:
        has_md = paths.survey_md_path(slug, ws).exists()
        (spawner or _default_spawner)(ws, slug, force=has_md)
        data["survey"] = {"state": "collecting", "force": has_md}
    return 200, data


def handle_watch(wiki_dir, payload: dict, client_ip: str = "127.0.0.1"):
    """POST /api/watch：设置/切换特别关注，并同步重建站点。返回 (http_code, json_dict)。"""
    from scripts.wiki_index import store

    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may toggle watch"}
    payload = payload or {}
    slug = str(payload.get("id") or "")
    if not ID_RE.match(slug):
        return 400, {"ok": False, "error": "INVALID_ID", "message": "id must match ^[\w-]+$"}
    ws = Path(wiki_dir)
    db = paths.db_path(ws)
    cur = store.get_entry(db, slug)
    if not cur:
        return 404, {"ok": False, "error": "ENTRY_NOT_FOUND",
                     "message": f"entry not found: {slug}"}
    target = bool(payload["on"]) if "on" in payload else not bool(cur.get("watched"))
    e = store.set_watched(db, slug, target)
    from scripts.publish.lock import PublishLock
    from scripts.site.build import build_site
    with PublishLock(timeout=30):
        build_site(db, ws)
    return 200, {"ok": True, "id": slug, "watched": bool(e["watched"]),
                 "watched_at": e.get("watched_at") or ""}
