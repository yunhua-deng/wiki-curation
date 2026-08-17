#!/usr/bin/env python3
"""
wiki_index/schema.py — wiki.db schema, migrations, and column helpers.
"""
import re
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    date TEXT,
    ver TEXT,
    depth TEXT,
    sources TEXT,
    topic_type TEXT DEFAULT 'unknown',
    title TEXT,
    overview TEXT,
    tags TEXT,
    raw TEXT,
    file TEXT,
    source_input TEXT,
    source_prompt TEXT,
    input_type TEXT DEFAULT 'unknown',
    source_type TEXT DEFAULT 'unknown',
    status TEXT DEFAULT 'pending',
    error TEXT,
    materials_ready INTEGER DEFAULT 0,
    queued_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    spec_version TEXT DEFAULT '1.0',
    verified_depths TEXT DEFAULT ''
)
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    id, title, overview, tags,
    content='entries',
    content_rowid='rowid'
)
"""

EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    FOREIGN KEY (slug) REFERENCES entries(id) ON DELETE CASCADE
)
"""

LINKS_SQL = """
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT DEFAULT 'other',
    role TEXT DEFAULT 'related',
    origin TEXT DEFAULT 'explicit',
    fetched INTEGER DEFAULT 0,
    verified INTEGER,
    discovered_at TEXT NOT NULL,
    UNIQUE(entry_id, url)
)
"""

RELATIONS_SQL = """
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_a TEXT NOT NULL,
    entry_b TEXT NOT NULL,
    kind TEXT NOT NULL,
    score REAL DEFAULT 0,
    evidence TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(entry_a, entry_b, kind)
)
"""

SCHEMA_VERSION_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

MIGRATIONS = [
    ('v1_add_depth', 'ALTER TABLE entries ADD COLUMN depth TEXT DEFAULT "—"'),
    ('v1_add_source_input', 'ALTER TABLE entries ADD COLUMN source_input TEXT'),
    # source_type / source_subtype 已合并进 v4_rename_fields，此处不再单独添加
    ('v1_add_status', 'ALTER TABLE entries ADD COLUMN status TEXT DEFAULT \'pending\''),
    ('v1_add_error', 'ALTER TABLE entries ADD COLUMN error TEXT'),
    ('v1_add_materials_ready', 'ALTER TABLE entries ADD COLUMN materials_ready INTEGER DEFAULT 0'),
    ('v1_add_queued_at', 'ALTER TABLE entries ADD COLUMN queued_at TEXT'),
    ('v1_add_started_at', 'ALTER TABLE entries ADD COLUMN started_at TEXT'),
    ('v1_add_completed_at', 'ALTER TABLE entries ADD COLUMN completed_at TEXT'),
    ('v2_add_spec_version', 'ALTER TABLE entries ADD COLUMN spec_version TEXT DEFAULT \'1.0\''),
    ('v2_add_verified_depths', 'ALTER TABLE entries ADD COLUMN verified_depths TEXT DEFAULT \'\''),
    ('v3_add_source_prompt', 'ALTER TABLE entries ADD COLUMN source_prompt TEXT'),
    ('v5_add_watched', 'ALTER TABLE entries ADD COLUMN watched INTEGER DEFAULT 0'),
    ('v5_add_watched_at', 'ALTER TABLE entries ADD COLUMN watched_at TEXT'),
    ('v7_entity_watch', "CREATE TABLE IF NOT EXISTS entity_watch (name TEXT PRIMARY KEY, type TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT NOT NULL)"),
]

VALID_STATUSES = {"pending", "running", "done", "failed", "orphan", "verified_brief"}


# ============================================================
# v4 field rename mapping helpers
# ============================================================

INPUT_TYPE_MAP = {
    'url': 'url',
    'non_url': 'keywords',
    'local': 'local',
    'local_file': 'local',
    'local-file': 'local',
}

SOURCE_TYPE_MAP = {
    'arxiv_paper': 'arxiv',
    'arxiv_id': 'arxiv',
    'arxiv_title': 'arxiv',
    'paper_keyword': 'arxiv',
    'github': 'github',
    'weixin': 'weixin',
    'weixin_paper': 'weixin',
    'huggingface': 'huggingface',
    'linkedin': 'linkedin',
    'zhihu': 'zhihu',
    'reddit': 'reddit',
    'twitter_x': 'twitter_x',
    'youtube': 'youtube',
    'bilibili': 'bilibili',
    'podcast': 'podcast',
    'tech_blog': 'blog',
    'news_article': 'news',
    'project_page': 'webpage',
    'generic_web': 'webpage',
    'docs': 'docs',
    'startup_name': 'company',
    'company_site': 'company',
    'product_name': 'product',
    'project_name': 'project',
    'researcher': 'researcher',
    'concept_query': 'concept',
    'comparison': 'comparison',
    'trend_question': 'trend',
    'local_file': 'local',
    'multi_source': 'multi_source',
}

TOPIC_TYPE_INFERENCE = {
    'arxiv': 'paper',
    'github': 'project',
    'project': 'project',
    'huggingface': 'tool',
    'product': 'product',
    'company': 'company',
    'researcher': 'researcher',
    'concept': 'concept',
    'comparison': 'comparison',
    'trend': 'trend',
    'weixin': 'article',
    'zhihu': 'article',
    'reddit': 'article',
    'twitter_x': 'article',
    'youtube': 'article',
    'bilibili': 'article',
    'podcast': 'article',
    'blog': 'article',
    'news': 'article',
    'docs': 'article',
    'webpage': 'article',
    'multi_source': 'observation',
    'local': 'observation',
}

VALID_TOPIC_TYPES = {
    'paper', 'project', 'tool', 'company', 'institution',
    'researcher', 'concept', 'whitepaper', 'best_practice',
    'comparison', 'trend', 'article', 'observation', 'product',
}


def normalize_topic_type(raw_type, source_type=None):
    """把任意 Type 字符串归一化为 VALID_TOPIC_TYPES 中的值。"""
    typ = (raw_type or '').strip().lower()
    if typ in VALID_TOPIC_TYPES:
        return typ

    # 子串匹配（优先于平台名映射，避免 'whitepaper' 被拆成 'paper' 等）
    if 'whitepaper' in typ or 'report' in typ:
        return 'whitepaper'
    if 'best_practice' in typ:
        return 'best_practice'
    if 'comparison' in typ:
        return 'comparison'
    if 'observation' in typ:
        return 'observation'
    if 'researcher' in typ:
        return 'researcher'
    if 'institution' in typ:
        return 'institution'
    if 'paper' in typ or 'arxiv' in typ:
        return 'paper'
    if 'company' in typ:
        return 'company'
    if 'product' in typ:
        return 'product'
    if 'project' in typ or 'github' in typ:
        return 'project'
    if 'tool' in typ or 'huggingface' in typ:
        return 'tool'
    if 'concept' in typ:
        return 'concept'
    if 'trend' in typ or 'survey' in typ:
        return 'trend'
    if 'article' in typ or 'blog' in typ or 'news' in typ or 'speech' in typ or 'weixin' in typ:
        return 'article'

    # 从来源平台兜底推断
    return TOPIC_TYPE_INFERENCE.get((source_type or '').strip().lower(), 'observation')


def _map_input_type(old_source_type, old_source_subtype, source_input):
    """把旧 source_type 归一化为 input_type。"""
    old = (old_source_type or '').strip().lower()
    if old in INPUT_TYPE_MAP:
        return INPUT_TYPE_MAP[old]
    # 旧数据里 source_type 混入了 subtype/topic，进一步推断
    sub = (old_source_subtype or '').strip().lower()
    if sub in ('local_file', 'zip') or sub.startswith('local'):
        return 'local'
    if source_input and source_input.strip().startswith(('http://', 'https://')):
        return 'url'
    return 'keywords'


def _map_source_type(old_source_subtype, old_type):
    """把旧 source_subtype 归一化为平台 source_type。"""
    sub = (old_source_subtype or '').strip().lower()
    if sub in SOURCE_TYPE_MAP:
        return SOURCE_TYPE_MAP[sub]
    # keyword / unknown 等按旧 type 推断
    typ = (old_type or '').strip().lower()
    if typ in ('paper',):
        return 'arxiv'
    if typ in ('project', 'github'):
        return 'github'
    if typ in ('company',):
        return 'company'
    if typ in ('product',):
        return 'product'
    if typ in ('tool',):
        return 'huggingface'
    if typ in ('weixin',):
        return 'weixin'
    if sub == 'keyword':
        return 'arxiv'  # 历史 keyword 多为论文搜索
    return 'unknown'


def _map_topic_type(old_type, new_source_type):
    """把旧 type 归一化为 topic_type。"""
    return normalize_topic_type(old_type, new_source_type)


# ============================================================
# Schema helpers
# ============================================================

def _table_columns(conn, table_name):
    """返回表中已有的列名集合。"""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _extract_added_column(sql):
    """从 ALTER TABLE ADD COLUMN 语句中提取列名。"""
    m = re.search(r'ADD COLUMN\s+(\w+)', sql, re.IGNORECASE)
    return m.group(1) if m else None


def _get_applied_versions(conn):
    """返回 schema_version 表中已记录的版本集合。"""
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _record_schema_version(conn, version):
    """记录迁移已执行。"""
    from scripts.wiki_index.store import _now_iso
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, _now_iso())
    )


def _migrate_v4_rename_fields(conn):
    """v4：重命名字段并清洗历史数据。"""
    applied = _get_applied_versions(conn)
    if 'v4_rename_fields' in applied:
        return

    cols = _table_columns(conn, 'entries')
    renames = [
        ('type', 'topic_type'),
        ('source_type', 'input_type'),
        ('source_subtype', 'source_type'),
    ]
    for old, new in renames:
        cols = _table_columns(conn, 'entries')
        if old in cols and new not in cols:
            conn.execute(f'ALTER TABLE entries RENAME COLUMN {old} TO {new}')

    # 兜底：新库通过 SCHEMA_SQL 已有新列，但旧库若缺则补
    cols = _table_columns(conn, 'entries')
    if 'topic_type' not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN topic_type TEXT DEFAULT 'unknown'")
    if 'input_type' not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN input_type TEXT DEFAULT 'unknown'")
    if 'source_type' not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN source_type TEXT DEFAULT 'unknown'")

    # 数据清洗（需要按列名访问）
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT rowid, id, topic_type, input_type, source_type, source_input FROM entries'
    ).fetchall()
    for r in rows:
        new_input_type = _map_input_type(r['input_type'], r['source_type'], r['source_input'] or '')
        new_source_type = _map_source_type(r['source_type'], r['topic_type'])
        new_topic_type = _map_topic_type(r['topic_type'], new_source_type)
        conn.execute(
            'UPDATE entries SET topic_type=?, input_type=?, source_type=? WHERE rowid=?',
            (new_topic_type, new_input_type, new_source_type, r['rowid'])
        )

    _record_schema_version(conn, 'v4_rename_fields')


def _migrate_v4b_normalize_topic_types(conn):
    """v4b：再次清洗 topic_type，确保所有值都在 VALID_TOPIC_TYPES 内。"""
    applied = _get_applied_versions(conn)
    if 'v4b_normalize_topic_types' in applied:
        return

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT rowid, id, topic_type, source_type FROM entries'
    ).fetchall()
    for r in rows:
        new_topic_type = normalize_topic_type(r['topic_type'], r['source_type'])
        if new_topic_type != r['topic_type']:
            conn.execute(
                'UPDATE entries SET topic_type=? WHERE rowid=?',
                (new_topic_type, r['rowid'])
            )

    _record_schema_version(conn, 'v4b_normalize_topic_types')


def _migrate_v5_records(conn):
    """v5：record-first——entries.entities 列 + links/relations 索引。"""
    applied = _get_applied_versions(conn)
    if 'v5_records' in applied:
        return

    cols = _table_columns(conn, 'entries')
    if 'entities' not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN entities TEXT DEFAULT ''")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_url ON links(url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_entry ON links(entry_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_a ON relations(entry_a)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_b ON relations(entry_b)")

    _record_schema_version(conn, 'v5_records')


def _migrate_v6_owner(conn):
    """v6：entries.owner 列（队列归属隔离）。"""
    applied = _get_applied_versions(conn)
    if 'v6_owner' in applied:
        return

    cols = _table_columns(conn, 'entries')
    if 'owner' not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN owner TEXT DEFAULT ''")

    _record_schema_version(conn, 'v6_owner')


def ensure_schema(db_path):
    """确保 wiki.db 表结构、FTS5 索引、辅助索引存在，并执行迁移。"""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA_SQL)
    conn.execute(FTS_SQL)
    conn.execute(EVENTS_SQL)
    conn.execute(LINKS_SQL)
    conn.execute(RELATIONS_SQL)
    conn.execute(SCHEMA_VERSION_SQL)

    existing_cols = _table_columns(conn, 'entries')
    applied = _get_applied_versions(conn)

    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        col = _extract_added_column(sql)
        if col and col in existing_cols:
            # 旧数据库已手动应用该迁移，仅补记录
            _record_schema_version(conn, version)
        else:
            conn.execute(sql)
            _record_schema_version(conn, version)

    # v4 字段重命名与清洗
    _migrate_v4_rename_fields(conn)

    # v4b 再次归一化 topic_type（例如从旧 markdown Type 字段同步后）
    _migrate_v4b_normalize_topic_types(conn)

    # v5 record-first：entities 列 + links/relations 索引
    _migrate_v5_records(conn)

    # v6：队列 owner 隔离
    _migrate_v6_owner(conn)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_topic_type ON entries(topic_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_action ON events(action)")

    # 一次性数据迁移：status 列刚加入时，有 md 文件的条目设为 done，否则 orphan
    if 'v1_seed_status_from_files' not in applied:
        non_pending = conn.execute("SELECT COUNT(*) FROM entries WHERE status != 'pending'").fetchone()[0]
        if non_pending == 0:
            conn.execute("UPDATE entries SET status = 'done' WHERE file IS NOT NULL AND file != ''")
            conn.execute("UPDATE entries SET status = 'orphan' WHERE file IS NULL OR file = ''")
        _record_schema_version(conn, 'v1_seed_status_from_files')

    conn.commit()
    conn.close()
