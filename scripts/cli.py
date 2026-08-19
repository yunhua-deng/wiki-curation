#!/usr/bin/env python3
"""
skills/wiki-curation/scripts/cli.py — Wiki 工作流统一 CLI（v3.1 record-only）。

文章路径已于 v3.1 废除。使用独立 `skills/article-writer` skill 生成文章。

Usage:
    python skills/wiki-curation/scripts/cli.py --json run --id <slug>
    python skills/wiki-curation/scripts/cli.py --json doctor --quick
    python skills/wiki-curation/scripts/cli.py --json stats
    python skills/wiki-curation/scripts/cli.py manifest
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

if __package__ is None:
    SKILL_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(SKILL_ROOT))

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


def _clear_stale_pycache(root: Path) -> None:
    for pycache in root.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)


_clear_stale_pycache(SCRIPT_DIR)

from scripts.lib import run_cmd
from scripts import paths
from scripts.bootstrap import init_workspace
from scripts.site.build import build_site
from scripts.site.serve import serve

_SCRIPT_PATHS = {
    "orchestrate.py": "exec/orchestrate.py",
    "classify_source.py": "intake/classify_source.py",
    "collect_materials.py": "exec/collect_materials.py",
    "doctor.py": "doctor.py",
    "wiki_db.py": "wiki_db.py",
}


def _script(name: str) -> Path:
    return SCRIPT_DIR / _SCRIPT_PATHS.get(name, name)


def _resolve_cwd(workspace: str = None) -> str:
    if workspace:
        return str(Path(workspace).parent)
    return str(Path.cwd())


def _run_script(name: str, args: list, json_mode: bool = False, quiet: bool = False,
                timeout: int = 120, workspace: str = None) -> dict:
    cmd = [sys.executable, str(_script(name))]
    if json_mode: cmd.append("--json")
    cmd.extend(args)
    env = os.environ.copy()
    skill_root = str(SCRIPT_DIR.parent)
    env["PYTHONPATH"] = skill_root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if workspace:
        env["WIKI_WORKSPACE"] = workspace
    elif "WIKI_WORKSPACE" not in env:
        env["WIKI_WORKSPACE"] = str(Path.cwd() / "wiki")
    r = run_cmd(cmd, timeout=timeout, cwd=_resolve_cwd(workspace), env=env)
    if r["ok"]:
        stdout = r.get("stdout", "").strip()
        try: data = json.loads(stdout) if stdout else None
        except json.JSONDecodeError: data = stdout
        return {"ok": True, "data": data}
    err_text = (r.get("stderr") or r.get("stdout") or "").strip()
    detail = {}
    try:
        if err_text: detail = json.loads(err_text)
    except Exception:
        detail = {"stderr": r.get("stderr", ""), "stdout": r.get("stdout", "")}
    return {"ok": False, "error": detail.get("error") or "COMMAND_FAILED",
            "message": detail.get("message") or (err_text.splitlines()[0] if err_text else "command failed"),
            "detail": detail, "next_cmd": detail.get("next_cmd")}


def _print_result(result: dict, json_mode: bool):
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("ok"):
        data = result.get("data")
        if isinstance(data, str): print(data)
        elif data is not None: print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"❌ {result.get('error')}: {result.get('message')}", file=sys.stderr)
        if result.get("next_cmd"):
            print(f"   next: {result['next_cmd']}", file=sys.stderr)


def _wiki_db_cmd(subcommand: str, args, extra: list = None, timeout: int = 120) -> dict:
    script_args = []
    if args.json: script_args.append("--json")
    script_args.append(subcommand)
    if extra: script_args.extend(extra)
    return _run_script("wiki_db.py", script_args, json_mode=args.json,
                       quiet=args.quiet, timeout=timeout, workspace=args.workspace)


# ============================================================
# 核心命令
# ============================================================

def cmd_run(args) -> int:
    script_args = ["run"]
    if args.id: script_args += ["--id", args.id]
    if getattr(args, "mode", None): script_args += ["--mode", args.mode]
    if getattr(args, "append_to", None): script_args += ["--append-to", args.append_to]
    if getattr(args, "depth", None): script_args += ["--depth", args.depth]
    if args.max_depth is not None: script_args += ["--max-depth", str(args.max_depth)]
    if args.force_collect: script_args.append("--force-collect")
    if args.json: script_args.append("--json")
    r = _run_script("orchestrate.py", script_args, json_mode=False,
                    quiet=args.quiet, timeout=180, workspace=args.workspace)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_article(args) -> int:
    """v3.1：文章写作已废除，输出错误并指引独立 skill。"""
    msg = "文章写作已于 v3.1 废除。请使用独立的 article-writer skill。当前 wiki skill 仅支持记录提取（record）。"
    _print_result({"ok": False, "error": "DEPRECATED_MODE", "message": msg}, args.json)
    return 1


def cmd_interpret(args) -> int:
    msg = "文章写作已于 v3.1 废除。请使用独立的 article-writer skill。"
    _print_result({"ok": False, "error": "DEPRECATED_MODE", "message": msg}, args.json)
    return 1


def cmd_verify_output(args) -> int:
    msg = "文章格式校验已于 v3.1 废除。常用 cli.py publish --id <slug> 发布记录。"
    _print_result({"ok": False, "error": "DEPRECATED_MODE", "message": msg}, args.json)
    return 1


def cmd_classify(args) -> int:
    r = _run_script("classify_source.py", ["--input", args.input],
                    json_mode=args.json, quiet=args.quiet, workspace=args.workspace)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_collect(args) -> int:
    script_args = ["--slug", args.slug, "--input-type", args.input_type,
                   "--source-type", args.source_type, "--input", args.input]
    if args.max_depth is not None: script_args += ["--max-depth", str(args.max_depth)]
    r = _run_script("collect_materials.py", script_args, json_mode=args.json,
                    quiet=args.quiet, timeout=180, workspace=args.workspace)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_add(args) -> int:
    extra = []
    if args.input:
        for inp in args.input: extra += ["--input", inp]
    if args.inputs_file: extra += ["--inputs-file", args.inputs_file]
    if args.append_to: extra += ["--append-to", args.append_to]
    extra += ["--input-type", args.input_type, "--source-type", args.source_type, "--depth", "brief"]
    if args.source_prompt: extra += ["--source-prompt", args.source_prompt]
    if args.id: extra += ["--id", args.id]
    r = _wiki_db_cmd("add", args, extra)
    if not r.get("ok"):
        _print_result(r, args.json)
        return 1

    entry = r.get("data", {}) or {}
    slug = entry.get("id", "")
    if slug and args.input_type in ("local-file", "local_file", "local"):
        ws = paths.get_workspace()
        raw_dir = paths.raw_dir(slug, ws)
        raw_dir.mkdir(parents=True, exist_ok=True)
        for src_str in (args.input or []):
            src = Path(src_str)
            if not src.exists(): continue
            if src.is_file():
                if src.suffix.lower() in ('.zip', '.tar', '.tar.gz', '.tgz', '.7z', '.rar'):
                    import zipfile, tarfile
                    shutil.copy2(str(src), str(raw_dir / src.name))
                    if src.suffix.lower() == '.zip':
                        with zipfile.ZipFile(str(raw_dir / src.name), 'r') as z:
                            z.extractall(str(raw_dir / src.stem))
                    elif src.suffix.lower() in ('.tar', '.tar.gz', '.tgz'):
                        with tarfile.open(str(raw_dir / src.name), 'r:*') as t:
                            t.extractall(str(raw_dir / src.stem))
                else:
                    shutil.copy2(str(src), str(raw_dir / src.name))
            elif src.is_dir():
                shutil.copytree(str(src), str(raw_dir / src.name), dirs_exist_ok=True)

    # 自动召回
    recall_data = None
    if not getattr(args, "no_recall", False) and slug and not entry.get("append_to"):
        try:
            from scripts.records.recall import recall
            db = paths.db_path(paths.get_workspace())
            joined = entry.get("joined_input") or "\n".join(args.input or [])
            recall_data = recall(db, joined, limit=5, exclude_id=slug)
            # v3.7：召回结果持久化为 RECALL 事件，publish 时注入 record.preview 供站点展示
            if recall_data is not None:
                try:
                    from scripts import wiki_index
                    wiki_index.record_event(db, slug, "RECALL",
                                            {"query": joined[:300], **recall_data})
                except Exception as ee:
                    print(f"RECALL event write failed: {ee}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ 自动召回失败（不影响入库）: {e}", file=sys.stderr)

    if args.json:
        if recall_data is not None:
            r.setdefault("data", {})["recall"] = recall_data
        _print_result(r, args.json)
    else:
        _print_result(r, args.json)
        if recall_data and recall_data.get("matches"):
            print("\n🔁 相似历史条目:")
            for m in recall_data["matches"]:
                reasons = "; ".join(f"{x['kind']}: {x['detail'][:50]}" for x in m["reasons"][:2])
                print(f"  - {m['id']} — {m['title'][:50]} (score={m['score']}) [{reasons}]")
        elif recall_data is not None:
            print("\n🔁 无相似历史条目")
    return 0


def cmd_pop(args) -> int:
    extra = ["--limit", str(args.limit)]
    r = _wiki_db_cmd("pop", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_list(args) -> int:
    extra = []
    if args.limit: extra += ["--limit", str(args.limit)]
    if args.topic_type: extra += ["--topic-type", args.topic_type]
    if args.input_type: extra += ["--input-type", args.input_type]
    if args.source_type: extra += ["--source-type", args.source_type]
    if args.status: extra += ["--status", args.status]
    if args.all: extra.append("--all")
    r = _wiki_db_cmd("list", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_search(args) -> int:
    extra = [args.query, "--limit", str(args.limit)]
    r = _wiki_db_cmd("search", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_stats(args) -> int:
    r = _wiki_db_cmd("stats", args)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_sync(args) -> int:
    extra = ["--rebuild"] if args.rebuild else []
    r = _wiki_db_cmd("sync", args, extra, timeout=300)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_requeue(args) -> int:
    extra = ["--id", args.id]
    if args.clear_md: extra.append("--clear-md")
    r = _wiki_db_cmd("requeue", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_delete(args) -> int:
    extra = ["--id", args.id]
    r = _wiki_db_cmd("delete", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_update(args) -> int:
    extra = ["--id", args.id]
    for attr, flag in [("title","title"),("overview","overview"),("topic_type","topic-type"),
        ("date","date"),("ver","ver"),("sources","sources"),("raw","raw"),
        ("file","file"),("depth","depth"),("tags","tags"),
        ("source_input","source_input"),("source_prompt","source_prompt"),
        ("input_type","input-type"),("source_type","source-type"),
        ("status","status"),("error","error")]:
        v = getattr(args, attr, None)
        if v is not None: extra += [f"--{flag}", v]
    if args.materials_ready is not None: extra += ["--materials_ready", str(args.materials_ready)]
    r = _wiki_db_cmd("update", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_status(args) -> int:
    extra = ["--id", args.id]
    if args.set: extra += ["--set", args.set]
    if args.error: extra += ["--error", args.error]
    r = _wiki_db_cmd("status", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_events(args) -> int:
    extra = ["--id", args.id]
    if args.action: extra += ["--action", args.action]
    if args.limit is not None: extra += ["--limit", str(args.limit)]
    r = _wiki_db_cmd("events", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_record_event(args) -> int:
    extra = ["--id", args.id, "--action", args.action]
    if args.detail: extra += ["--detail", args.detail]
    r = _wiki_db_cmd("record-event", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_publish(args) -> int:
    extra = ["--id", args.id]
    if getattr(args, "depth", None): extra += ["--depth", args.depth]
    if getattr(args, "spec", None): extra += ["--spec", args.spec]
    if getattr(args, "title", None): extra += ["--title", args.title]
    if getattr(args, "site_only", False): extra.append("--site-only")
    r = _wiki_db_cmd("publish", args, extra, timeout=60)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_index(args) -> int:
    extra = (["--output", args.output] if args.output else [])
    r = _wiki_db_cmd("index", args, extra, timeout=60)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_site(args) -> int:
    ws = Path(args.workspace).resolve() if args.workspace else paths.get_workspace()
    db = paths.db_path(ws)
    if args.stop:
        if not args.pid_file:
            _print_result({"ok": False, "error": "MISSING_PID_FILE",
                          "message": "--stop requires --pid-file"}, args.json)
            return 1
        try:
            from scripts.site import serve as serve_mod
            serve_mod.stop_server(args.pid_file)
            if not args.json:
                print(f"✅ wiki server stopped: {args.pid_file}")
        except Exception as e:
            _print_result({"ok": False, "error": "STOP_FAILED", "message": str(e)}, args.json)
            return 1
        if args.json:
            _print_result({"ok": True, "stopped": True, "pid_file": args.pid_file}, args.json)
        return 0
    try:
        out_dir = build_site(db, ws, export=args.export)
        if not args.json: print(f"✅ wiki site built: {out_dir}")
    except Exception as e:
        _print_result({"ok": False, "error": "SITE_BUILD_FAILED", "message": str(e)}, args.json)
        return 1
    if args.serve:
        try:
            serve(ws, port=args.port, open_browser=args.open, quiet=args.quiet, pid_file=args.pid_file)
        except KeyboardInterrupt: pass
        except Exception as e:
            _print_result({"ok": False, "error": "SERVE_FAILED", "message": str(e)}, args.json)
            return 1
    if args.json:
        _print_result({"ok": True, "site": str(out_dir), "served": args.serve}, args.json)
    return 0


def cmd_dedup(args) -> int:
    extra = ["--input", args.input]
    r = _wiki_db_cmd("dedup", args, extra)
    _print_result(r, args.json)
    return 0 if r.get("ok") else 1


def cmd_recall(args) -> int:
    try:
        from scripts.records.recall import recall
        db = paths.db_path(paths.get_workspace())
        result = recall(db, args.input, limit=args.limit)
        if args.json:
            _print_result({"ok": True, "data": result}, args.json)
        else:
            for m in result.get("matches", []):
                reasons = "; ".join(f"{x['kind']}: {x['detail'][:50]}" for x in m["reasons"][:3])
                print(f"  {m['id']}  [{m['score']}]  {m['title'][:60]}")
                print(f"      {reasons}")
            if not result.get("matches"):
                print("🔁 无相似历史条目")
        return 0
    except Exception as e:
        _print_result({"ok": False, "error": "RECALL_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_verify_links(args) -> int:
    try:
        from scripts.records.verify_links import verify_entry_links
        db = paths.db_path(paths.get_workspace())
        result = verify_entry_links(db, args.id, limit=args.limit)
        if args.json:
            _print_result({"ok": True, "data": result}, args.json)
        else:
            print(f"✅ {args.id}: checked={result['checked']} ok={result['ok']} fail={result['fail']}")
            for r in result["results"]:
                print(f"  {'✅' if r['verified'] else '❌'} {r['url']}")
        return 0
    except Exception as e:
        _print_result({"ok": False, "error": "VERIFY_LINKS_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_analyze(args) -> int:
    """analyze：主题聚簇 / 去重候选。"""
    try:
        from scripts.records import analyze as AZ
        db = paths.db_path(paths.get_workspace())
        if getattr(args, "discover", False):
            result = AZ.discover_topics(db, recent_days=args.days, top_n=args.limit)
            _print_result({"ok": True, "data": {"topics": result, "days": args.days}}, args.json)
            return 0
        if getattr(args, "dedup", False):
            result = AZ.dedup_candidates(db, min_score=args.min_score, limit=args.limit)
            _print_result({"ok": True, "data": {"candidates": result, "count": len(result)}}, args.json)
            return 0
        if not args.topic:
            _print_result({"ok": False, "error": "MISSING_TOPIC",
                           "message": "analyze 需要 --topic，或使用 --dedup"}, args.json)
            return 1
        result = AZ.cluster(db, args.topic, limit=args.limit)
        _print_result({"ok": True, "data": result}, args.json)
        return 0
    except Exception as e:
        _print_result({"ok": False, "error": "ANALYZE_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_star(args) -> int:
    try:
        from scripts.records.star_github import star_entry
        from scripts.records import schema as RS
        ws = paths.get_workspace()
        db = paths.db_path(ws)
        record = RS.load_record(args.id, ws) or {}
        result = star_entry(db, args.id, record)
        if args.json:
            _print_result({"ok": True, "data": result}, args.json)
        else:
            if result.get("skipped") == "no_github":
                print(f"⭐ {args.id}: 无 canonical GitHub 仓库，跳过")
            elif result.get("skipped") == "no_token":
                print(f"⭐ {args.id}: GITHUB_TOKEN 未设置，跳过 (repos={result.get('repos')})")
            else:
                print(f"⭐ {args.id}: starred={len(result['starred'])} "
                      f"already={len(result['already'])} failed={len(result['failed'])}")
                for r in result["starred"]: print(f"  ⭐ {r}")
                for r in result["already"]: print(f"  ✓ {r} (已标星)")
                for f in result["failed"]: print(f"  ❌ {f['repo']}: {f['error']}")
        return 0
    except Exception as e:
        _print_result({"ok": False, "error": "STAR_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_add_link(args) -> int:
    """add-link：手动添加链接到 record 图谱。"""
    try:
        from scripts.records import link_ops
        ws = paths.get_workspace()
        result = link_ops.add_manual_link(args.id, args.url, role=args.role,
                                          ws=ws, db_path=paths.db_path(ws))
        data = dict(result)
        _print_result({"ok": True, "data": data}, args.json)
        return 0
    except Exception as e:
        from scripts.records.link_ops import LinkOpError
        if isinstance(e, LinkOpError):
            _print_result({"ok": False, "error": e.code, "message": str(e)}, args.json)
        else:
            _print_result({"ok": False, "error": "ADD_LINK_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_entities(args) -> int:
    """entities：实体综合层——list / --name 聚合 / watch 管理 / --summary 摘要。"""
    from scripts import entity_summary as ES
    try:
        ws = paths.get_workspace()
        db = paths.db_path(ws)
        if getattr(args, "watch", None):
            r = ES.watch_entity(db, args.watch,
                                type=getattr(args, "entity_type", "") or "",
                                note=getattr(args, "note", "") or "")
            _print_result({"ok": True, "data": r}, args.json)
            return 0
        if getattr(args, "unwatch", None):
            removed = ES.unwatch_entity(db, args.unwatch)
            _print_result({"ok": True, "data": {"name": args.unwatch, "removed": removed}}, args.json)
            return 0
        if getattr(args, "summary", False):
            if getattr(args, "watched", False):
                names = [w["name"] for w in ES.list_watched(db)]
                written, failed = [], []
                for n in names:
                    try:
                        written.append(ES.auto_write_summary(n, ws, db))
                    except Exception as e:
                        failed.append({"name": n, "error": str(e)})
                _print_result({"ok": not failed,
                               "data": {"written": written, "failed": failed}}, args.json)
                return 0 if not failed else 1
            if getattr(args, "name", None):
                r = ES.auto_write_summary(args.name, ws, db)
                _print_result({"ok": True, "data": r}, args.json)
                return 0
            _print_result({"ok": False, "error": "MISSING_ARGS",
                           "message": "entities --summary 需要 --name X 或 --watched"}, args.json)
            return 1
        if getattr(args, "watched", False):
            items = ES.list_watched(db)
            _print_result({"ok": True, "data": {"watched": items, "count": len(items)}}, args.json)
            return 0
        if getattr(args, "name", None):
            agg = ES.aggregate_entity(db, args.name, ws)
            _print_result({"ok": True, "data": agg}, args.json)
            return 0
        # 默认：--list
        idx = ES.entity_index(db)
        watched_names = {w["name"] for w in ES.list_watched(db)}
        items = [{"name": n, "type": s["type"], "record_count": len(s["entries"]),
                  "watched": n in watched_names}
                 for n, s in sorted(idx.items(), key=lambda kv: -len(kv[1]["entries"]))]
        _print_result({"ok": True, "data": {"entities": items, "count": len(items)}}, args.json)
        return 0
    except ES.EntityError as e:
        _print_result({"ok": False, "error": e.code, "message": str(e)}, args.json)
        return 1
    except Exception as e:
        _print_result({"ok": False, "error": "ENTITIES_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_clean_entities(args) -> int:
    """clean-entities：批量清洗存量 record.json 实体（alias 归一 + suppress，默认 dry-run）。"""
    try:
        from scripts.records.clean_entities import clean_entities
        from scripts.publish.lock import PublishLock, LockBusyError
        ws = paths.get_workspace()
        db = paths.db_path(ws)
        entry_id = getattr(args, "id", None)
        if getattr(args, "apply", False):
            try:
                with PublishLock(timeout=30):
                    data = clean_entities(db, ws, entry_id=entry_id, apply=True)
            except LockBusyError as e:
                _print_result({"ok": False, "error": "BUSY", "message": str(e)}, args.json)
                return 1
        else:
            data = clean_entities(db, ws, entry_id=entry_id, apply=False)
        _print_result({"ok": True, "data": data}, args.json)
        return 0
    except Exception as e:
        _print_result({"ok": False, "error": "CLEAN_ENTITIES_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_watch(args) -> int:
    """watch：特别关注——toggle / --on / --off / 无 --id 列出全部。"""
    try:
        from scripts.wiki_index import store
        ws = paths.get_workspace()
        db = paths.db_path(ws)
        if not getattr(args, "id", None):
            items = store.list_watched(db)
            data = {"watched": [{"id": e["id"], "title": e.get("title") or "",
                                 "watched_at": e.get("watched_at") or ""} for e in items],
                    "count": len(items)}
            _print_result({"ok": True, "data": data}, args.json)
            return 0
        if getattr(args, "on", False):
            target = True
        elif getattr(args, "off", False):
            target = False
        else:
            cur = store.get_entry(db, args.id)
            if not cur:
                raise ValueError(f"entry not found: {args.id}")
            target = not bool(cur.get("watched"))
        e = store.set_watched(db, args.id, target)
        _print_result({"ok": True, "data": {"id": args.id, "watched": bool(e["watched"]),
                                            "watched_at": e.get("watched_at") or ""}}, args.json)
        return 0
    except ValueError as e:
        _print_result({"ok": False, "error": "ENTRY_NOT_FOUND", "message": str(e)}, args.json)
        return 1
    except Exception as e:
        _print_result({"ok": False, "error": "WATCH_FAILED", "message": str(e)}, args.json)
        return 1


def cmd_doctor(args) -> int:
    script_args = []
    if args.quick: script_args.append("--quick")
    if args.since: script_args += ["--since", args.since]
    if args.fix_plan: script_args.append("--fix-plan")
    cmd = [sys.executable, str(_script("doctor.py"))]
    if args.json: cmd.append("--json")
    cmd.extend(script_args)
    env = os.environ.copy()
    skill_root = str(SCRIPT_DIR.parent)
    env["PYTHONPATH"] = skill_root + os.pathsep + env.get("PYTHONPATH", "")
    if args.workspace: env["WIKI_WORKSPACE"] = args.workspace
    elif "WIKI_WORKSPACE" not in env: env["WIKI_WORKSPACE"] = str(Path.cwd() / "wiki")
    r = run_cmd(cmd, timeout=300, cwd=_resolve_cwd(args.workspace), env=env)
    stdout = (r.get("stdout") or "").strip()
    try: data = json.loads(stdout) if stdout else None
    except json.JSONDecodeError: data = None
    if data is not None:
        result = {"ok": True, "data": data}
    else:
        err_text = (r.get("stderr") or stdout or "doctor produced no output").strip()
        result = {"ok": False, "error": "COMMAND_FAILED",
                  "message": err_text.splitlines()[0] if err_text else "doctor failed",
                  "detail": {"stderr": r.get("stderr", ""), "stdout": r.get("stdout", "")}}
    _print_result(result, args.json)
    return 0 if result.get("ok") else 1


def cmd_init(args) -> int:
    ws = paths.get_workspace()
    try:
        data = init_workspace(ws)
    except FileNotFoundError as e:
        _print_result({"ok": False, "error": "TEMPLATE_MISSING",
                       "message": f"模板文件缺失（skill 安装损坏？）: {e.filename}",
                       "next_cmd": "重新 clone 或 git pull skill repo"}, args.json)
        return 1
    if args.json:
        _print_result({"ok": True, "data": data}, True)
    else:
        print(f"✅ wiki 工作区已就绪: {data['workspace']}")
        print(f"   wiki.db: {data['db_path']}")
        print(f"   created: {len(data['created'])} 项; skipped（已存在，未覆盖）: {len(data['skipped'])} 项")
        print("\n===== 将以下片段接入工作区根 AGENTS.md =====\n")
        print(data["agents_snippet"])
    return 0


def cmd_manifest(args) -> int:
    manifest = {
        # CLI manifest 独立语义版本：随命令清单/JSON 契约变更递增，
        # 与 assets/site.js 注释里的站点版本号（v3.xx）是两套编号，不要对齐。
        "version": "3.6",
        "entry": "python skills/wiki-curation/scripts/cli.py",
        "global_flags": ["--json", "--quiet", "--workspace PATH"],
        "commands": [
            {"name": "init", "args": [],
             "description": "初始化 wiki 工作区骨架（目录/wiki.db/模板，幂等）+ 输出 AGENTS.md 接入片段"},
            {"name": "entities", "args": ["--list", "--name", "--watch", "--unwatch", "--watched", "--summary", "--type", "--note"],
             "description": "实体综合层：聚合/list、watch 清单、可选 LLM 摘要"},
            {"name": "run", "args": ["--id", "--max-depth", "--force-collect"],
             "description": "执行已 add+pop 的任务：record 记录提取 → spawn"},
            {"name": "add", "args": ["--input", "--input-type", "--source-type", "--id", "--no-recall"],
             "description": "添加 pending 任务（add 后自动召回相似历史条目）"},
            {"name": "pop", "args": ["--limit"], "description": "取出 pending 任务"},
            {"name": "publish", "args": ["--id", "--site-only"], "description": "记录发布：validate record.json + links/relations 入库（--site-only 仅重建站点）"},
            {"name": "recall", "args": ["--input", "--limit"], "description": "四层确定性相似召回"},
            {"name": "verify-links", "args": ["--id", "--limit"], "description": "验证条目链接可达性（curl HEAD）"},
            {"name": "analyze", "args": ["--topic", "--dedup", "--discover", "--days", "--limit"], "description": "主题聚簇 / 去重候选 / 热点发现"},
            {"name": "add-link", "args": ["--id", "--url", "--role"],
             "description": "手动添加链接到 record 图谱（origin=manual）"},
            {"name": "watch", "args": ["--id", "--on", "--off"], "description": "特别关注：toggle / 设置 / 无 --id 列出全部"},
            {"name": "clean-entities", "args": ["--apply", "--id"],
             "description": "批量清洗存量 record.json 实体（alias 归一 + suppress，默认 dry-run）"},
            {"name": "star", "args": ["--id"], "description": "publish 后标星 canonical GitHub 仓库（需 GITHUB_TOKEN）"},
            {"name": "list", "args": ["--limit", "--status", "--all"], "description": "列出 entries"},
            {"name": "search", "args": ["query", "--limit"], "description": "FTS5 搜索"},
            {"name": "classify", "args": ["--input"], "description": "输入源分类"},
            {"name": "collect", "args": ["--slug", "--input-type", "--source-type", "--input", "--max-depth"],
             "description": "采集原始素材"},
            {"name": "stats", "args": [], "description": "wiki.db 统计"},
            {"name": "sync", "args": ["--rebuild"], "description": "一致性检查/重建"},
            {"name": "index", "args": ["--output"], "description": "刷新 wiki/wiki.html 索引"},
            {"name": "site", "args": ["--serve", "--export", "--port", "--open", "--stop", "--pid-file"],
             "description": "构建并启动 wiki 站点"},
            {"name": "requeue", "args": ["--id", "--clear-md"], "description": "重新入队"},
            {"name": "delete", "args": ["--id"], "description": "删除 entry"},
            {"name": "update", "args": ["--id", "--topic-type", "--status", "--error", "..."], "description": "更新元数据"},
            {"name": "status", "args": ["--id", "--set", "--error"], "description": "查看/设置状态"},
            {"name": "events", "args": ["--id", "--action", "--limit"], "description": "审计事件"},
            {"name": "record-event", "args": ["--id", "--action", "--detail"], "description": "手动记录一条审计事件"},
            {"name": "dedup", "args": ["--input"], "description": "重复检查"},
            {"name": "doctor", "args": ["--quick", "--fix-plan"], "description": "健康检查"},
            {"name": "manifest", "args": [], "description": "输出本清单"},
            {"name": "article", "args": ["--id"], "description": "（已废除 v3.1）→ 独立 article-writer skill"},
            {"name": "interpret", "args": ["--slug"], "description": "（已废除 v3.1）→ 独立 article-writer skill"},
        ],
    }
    if args.json:
        print(json.dumps({"ok": True, "data": manifest}, ensure_ascii=False, indent=2))
    else:
        print(f"Wiki CLI v{manifest['version']}")
        for c in manifest["commands"]:
            print(f"  {c['name']:<18} {', '.join(c['args']):<45} # {c['description']}")
    return 0


# ============================================================
# argparser
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Wiki 工作流统一 CLI",
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--workspace", help="wiki 工作区根目录")
    sub = parser.add_subparsers(dest="command", help="子命令")

    p_run = sub.add_parser("run", help="执行已 pop 的任务——record 记录提取（v3.1 record-only）")
    p_run.add_argument("--id", required=True)
    p_run.add_argument("--max-depth", type=int)
    p_run.add_argument("--force-collect", action="store_true")
    # 以下仅用于返回废弃错误
    p_run.add_argument("--mode", choices=["record", "article"], default=None)
    p_run.add_argument("--depth", choices=["brief", "deep"], default=None)
    p_run.add_argument("--append-to")

    p_art = sub.add_parser("article", help="（已废除 v3.1）→ 独立 article-writer skill")
    p_art.add_argument("--id", required=True)

    p_cls = sub.add_parser("classify", help="来源分类")
    p_cls.add_argument("--input", "-i", required=True)

    p_col = sub.add_parser("collect", help="采集素材")
    p_col.add_argument("--slug", required=True)
    p_col.add_argument("--input-type", "--type", dest="input_type", required=True)
    p_col.add_argument("--source-type", "--subtype", dest="source_type", required=True)
    p_col.add_argument("--input", required=True)
    p_col.add_argument("--max-depth", type=int)

    p_int = sub.add_parser("interpret", help="（已废除 v3.1）→ 独立 article-writer skill")
    p_int.add_argument("--slug", required=True)

    p_vo = sub.add_parser("verify-output", help="（已废除 v3.1）→ cli.py publish --id <slug>")
    p_vo.add_argument("--file")

    p_pub = sub.add_parser("publish", help="验证并发布记录（或 --site-only 只重建站点）")
    p_pub.add_argument("--id", required=True)
    p_pub.add_argument("--site-only", action="store_true",
                       help="只重建站点（entries/timeline/trends JSON），跳过 record 校验/入库")
    p_pub.add_argument("--depth", choices=["brief", "deep"], default=None, help="（保留用于历史文章发布）")

    p_index = sub.add_parser("index", help="刷新 wiki/wiki.html 索引")
    p_index.add_argument("--output")

    p_site = sub.add_parser("site", help="构建并启动 wiki 站点")
    p_site.add_argument("--serve", action="store_true")
    p_site.add_argument("--export", action="store_true")
    p_site.add_argument("--port", type=int, default=8123)
    p_site.add_argument("--open", action="store_true")
    p_site.add_argument("--pid-file")
    p_site.add_argument("--stop", action="store_true")

    p_add = sub.add_parser("add", help="添加 pending 任务")
    p_add.add_argument("--input", "-i", action="append", required=True)
    p_add.add_argument("--inputs-file")
    p_add.add_argument("--source-prompt")
    p_add.add_argument("--append-to")
    p_add.add_argument("--input-type", "--type", dest="input_type", default="unknown")
    p_add.add_argument("--source-type", "--subtype", dest="source_type", default="unknown")
    p_add.add_argument("--depth", default="brief")
    p_add.add_argument("--id")
    p_add.add_argument("--no-recall", action="store_true")

    p_pop = sub.add_parser("pop", help="取出 pending 任务")
    p_pop.add_argument("--limit", "-n", type=int, default=3)

    p_list = sub.add_parser("list", help="列出 entries")
    p_list.add_argument("--limit", "-n", type=int)
    p_list.add_argument("--topic-type", "--type", dest="topic_type")
    p_list.add_argument("--input-type", dest="input_type")
    p_list.add_argument("--source-type", dest="source_type")
    p_list.add_argument("--status")
    p_list.add_argument("--all", action="store_true")

    p_search = sub.add_parser("search", help="FTS5 搜索")
    p_search.add_argument("query")
    p_search.add_argument("--limit", "-n", type=int, default=10)

    sub.add_parser("stats", help="wiki.db 统计")
    p_sync = sub.add_parser("sync", help="一致性检查/重建")
    p_sync.add_argument("--rebuild", action="store_true")

    p_req = sub.add_parser("requeue", help="重新入队")
    p_req.add_argument("--id", required=True)
    p_req.add_argument("--clear-md", action="store_true")

    p_del = sub.add_parser("delete", help="删除 entry")
    p_del.add_argument("--id", required=True)

    p_upd = sub.add_parser("update", help="更新 entry 元数据")
    p_upd.add_argument("--id", required=True)
    p_upd.add_argument("--title"); p_upd.add_argument("--overview")
    p_upd.add_argument("--topic-type", "--type", dest="topic_type")
    p_upd.add_argument("--date"); p_upd.add_argument("--ver")
    p_upd.add_argument("--sources"); p_upd.add_argument("--raw")
    p_upd.add_argument("--file"); p_upd.add_argument("--depth")
    p_upd.add_argument("--tags")
    p_upd.add_argument("--source_input"); p_upd.add_argument("--source_prompt")
    p_upd.add_argument("--input-type", "--source_type", dest="input_type")
    p_upd.add_argument("--source-type", "--source_subtype", dest="source_type")
    p_upd.add_argument("--status"); p_upd.add_argument("--error")
    p_upd.add_argument("--materials_ready", type=int)

    p_status = sub.add_parser("status", help="查看/设置状态")
    p_status.add_argument("--id", required=True)
    p_status.add_argument("--set"); p_status.add_argument("--error")

    p_events = sub.add_parser("events", help="审计事件")
    p_events.add_argument("--id", required=True)
    p_events.add_argument("--action")
    p_events.add_argument("--limit", "-n", type=int, default=50)

    p_rec = sub.add_parser("record-event", help="记录审计事件")
    p_rec.add_argument("--id", required=True)
    p_rec.add_argument("--action", required=True)
    p_rec.add_argument("--detail")

    p_dedup = sub.add_parser("dedup", help="重复检查")
    p_dedup.add_argument("--input", "-i", required=True)

    p_recall = sub.add_parser("recall", help="相似历史条目召回")
    p_recall.add_argument("--input", "-i", required=True)
    p_recall.add_argument("--limit", "-n", type=int, default=5)

    p_vlinks = sub.add_parser("verify-links", help="验证链接可达性")
    p_vlinks.add_argument("--id", required=True)
    p_vlinks.add_argument("--limit", "-n", type=int, default=20)

    p_star = sub.add_parser("star", help="标星 canonical GitHub 仓库（需 GITHUB_TOKEN）")
    p_star.add_argument("--id", required=True)

    # analyze
    p_analyze = sub.add_parser("analyze", help="主题聚簇 / 去重候选")
    p_analyze.add_argument("--topic", help="分析主题（关键词或实体名）")
    p_analyze.add_argument("--dedup", action="store_true", help="输出去重候选对")
    p_analyze.add_argument("--discover", action="store_true", help="自动发现近期热点主题")
    p_analyze.add_argument("--days", type=int, default=14, help="discover 的近 N 天窗口")
    p_analyze.add_argument("--limit", "-n", type=int, default=30)
    p_analyze.add_argument("--min-score", type=float, default=40)

    p_addlink = sub.add_parser("add-link", help="手动添加链接到 record 图谱")
    p_addlink.add_argument("--id", required=True)
    p_addlink.add_argument("--url", required=True)
    p_addlink.add_argument("--role", choices=["canonical", "related"], default="related")

    p_ent = sub.add_parser("entities", help="实体综合层：list / --name 聚合 / --watch 管理 / --summary 摘要")
    p_ent.add_argument("--list", action="store_true")
    p_ent.add_argument("--name")
    p_ent.add_argument("--watch")
    p_ent.add_argument("--unwatch")
    p_ent.add_argument("--watched", action="store_true")
    p_ent.add_argument("--summary", action="store_true")
    p_ent.add_argument("--type", dest="entity_type", default="")
    p_ent.add_argument("--note", default="")

    p_watch = sub.add_parser("watch", help="特别关注：toggle / --on / --off / 无 --id 列出全部")
    p_watch.add_argument("--id")
    p_watch.add_argument("--on", action="store_true")
    p_watch.add_argument("--off", action="store_true")

    p_clean = sub.add_parser("clean-entities",
                             help="批量清洗存量 record.json 实体（alias 归一 + suppress，默认 dry-run）")
    p_clean.add_argument("--apply", action="store_true",
                         help="实际写回 record.json + 更新 db + 重织 relations + 重建站点（持 PublishLock）")
    p_clean.add_argument("--id", help="只清洗指定 entry（默认全部 done entries）")

    p_doc = sub.add_parser("doctor", help="健康检查")
    p_doc.add_argument("--quick", action="store_true")
    p_doc.add_argument("--since")
    p_doc.add_argument("--fix-plan", action="store_true")

    sub.add_parser("init", help="初始化 wiki 工作区骨架（目录/wiki.db/模板，幂等）+ 输出 AGENTS.md 接入片段")

    sub.add_parser("manifest", help="输出命令清单")

    args = parser.parse_args()
    if args.workspace: os.environ["WIKI_WORKSPACE"] = args.workspace
    elif "WIKI_WORKSPACE" not in os.environ:
        os.environ["WIKI_WORKSPACE"] = str(Path.cwd() / "wiki")

    handlers = {
        "init": cmd_init,
        "entities": cmd_entities,
        "run": cmd_run, "article": cmd_article, "classify": cmd_classify,
        "collect": cmd_collect, "interpret": cmd_interpret,
        "verify-output": cmd_verify_output,
        "publish": cmd_publish, "index": cmd_index, "site": cmd_site,
        "add": cmd_add, "pop": cmd_pop, "list": cmd_list, "search": cmd_search,
        "stats": cmd_stats, "sync": cmd_sync, "requeue": cmd_requeue,
        "delete": cmd_delete, "update": cmd_update, "status": cmd_status,
        "events": cmd_events, "record-event": cmd_record_event,
        "dedup": cmd_dedup, "recall": cmd_recall, "verify-links": cmd_verify_links,
        "star": cmd_star,
        "analyze": cmd_analyze,
        "add-link": cmd_add_link, "watch": cmd_watch,
        "clean-entities": cmd_clean_entities,
        "doctor": cmd_doctor, "manifest": cmd_manifest,
    }
    if args.command in handlers: return handlers[args.command](args)
    else: parser.print_help(); return 0


if __name__ == "__main__":
    sys.exit(main())
