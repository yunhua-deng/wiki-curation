#!/usr/bin/env python3
"""
publish/commands.py — wiki 发布命令（v3.1 record-only）。

分发规则：
  - publish --id X（无 --depth）→ record.json 记录发布
  - publish --id X --depth brief|deep → 历史文章标记 done（保留可读，不做 verify_output）
"""
import json
import sys
import time
from pathlib import Path

from scripts import wiki_index
from scripts.publish.lock import PublishLock, LockBusyError
def _refresh_html_index(db_path, wiki_dir, out_path=None):
    """v3.1: wiki.html 生成已停用；仅刷新站点。"""
    _refresh_site(db_path, wiki_dir)
from scripts.site.build import build_site

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"
SITE_BUILD_WARN = "站点构建失败，但发布成功"


def _out_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _refresh_site(db_path, wiki_dir, json_mode=False):
    try:
        build_site(db_path, wiki_dir)
        return True
    except Exception as e:
        if not json_mode:
            print(f"  ⚠️ {SITE_BUILD_WARN}: {e}")
        return False


def _publish_article_legacy(args, db_path, wiki_dir, scripts_dir):
    """v3.1：历史文章路径——标记 done，刷新索引（不做 verify_output）。"""
    entry_id = args.id
    depth = args.depth or 'brief'
    from scripts import paths as article_paths
    wiki_path = article_paths.article_path(entry_id, depth, article_paths.get_workspace())
    for delay in (0.5, 1.0, 1.5):
        if wiki_path.exists(): break
        time.sleep(delay)
    if not wiki_path.exists():
        msg = f'File not found: {wiki_path}'
        wiki_index.update_status(db_path, entry_id, 'failed', error=f'output file missing: {wiki_path.name}')
        if args.json:
            _out_json({"ok": False, "error": "FILE_MISSING", "message": msg})
        else:
            print(f'❌ {msg}')
        sys.exit(1)

    wiki_index.record_event(db_path, entry_id, 'DONE', {'file': wiki_path.name, 'depth': depth})
    wiki_index.upsert_task(db_path, entry_id, status='done')

    try: _refresh_html_index(db_path, wiki_dir)
    except Exception as e:
        if not args.json: print(f"  ⚠️ Index refresh skipped: {e}")

    _refresh_site(db_path, wiki_dir, json_mode=args.json)
    if args.json:
        _out_json({"ok": True, "id": entry_id, "depth": depth, "status": "done",
                   "file": article_paths.article_rel(entry_id, depth)})
    else:
        print(f'✅ Published (legacy article): {wiki_path.name}')


def cmd_publish(args, db_path, wiki_dir, scripts_dir):
    """publish 分发：无 --depth → 记录发布；显式 --depth → 历史文章标记 done。"""
    try:
        with PublishLock(timeout=30):
            if getattr(args, 'site_only', False):
                _refresh_site(db_path, wiki_dir, json_mode=args.json)
                if args.json:
                    _out_json({"ok": True, "mode": "site-only"})
                else:
                    print("✅ site rebuilt")
                return
            if getattr(args, 'depth', None) is None:
                from scripts.records.publish_record import publish_record
                publish_record(args, db_path, wiki_dir, scripts_dir)
            else:
                _publish_article_legacy(args, db_path, wiki_dir, scripts_dir)
    except LockBusyError as e:
        msg = str(e)
        if args.json:
            _out_json({"ok": False, "error": "BUSY", "message": msg,
                       "next_cmd": f"{CLI_CMD} publish --id {args.id}"})
        else:
            print(f'⏳ {msg}')
        sys.exit(1)


def cmd_requeue(args, db_path, wiki_dir):
    try:
        entry = wiki_index.requeue(db_path, args.id, clear_md=args.clear_md, wiki_dir=wiki_dir)
        if args.json:
            _out_json({"ok": True, "id": entry["id"], "status": entry["status"]})
        else:
            print(f'Requeued: {entry["id"]} → status={entry["status"]}')
    except ValueError as e:
        if args.json:
            _out_json({"ok": False, "error": "REQUEUE_FAILED", "message": str(e)})
        else:
            print(f'Error: {e}')
        sys.exit(1)


def cmd_sync(args, db_path, wiki_dir):
    if args.rebuild:
        count, fts_count = wiki_index.rebuild_index(db_path, wiki_dir, preserve_meta=True)
        if args.json:
            _out_json({"ok": True, "entries": count, "fts_docs": fts_count, "rebuilt": True})
        else:
            print(f'Rebuilt wiki.db: {count} entries, FTS5 index: {fts_count} docs')
    else:
        report = wiki_index.sync_with_files(db_path, wiki_dir)
        if args.json:
            _out_json({"ok": True, **report})
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report['db_only']:
                print(f'\n⚠️ {len(report["db_only"])} entries in db but no md (orphan)')
            if report['md_only']:
                print(f'\n⚠️ {len(report["md_only"])} md files not in db')
            if report['mismatch']:
                print(f'\n⚠️ {len(report["mismatch"])} id/file mismatch')


def cmd_record_event(args, db_path):
    detail = None
    if args.detail:
        try: detail = json.loads(args.detail)
        except json.JSONDecodeError: detail = args.detail
    wiki_index.record_event(db_path, args.id, args.action, detail)
    if args.json:
        _out_json({"ok": True, "id": args.id, "action": args.action})
    else:
        print(f'Recorded {args.action} for {args.id}')


def cmd_index(args, db_path, wiki_dir):
    out_path = args.output if getattr(args, 'output', None) else None
    try:
        count = 0; _refresh_html_index(db_path, wiki_dir, out_path=out_path)
        _refresh_site(db_path, wiki_dir, json_mode=args.json)
        out = Path(out_path) if out_path else Path(wiki_dir) / 'wiki.html'
        if args.json:
            _out_json({"ok": True, "entries": count, "index": str(out)})
        else:
            print(f'wiki/wiki.html refreshed: {count} entries')
    except Exception as e:
        if args.json:
            _out_json({"ok": False, "error": "INDEX_REFRESH_FAILED", "message": str(e)})
        else:
            print(f'❌ Index refresh failed: {e}')
        sys.exit(1)
