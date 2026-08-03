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


def _spawn_detached(wiki_dir: Path, cmd: list, log_path: Path = None) -> None:
    """以分离子进程执行固定命令（list argv，无 shell）。"""
    wiki_dir = Path(wiki_dir)
    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(wiki_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = open(log_path, "ab") if log_path else subprocess.DEVNULL
    kwargs = dict(stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                  env=env, cwd=str(wiki_dir.parent), close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _default_spawner(wiki_dir: Path, slug: str, force: bool = False) -> None:
    """分离子进程执行 `cli.py --json survey --id <slug> --auto`（端到端）。"""
    wiki_dir = Path(wiki_dir)
    cli = Path(__file__).resolve().parent.parent / "cli.py"
    log_dir = paths.survey_dir(slug, wiki_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(cli), "--json", "survey", "--id", slug, "--auto"]
    if force:
        cmd.append("--force")
    _spawn_detached(wiki_dir, cmd, log_path=log_dir / "collect.log")


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


_NAME_MAX = 64


def handle_track(wiki_dir, payload: dict, client_ip: str = "127.0.0.1", spawner=None):
    """POST /api/track：从网页实体发起跟踪主题（幂等）。返回 (http_code, json_dict)。"""
    from scripts import tracking as TR

    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may create tracking topics"}
    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    kind = str(payload.get("kind") or "person").strip() or "person"
    if not name:
        return 400, {"ok": False, "error": "MISSING_NAME", "message": "name is required"}
    if len(name) > _NAME_MAX or "\n" in name:
        return 400, {"ok": False, "error": "INVALID_NAME", "message": "name too long or multiline"}
    ws = Path(wiki_dir)
    slug = TR.slugify_name(name)
    if TR.load_topic(slug, ws):
        return 200, {"ok": True, "exists": True, "slug": slug, "name": name}
    cli = Path(__file__).resolve().parent.parent / "cli.py"
    cmd = [sys.executable, str(cli), "--json", "track", "--name", name,
           "--kind", kind, "--auto"]
    if spawner:
        spawner(ws, cmd)
    else:
        log_dir = paths.tracking_topic_dir(slug, ws)
        log_dir.mkdir(parents=True, exist_ok=True)
        _spawn_detached(ws, cmd, log_path=log_dir / "create.log")
    return 202, {"ok": True, "slug": slug, "name": name, "state": "creating"}


def _existing_post_for_topic(ws: Path, topic: str) -> str:
    """已写过同 topic 的 post → 返回 stem（幂等）；否则 ''。"""
    posts_dir = paths.posts_dir(ws)
    if not posts_dir.exists():
        return ""
    for meta in posts_dir.glob("*.meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            continue
        tr = m.get("trigger") or {}
        if tr.get("kind") == "topic" and (tr.get("topic") or "").strip() == topic:
            return m.get("stem") or meta.stem.replace(".meta", "")
    return ""


def handle_post(wiki_dir, payload: dict, client_ip: str = "127.0.0.1", spawner=None):
    """POST /api/post：网页发起 post 写作（topic 或 records 融合）。返回 (http_code, json_dict)。"""
    if client_ip not in LOOPBACK_IPS:
        return 403, {"ok": False, "error": "FORBIDDEN",
                     "message": "only loopback clients may trigger posts"}
    payload = payload or {}
    ws = Path(wiki_dir)
    cli = Path(__file__).resolve().parent.parent / "cli.py"
    cmd = [sys.executable, str(cli), "--json", "post"]

    topic = str(payload.get("topic") or "").strip()
    records = payload.get("records")
    if records and isinstance(records, list):
        ids = [str(x).strip() for x in records if str(x).strip()]
        if not ids or len(ids) > 8 or any(not ID_RE.match(i) for i in ids):
            return 400, {"ok": False, "error": "INVALID_RECORDS",
                         "message": "records must be 1-8 entry ids"}
        cmd += ["--records", ",".join(ids), "--auto"]
        trigger_desc = f"records fusion ({len(ids)})"
    elif topic:
        if len(topic) > 200:
            return 400, {"ok": False, "error": "INVALID_TOPIC", "message": "topic too long"}
        existing = _existing_post_for_topic(ws, topic)
        if existing:
            return 200, {"ok": True, "exists": True, "stem": existing}
        cmd += ["--topic", topic, "--auto"]
        trigger_desc = topic
    else:
        return 400, {"ok": False, "error": "MISSING_TRIGGER",
                     "message": "topic or records required"}

    if spawner:
        spawner(ws, cmd)
    else:
        log_dir = paths.post_staging_dir(ws)
        log_dir.mkdir(parents=True, exist_ok=True)
        _spawn_detached(ws, cmd, log_path=log_dir / "post.log")
    return 202, {"ok": True, "trigger": trigger_desc, "state": "writing"}
