#!/usr/bin/env python3
"""
store/commands.py — wiki.db 纯 CRUD / 队列 / 搜索 / 统计 / 迁移命令。

从 wiki_db.py 拆分而来，保持 CLI 行为不变。
"""
import sys
import argparse
import json
import sqlite3
from pathlib import Path


from scripts import paths
from scripts import wiki_index
from scripts.wiki_index.schema import VALID_STATUSES


def _out_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _fmt_entry(e):
    tags = ', '.join(e.get('tags', [])[:5]) if e.get('tags') else ''
    title = e.get('title', '')[:55]
    source = (e.get('source_input') or '')[:40]
    return (f"  {e.get('id','?'):<32} [{e.get('depth','-'):<5}] {e.get('status','-'):<10} "
            f"{e.get('date','-'):<12} {e.get('topic_type','-'):<12} {title:<55} {source} {tags}")


def _fmt_row(e: dict) -> str:
    tid = e.get("id", "?")
    status = e.get("status", "?")
    depth = e.get("depth") or ""
    title = (e.get("title") or e.get("source_input") or "")[:45]
    input_preview = (e.get("source_input") or "")[:50]
    line = f"  {tid:<32} [{depth:<5}] {status:<10}"
    if title:
        line += f" {title}"
    else:
        line += f" {input_preview}"
    return line


def _fmt_row_dict(e: dict) -> dict:
    return {k: e.get(k) for k in ['id', 'source_input', 'depth', 'status', 'title', 'error', 'queued_at']}


def cmd_list(args, db_path):
    limit = 50 if getattr(args, 'all', False) else args.limit
    order_by = 'queued_at DESC, id DESC' if getattr(args, 'queue', False) else 'date DESC'
    entries = wiki_index.list_entries(db_path, limit=limit, order_by=order_by,
                                      status=args.status)
    if args.json:
        from scripts.records.links import get_links_count_map
        counts = get_links_count_map(db_path)
        # 工作区优先从 db_path 推导（ws/data/wiki.db），保证测试与自定义路径正确
        db_path = Path(db_path)
        ws = db_path.parent.parent if db_path.parent.name == 'data' else paths.get_workspace()
        rows = []
        for e in entries:
            d = _fmt_row_dict(e)
            d['has_record'] = paths.record_path(e.get('id', ''), ws).exists()
            d['links_count'] = counts.get(e.get('id', ''), 0)
            rows.append(d)
        _out_json(rows)
        return
    if getattr(args, 'human', False):
        if not entries:
            print("No tasks found.")
            return
        for e in entries:
            print(_fmt_row(e))
        return
    print(f"Total: {len(entries)} (limit={limit or 'none'})")
    for e in entries:
        if args.topic_type and e.get('topic_type') != args.topic_type:
            continue
        if args.input_type and e.get('input_type') != args.input_type:
            continue
        if args.source_type and e.get('source_type') != args.source_type:
            continue
        print(_fmt_entry(e))


def cmd_search(args, db_path):
    rows = wiki_index.search(db_path, args.query, limit=args.limit)
    if args.json:
        _out_json([_fmt_row_dict(r) for r in rows])
        return
    print(f'Query: "{args.query}" → {len(rows)} result(s)')
    for e in rows:
        print(_fmt_entry(e))


def cmd_pop(args, db_path):
    """取出待处理任务（manage_queue 风格）。"""
    rows = wiki_index.pop_pending(db_path, limit=args.limit or 3)
    for e in rows:
        wiki_index.record_event(db_path, e['id'], 'STARTED',
                                {'queued_at': e.get('queued_at')})
    if args.json:
        _out_json(rows)
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_dedup(args, db_path):
    """检查重复输入（manage_queue 风格）。"""
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, source_input, depth, status, title, error, queued_at "
            "FROM entries WHERE source_input = ? ORDER BY queued_at DESC LIMIT 3",
            (args.input,)
        ).fetchall()
        result = {"duplicate": bool(rows)}
        if rows:
            result["existing"] = [_fmt_row_dict({k: r[k] for k in r.keys()}) for r in rows]
        if args.json:
            _out_json(result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if conn:
            conn.close()


def cmd_update(args, db_path):
    conn = None
    try:
        wiki_index.ensure_schema(db_path)
        entry_id = getattr(args, 'id', None) or getattr(args, 'slug', None)
        if not entry_id:
            msg = 'Need --id or --slug'
            if args.json:
                _out_json({"ok": False, "error": "MISSING_ID", "message": msg})
            else:
                print(msg)
            sys.exit(1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
        if not row:
            msg = f'Entry not found: {entry_id}'
            if args.json:
                _out_json({"ok": False, "error": "NOT_FOUND", "message": msg})
            else:
                print(msg)
            sys.exit(1)
        entry = {k: row[k] for k in row.keys()}
        if isinstance(entry.get('tags'), str):
            entry['tags'] = [t.strip() for t in entry['tags'].split(',') if t.strip()]

        fields = ['title', 'overview', 'topic_type', 'date', 'ver', 'sources', 'raw', 'file',
                  'depth', 'source_input', 'source_prompt', 'input_type', 'source_type', 'status', 'error']
        updated = []
        for f in fields:
            v = getattr(args, f, None)
            if v is not None:
                entry[f] = v
                updated.append(f)
        if args.tags:
            entry['tags'] = [t.strip() for t in args.tags.split(',') if t.strip()]
            updated.append('tags')
        if args.materials_ready is not None:
            entry['materials_ready'] = bool(args.materials_ready)
            updated.append('materials_ready')
        if getattr(args, 'wiki_path', None) is not None:
            entry['file'] = Path(args.wiki_path).name
            updated.append('file')

        if not updated:
            msg = 'No fields to update.'
            if args.json:
                _out_json({"ok": False, "error": "NO_FIELDS", "message": msg})
            else:
                print(msg)
            sys.exit(0)

        wiki_index.update_entry(db_path, entry)
        if args.json:
            _out_json({"ok": True, "id": entry_id, "updated": updated})
        else:
            print(f'Updated {entry_id}: {", ".join(updated)}')
    finally:
        if conn:
            conn.close()


def cmd_delete(args, db_path):
    wiki_index.delete_entry(db_path, args.id)
    if args.json:
        _out_json({"ok": True, "id": args.id, "message": "deleted from wiki.db"})
    else:
        print(f'Deleted {args.id} from wiki.db (source markdown unchanged)')


def cmd_status(args, db_path):
    entry = wiki_index.get_entry(db_path, args.id)
    if not entry:
        msg = f'Entry not found: {args.id}'
        if args.json:
            _out_json({"ok": False, "error": "NOT_FOUND", "message": msg})
        else:
            print(msg)
        sys.exit(1)
    if args.set:
        entry = wiki_index.update_status(db_path, args.id, args.set, error=args.error)
        if args.json:
            _out_json({"ok": True, "id": args.id, "status": entry["status"]})
        else:
            print(f'Status updated: {args.id} → {entry["status"]}')
    else:
        data = {k: entry.get(k) for k in ['id', 'status', 'source_input', 'source_prompt',
                                            'input_type', 'source_type', 'topic_type', 'depth', 'queued_at',
                                            'started_at', 'completed_at', 'error']}
        # 兼容性别名
        data['type'] = data.get('topic_type')
        data['subtype'] = data.get('source_type')
        if args.json:
            _out_json(data)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))

def cmd_events(args, db_path):
    events = wiki_index.get_events(db_path, slug=args.id, action=args.action, limit=args.limit)
    if args.json:
        _out_json(events)
        return
    if not events:
        print(f'No events found for {args.id}')
        return
    for e in events:
        detail = e.get('detail', '')
        if detail:
            try:
                detail_obj = json.loads(detail)
                detail_str = json.dumps(detail_obj, ensure_ascii=False)[:120]
            except Exception:
                detail_str = str(detail)[:120]
        else:
            detail_str = ''
        print(f'{e["timestamp"][:19]}  {e["action"]:<8}  {detail_str}')


def cmd_stats(args, db_path):
    stats = wiki_index.get_stats(db_path)
    if args.json:
        _out_json(stats)
        return
    if getattr(args, 'human', False):
        total = stats['total']
        status_counts = stats.get('status_counts', {})
        print(f"Queue/Index: {total} total | " + " | ".join(
            f"{s}={status_counts.get(s, 0)}" for s in sorted(VALID_STATUSES)
        ))
        return
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_migrate_queue(args, db_path, wiki_dir):
    queue_path = wiki_dir / 'queue.db'
    if not queue_path.exists():
        msg = 'queue.db not found, nothing to migrate'
        if args.json:
            _out_json({"ok": True, "migrated": 0, "message": msg})
        else:
            print(msg)
        return
    count = wiki_index.migrate_queue_db(db_path, queue_path)
    deleted = False
    if args.delete:
        queue_path.unlink()
        deleted = True
    if args.json:
        _out_json({"ok": True, "migrated": count, "deleted": deleted})
    else:
        print(f'Migrated {count} task(s) from queue.db to wiki.db')
        if deleted:
            print('Deleted queue.db')
