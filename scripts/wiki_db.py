#!/usr/bin/env python3
"""
wiki_db.py — wiki.db 的统一 CLI（仅负责命令路由）。

所有命令实现已拆分到：
  - store/commands.py   （list/search/pop/dedup/update/delete/status/events/stats/migrate-queue）
  - intake/commands.py  （add）
  - publish/commands.py （publish/requeue/sync/record-event）

Usage:
  python wiki_db.py list [--limit N] [--type TYPE] [--status STATUS]
  python wiki_db.py search "世界模型" [--limit 10]
  python wiki_db.py add --input "..." --type paper --subtype arxiv_paper --depth brief
  python wiki_db.py requeue --id xxx [--clear-md]
  python wiki_db.py status --id xxx [--set pending]
  python wiki_db.py sync [--rebuild]
  python wiki_db.py stats
"""
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

from scripts import paths
from scripts import wiki_index
from scripts.wiki_index.schema import VALID_STATUSES

from scripts.store import commands as store_cmds
from scripts.intake import commands as intake_cmds
from scripts.publish import commands as publish_cmds
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB_PATH = paths.db_path()
WIKI_DIR = paths.get_workspace()
SCRIPTS_DIR = SCRIPT_DIR


def main():
    parser = argparse.ArgumentParser(description='wiki.db unified CLI')
    parser.add_argument('--json', action='store_true', help='输出结构化 JSON')
    sub = parser.add_subparsers(dest='command')

    p_list = sub.add_parser('list', help='List entries')
    p_list.add_argument('--limit', '-n', type=int)
    p_list.add_argument('--topic-type', '--type', dest='topic_type')
    p_list.add_argument('--input-type', dest='input_type')
    p_list.add_argument('--source-type', dest='source_type')
    p_list.add_argument('--status', choices=sorted(VALID_STATUSES))
    p_list.add_argument('--all', action='store_true', help='Show more results (queue style)')
    p_list.add_argument('--human', action='store_true', help='Use queue-style compact output')

    p_search = sub.add_parser('search', help='FTS5 search')
    p_search.add_argument('query')
    p_search.add_argument('--limit', '-n', type=int, default=10)

    p_add = sub.add_parser('add', help='Add a pending task/entry')
    p_add.add_argument('--input', '-i', action='append', required=True,
                       help='User input source (URL / keyword / local path); repeatable')
    p_add.add_argument('--inputs-file', help='File with one source per line')
    p_add.add_argument('--source-prompt', help='Original user prompt (defaults to --input)')
    p_add.add_argument('--append-to', help='Append new sources to an existing entry')
    p_add.add_argument('--input-type', '--type', dest='input_type', default='unknown')
    p_add.add_argument('--source-type', '--subtype', dest='source_type', default='unknown')
    p_add.add_argument('--depth', default='brief')
    p_add.add_argument('--id', help='Custom slug (default: hash-based)')

    p_update = sub.add_parser('update', help='Update entry metadata')
    p_update.add_argument('--id', help='Entry id/slug')
    p_update.add_argument('--slug', help='Alias for --id (queue style)')
    p_update.add_argument('--title')
    p_update.add_argument('--overview')
    p_update.add_argument('--topic-type', '--type', dest='topic_type')
    p_update.add_argument('--date')
    p_update.add_argument('--ver')
    p_update.add_argument('--sources')
    p_update.add_argument('--raw')
    p_update.add_argument('--file')
    p_update.add_argument('--depth')
    p_update.add_argument('--tags')
    p_update.add_argument('--source_input')
    p_update.add_argument('--source_prompt')
    p_update.add_argument('--input-type', '--source_type', dest='input_type')
    p_update.add_argument('--source-type', '--source_subtype', dest='source_type')
    p_update.add_argument('--status', choices=sorted(VALID_STATUSES))
    p_update.add_argument('--error')
    p_update.add_argument('--wiki-path', help='Set file field from path (queue style)')
    p_update.add_argument('--materials_ready', type=int)

    p_delete = sub.add_parser('delete', help='Delete entry')
    p_delete.add_argument('--id', required=True)

    p_pop = sub.add_parser('pop', help='Pop pending tasks (queue style)')
    p_pop.add_argument('--limit', '-n', type=int, default=3)

    p_dedup = sub.add_parser('dedup', help='Check duplicate input (queue style)')
    p_dedup.add_argument('--input', '-i', required=True)

    p_requeue = sub.add_parser('requeue', help='Requeue an entry')
    p_requeue.add_argument('--id', required=True)
    p_requeue.add_argument('--clear-md', action='store_true',
                           help='Also delete existing markdown files')

    p_status = sub.add_parser('status', help='Show or set entry status')
    p_status.add_argument('--id', required=True)
    p_status.add_argument('--set', choices=sorted(VALID_STATUSES))
    p_status.add_argument('--error')

    p_sync = sub.add_parser('sync', help='Consistency check or rebuild')
    p_sync.add_argument('--rebuild', action='store_true',
                        help='Rebuild metadata from markdown (preserves task fields)')

    p_migrate = sub.add_parser('migrate-queue', help='Migrate legacy queue.db into wiki.db')
    p_migrate.add_argument('--delete', action='store_true',
                           help='Delete queue.db after successful migration')

    p_events = sub.add_parser('events', help='Show audit events')
    p_events.add_argument('--id', required=True)
    p_events.add_argument('--action')
    p_events.add_argument('--limit', '-n', type=int, default=50)

    p_stats = sub.add_parser('stats', help='Show stats')
    p_stats.add_argument('--human', action='store_true', help='Compact human-readable summary')

    p_publish = sub.add_parser('publish', help='Verify output and mark entry as done')
    p_publish.add_argument('--id', required=True, help='Entry slug')
    p_publish.add_argument('--site-only', action='store_true',
                           help='只重建站点，跳过 record 校验/入库（用于 trends 更新后）')
    p_publish.add_argument('--depth', choices=['brief', 'deep'], default=None,
                           help='显式指定走文章校验；缺省为记录发布（record.json）')
    p_publish.add_argument('--spec', help='Path to output spec YAML')
    p_publish.add_argument('--title', help='Article title')

    p_record = sub.add_parser('record-event', help='Record an audit event')
    p_record.add_argument('--id', required=True, help='Entry slug')
    p_record.add_argument('--action', required=True, choices=sorted({'ENQUEUE', 'FETCH', 'GATE', 'WRITE', 'VERIFY', 'DONE'}))
    p_record.add_argument('--detail', help='JSON or string detail')

    args = parser.parse_args()

    dispatch = {
        'list': lambda: store_cmds.cmd_list(args, DB_PATH),
        'search': lambda: store_cmds.cmd_search(args, DB_PATH),
        'add': lambda: intake_cmds.cmd_add(args, DB_PATH),
        'update': lambda: store_cmds.cmd_update(args, DB_PATH),
        'delete': lambda: store_cmds.cmd_delete(args, DB_PATH),
        'pop': lambda: store_cmds.cmd_pop(args, DB_PATH),
        'dedup': lambda: store_cmds.cmd_dedup(args, DB_PATH),
        'requeue': lambda: publish_cmds.cmd_requeue(args, DB_PATH, WIKI_DIR),
        'status': lambda: store_cmds.cmd_status(args, DB_PATH),
        'sync': lambda: publish_cmds.cmd_sync(args, DB_PATH, WIKI_DIR),
        'migrate-queue': lambda: store_cmds.cmd_migrate_queue(args, DB_PATH, WIKI_DIR),
        'events': lambda: store_cmds.cmd_events(args, DB_PATH),
        'stats': lambda: store_cmds.cmd_stats(args, DB_PATH),
        'publish': lambda: publish_cmds.cmd_publish(args, DB_PATH, WIKI_DIR, SCRIPTS_DIR),
        'record-event': lambda: publish_cmds.cmd_record_event(args, DB_PATH),
    }

    if args.command in dispatch:
        dispatch[args.command]()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
