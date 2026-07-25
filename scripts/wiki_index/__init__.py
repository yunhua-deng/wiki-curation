#!/usr/bin/env python3
"""
wiki_index package — unified wiki.db metadata, queue, FTS, and markdown indexing.

Backwards-compatible re-export of all public functions previously in wiki_index.py.
"""

# Schema / migrations
from .schema import (
    SCHEMA_SQL,
    FTS_SQL,
    EVENTS_SQL,
    SCHEMA_VERSION_SQL,
    MIGRATIONS,
    VALID_STATUSES,
    ensure_schema,
)

# Store / CRUD / queue / events / FTS
from .store import (
    migrate_queue_db,
    get_entry,
    get_entry_by_file,
    upsert_task,
    update_status,
    pop_pending,
    requeue,
    update_entry,
    delete_entry,
    search,
    list_entries,
    get_stats,
    record_event,
    get_events,
    check_events_complete,
)

# v3.1: sync/rebuild moved to store
from .store import rebuild_index, sync_with_files

__all__ = [
    "SCHEMA_SQL", "FTS_SQL", "EVENTS_SQL", "SCHEMA_VERSION_SQL",
    "MIGRATIONS", "VALID_STATUSES", "ensure_schema",
    "migrate_queue_db", "get_entry", "get_entry_by_file",
    "upsert_task", "update_status", "pop_pending", "requeue",
    "update_entry", "delete_entry", "search", "list_entries",
    "get_stats", "record_event", "get_events", "check_events_complete",
    "rebuild_index", "sync_with_files",
]
