#!/usr/bin/env python3
"""
wiki_index/store.py — wiki.db CRUD, queue, events, FTS, and stats.
"""
import re
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.wiki_index.schema import ensure_schema, normalize_topic_type
from scripts import paths

def _row_to_entry(row):
    """把 sqlite3.Row 转成普通 dict。"""
    return {k: row[k] for k in row.keys()}


def _tags_to_list(tags):
    """把 tags 字符串/列表统一为列表。"""
    if isinstance(tags, list):
        return [t.strip() for t in tags if t and t.strip()]
    if not tags or tags == '—':
        return []
    return [t.strip() for t in str(tags).split(',') if t.strip() and t.strip() != '—']


def _tags_to_str(tags):
    """把 tags 列表统一为逗号字符串。"""
    return ','.join(_tags_to_list(tags))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Migration helpers
# ============================================================

def migrate_queue_db(wiki_db_path, queue_db_path):
    """一次性迁移：将 queue.db 的 tasks 合并到 wiki.db.entries。返回迁移数量。"""
    wiki_db_path = Path(wiki_db_path)
    queue_db_path = Path(queue_db_path)
    ensure_schema(wiki_db_path)

    if not queue_db_path.exists():
        return 0

    qconn = sqlite3.connect(str(queue_db_path))
    qconn.row_factory = sqlite3.Row
    tasks = qconn.execute('SELECT * FROM tasks ORDER BY id ASC').fetchall()
    qconn.close()

    count = 0
    for t in tasks:
        task = {k: t[k] for k in t.keys()}
        slug = task.get('slug')
        if not slug:
            continue

        entry = get_entry(wiki_db_path, slug) or {}
        import re as _re
        date_prefix = _re.match(r'(\d{4}-\d{2}-\d{2})', slug)

        # queue.db 旧字段：type_val -> input_type, subtype -> source_type
        old_input_type = task.get('type_val') or entry.get('input_type') or 'unknown'
        old_source_type = task.get('subtype') or entry.get('source_type') or 'unknown'
        old_topic_type = entry.get('topic_type') or task.get('type_val') or 'unknown'

        new_entry = {
            'id': slug,
            'date': entry.get('date') or (date_prefix.group(1) if date_prefix else '—'),
            'ver': entry.get('ver') or '—',
            'depth': task.get('depth') or entry.get('depth') or '—',
            'sources': entry.get('sources') or '—',
            'topic_type': old_topic_type,
            'title': entry.get('title') or task.get('title') or task.get('input_val', '')[:80] or slug,
            'overview': entry.get('overview') or '',
            'tags': entry.get('tags') or [],
            'raw': entry.get('raw') or '',
            'file': entry.get('file') or '',
            'source_input': task.get('input_val') or entry.get('source_input'),
            'source_prompt': task.get('input_val') or entry.get('source_prompt') or entry.get('source_input'),
            'input_type': old_input_type,
            'source_type': old_source_type,
            'status': task.get('status') or entry.get('status') or 'pending',
            'error': task.get('error') or entry.get('error'),
            'materials_ready': entry.get('materials_ready') or 0,
            'queued_at': task.get('created_at') or entry.get('queued_at'),
            'started_at': task.get('started_at') or entry.get('started_at'),
            'completed_at': task.get('completed_at') or entry.get('completed_at'),
            'spec_version': entry.get('spec_version') or '1.0',
            'verified_depths': entry.get('verified_depths') or '',
        }
        update_entry(wiki_db_path, new_entry)
        count += 1

    return count


# ============================================================
# DB columns and row conversion
# ============================================================

COLUMNS = [
    'id', 'date', 'ver', 'depth', 'sources', 'topic_type', 'title', 'overview', 'tags',
    'raw', 'file', 'source_input', 'source_prompt', 'input_type', 'source_type', 'status',
    'error', 'materials_ready', 'queued_at', 'started_at', 'completed_at',
    'spec_version', 'verified_depths', 'entities'
]


def _entry_to_row(entry):
    """把 entry dict 转成与表列顺序一致的 tuple。"""
    def _get(col):
        if col == 'tags':
            return _tags_to_str(entry.get('tags'))
        if col == 'materials_ready':
            return 1 if entry.get('materials_ready') else 0
        if col == 'spec_version':
            return entry.get('spec_version') or '1.0'
        if col == 'verified_depths':
            return entry.get('verified_depths') or ''
        if col == 'entities':
            v = entry.get('entities')
            if isinstance(v, dict):
                return json.dumps(v, ensure_ascii=False)
            return v or ''
        return entry.get(col)
    return tuple(_get(c) for c in COLUMNS)


def _insert_entry(conn, entry):
    """向 entries 与 entries_fts 同时写入/更新一条记录。"""
    placeholders = ', '.join('?' * len(COLUMNS))
    cols = ', '.join(COLUMNS)
    sql = f'INSERT OR REPLACE INTO entries ({cols}) VALUES ({placeholders})'
    conn.execute(sql, _entry_to_row(entry))
    rowid = conn.execute('SELECT rowid FROM entries WHERE id = ?', (entry['id'],)).fetchone()[0]
    # 使用 INSERT OR REPLACE 维护外部内容 FTS5 索引，避免某些 SQLite 实现上
    # DELETE + INSERT 触发 'database disk image is malformed'。
    conn.execute('''
        INSERT OR REPLACE INTO entries_fts (rowid, id, title, overview, tags)
        VALUES (?, ?, ?, ?, ?)
    ''', (rowid, entry['id'], entry.get('title', ''), entry.get('overview', ''), _tags_to_str(entry.get('tags', []))))


def _delete_entry_by_id(conn, eid):
    row = conn.execute('SELECT rowid FROM entries WHERE id = ?', (eid,)).fetchone()
    if row:
        # 某些 Windows SQLite 对外部内容 FTS5 直接 DELETE 会报 malformed；
        # 先置空 fts 行再删主表。
        conn.execute('''
            INSERT OR REPLACE INTO entries_fts (rowid, id, title, overview, tags)
            VALUES (?, ?, '', '', '')
        ''', (row[0], eid))
    conn.execute('DELETE FROM entries WHERE id = ?', (eid,))


def _delete_entry_by_file(conn, filename):
    row = conn.execute('SELECT rowid FROM entries WHERE file = ?', (filename,)).fetchone()
    if row:
        eid = conn.execute('SELECT id FROM entries WHERE file = ?', (filename,)).fetchone()[0]
        conn.execute('''
            INSERT OR REPLACE INTO entries_fts (rowid, id, title, overview, tags)
            VALUES (?, ?, '', '', '')
        ''', (row[0], eid))
    conn.execute('DELETE FROM entries WHERE file = ?', (filename,))


# ============================================================
# Task / queue operations
# ============================================================

def get_entry(db_path, slug):
    """按 slug 读取 entry，不存在返回 None。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM entries WHERE id = ?', (slug,)).fetchone()
    conn.close()
    return _row_to_entry(row) if row else None


def get_entry_by_file(db_path, filename):
    """按 file 名读取 entry。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM entries WHERE file = ?', (filename,)).fetchone()
    conn.close()
    return _row_to_entry(row) if row else None


def upsert_task(db_path, slug, source_input=None, source_prompt=None, input_type=None,
                source_type=None, topic_type=None, depth=None, status=None, title=None,
                error=None, **kwargs):
    """创建或更新一个任务/条目。用于 add/requeue/orchestrate 初始写入。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    existing = conn.execute('SELECT * FROM entries WHERE id = ?', (slug,)).fetchone()

    if existing:
        entry = _row_to_entry(existing)
    else:
        date_prefix = re.match(r'(\d{4}-\d{2}-\d{2})', slug)
        entry = {
            'id': slug,
            'date': date_prefix.group(1) if date_prefix else '—',
            'ver': '—',
            'depth': depth,
            'sources': '—',
            'topic_type': 'unknown',
            'title': title or source_input[:80] if source_input else slug,
            'overview': '',
            'tags': [],
            'raw': '',
            'file': '',
            'spec_version': '1.0',
            'verified_depths': '',
        }

    if source_input is not None:
        entry['source_input'] = source_input
    if source_prompt is not None:
        entry['source_prompt'] = source_prompt
    elif 'source_prompt' not in entry or not entry.get('source_prompt'):
        # 向后兼容：未提供 source_prompt 时，默认复用 source_input
        entry['source_prompt'] = entry.get('source_input')
    if input_type is not None:
        entry['input_type'] = input_type
    if source_type is not None:
        entry['source_type'] = source_type
    if topic_type is not None:
        entry['topic_type'] = topic_type
    # 未指定主题类型时，按来源平台做最佳-effort 推断
    if not entry.get('topic_type') or entry.get('topic_type') == 'unknown':
        entry['topic_type'] = normalize_topic_type(entry.get('topic_type'), entry.get('source_type'))
    if depth is not None:
        entry['depth'] = depth
    if status is not None:
        entry['status'] = status
    if title is not None:
        entry['title'] = title
    if error is not None:
        entry['error'] = error

    # 状态变更时更新时间戳
    now = _now_iso()
    if status == 'pending':
        entry['queued_at'] = entry.get('queued_at') or now
    if status == 'running':
        entry['started_at'] = entry.get('started_at') or now
    if status in ('done', 'failed'):
        entry['completed_at'] = now

    for k, v in kwargs.items():
        if k in COLUMNS:
            entry[k] = v

    _insert_entry(conn, entry)
    conn.commit()
    conn.close()
    return entry


def update_status(db_path, slug, status, error=None, title=None, **kwargs):
    """更新任务状态及相关字段。"""
    from scripts.wiki_index.schema import VALID_STATUSES
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Valid: {', '.join(sorted(VALID_STATUSES))}")
    return upsert_task(db_path, slug, status=status, error=error, title=title, **kwargs)


def pop_pending(db_path, limit=3):
    """取出最多 N 个 pending 任务，设为 running，返回 entry 列表。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT * FROM entries
        WHERE status = 'pending'
        ORDER BY COALESCE(queued_at, '9999') ASC, id ASC
        LIMIT ?
    ''', (limit,)).fetchall()

    now = _now_iso()
    result = []
    for r in rows:
        entry = _row_to_entry(r)
        conn.execute('''
            UPDATE entries SET status = 'running', started_at = ? WHERE id = ?
        ''', (now, entry['id']))
        entry['status'] = 'running'
        entry['started_at'] = now
        result.append(entry)
    conn.commit()
    conn.close()
    return result


def requeue(db_path, slug, clear_md=False, wiki_dir=None):
    """将条目重新置为 pending，可同步删除 md 文件。返回更新后的 entry。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    entry = get_entry(db_path, slug)
    if not entry:
        raise ValueError(f"Entry not found: {slug}")

    if clear_md and wiki_dir:
        for depth in ['brief', 'deep']:
            md = paths.article_path(slug, depth, wiki_dir)
            if md.exists():
                md.unlink()

    return update_status(db_path, slug, 'pending', error=None,
                         materials_ready=0, completed_at=None)


# ============================================================
# CRUD / query
# ============================================================

def update_entry(db_path, entry):
    """更新或插入单条 entry（通过 id 主键）。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_entry(conn, entry)
    conn.commit()
    conn.close()


def delete_entry(db_path, id_or_file):
    """按 id 或 file 删除 entry。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    _delete_entry_by_id(conn, id_or_file)
    _delete_entry_by_file(conn, id_or_file)
    conn.commit()
    conn.close()


def _escape_fts_query(query: str) -> str:
    """安全转义 FTS5 查询。"""
    tokens = []
    for token in query.split():
        if not token:
            continue
        if re.match(r'^[\w一-鿿]+$', token):
            tokens.append(token)
        else:
            escaped = token.replace('"', '""')
            tokens.append(f'"{escaped}"')
    return ' '.join(tokens)


def search(db_path, query, limit=10):
    """FTS5 全文搜索，返回 entry dict 列表。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    safe_query = _escape_fts_query(query)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT e.* FROM entries e
        JOIN entries_fts fts ON e.rowid = fts.rowid
        WHERE entries_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    ''', (safe_query, limit)).fetchall()
    conn.close()
    return [_row_to_entry(r) for r in rows]


def list_entries(db_path, limit=None, order_by='date DESC', status=None):
    """列出 entries，支持按状态过滤。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    where = ''
    params = ()
    if status:
        where = 'WHERE status = ?'
        params = (status,)
    sql = f'SELECT * FROM entries {where} ORDER BY {order_by}'
    if limit:
        sql += ' LIMIT ?'
        params = params + (limit,)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_entry(r) for r in rows]


def get_stats(db_path):
    """返回 wiki.db 统计信息。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    total = conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
    fts_total = conn.execute('SELECT COUNT(*) FROM entries_fts').fetchone()[0]
    type_counts = {}
    for row in conn.execute('SELECT topic_type, COUNT(*) FROM entries GROUP BY topic_type'):
        type_counts[row[0]] = row[1]
    status_counts = {}
    for row in conn.execute('SELECT status, COUNT(*) FROM entries GROUP BY status'):
        status_counts[row[0]] = row[1]
    conn.close()
    return {'total': total, 'fts_total': fts_total, 'type_counts': type_counts, 'status_counts': status_counts}


# ============================================================
# Events / audit log
# ============================================================

def record_event(db_path, slug, action, detail=None):
    """向 wiki.db events 表写入一条审计事件。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None
    conn.execute('''
        INSERT INTO events (slug, timestamp, action, detail)
        VALUES (?, ?, ?, ?)
    ''', (slug, _now_iso(), action, detail_json))
    conn.commit()
    conn.close()


def get_events(db_path, slug=None, action=None, limit=None):
    """读取审计事件。"""
    db_path = Path(db_path)
    ensure_schema(db_path)
    where = []
    params = []
    if slug:
        where.append('slug = ?')
        params.append(slug)
    if action:
        where.append('action = ?')
        params.append(action)
    sql = 'SELECT * FROM events'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY timestamp DESC'
    if limit:
        sql += ' LIMIT ?'
        params.append(limit)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_entry(r) for r in rows]


def check_events_complete(db_path, slug):
    """检查 slug 的审计事件是否包含所有必需阶段。"""
    events = get_events(db_path, slug=slug)
    actions = {e['action'] for e in events}
    required = {'ENQUEUE', 'FETCH', 'GATE', 'WRITE', 'VERIFY', 'DONE'}
    missing = sorted(required - actions)
    fetches = [e for e in events if e['action'] == 'FETCH']
    return {
        'complete': len(missing) == 0 and len(fetches) > 0,
        'missing': missing,
        'fetch_count': len(fetches),
        'total_events': len(events),
    }


# v3.1: moved from deleted meta.py

def sync_with_files(db_path, wiki_dir):
    """校验 db 与文件一致性。简化版：只做基本统计。"""
    from scripts.wiki_index.schema import ensure_schema
    import sqlite3
    db_path = Path(db_path); wiki_dir = Path(wiki_dir)
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    db_entries = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM entries")}
    conn.close()
    md_ids = set()
    artifacts = wiki_dir / "artifacts"
    if artifacts.exists():
        for d in artifacts.iterdir():
            if d.is_dir() and (d / "record.json").exists():
                md_ids.add(d.name)
            elif d.is_dir() and list(d.glob("*.md")):
                md_ids.add(d.name)
    both = len(set(db_entries) & md_ids)
    db_only = [eid for eid in db_entries if eid not in md_ids]
    record_only = [eid for eid in md_ids if eid not in db_entries and (artifacts/eid/"record.json").exists()]
    md_only = [eid for eid in md_ids if eid not in db_entries and eid not in record_only]
    return {"db_total": len(db_entries), "md_total": len(md_ids),
            "db_only": db_only, "record_only": record_only, "md_only": md_only,
            "both": both, "mismatch": []}

def rebuild_index(db_path, wiki_dir, preserve_meta=True):
    """重建索引：返回 (entry_count, fts_count)。"""
    from scripts.wiki_index.schema import ensure_schema
    db_path = Path(db_path)
    ensure_schema(db_path)
    entries = list_entries(db_path)
    return len(entries), len(entries)

