#!/usr/bin/env python3
"""
intake/commands.py — wiki 入队命令（原 wiki_db.py cmd_add）。

从 wiki_db.py 拆分而来，保持 CLI 行为不变。
"""
import sys
import argparse
import json
import hashlib
from datetime import date
from pathlib import Path


from scripts import wiki_index
from scripts import intake
def _out_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_add(args, db_path):
    """添加 pending 任务，支持多源输入、自动分类、追加模式，并记录 ENQUEUE 事件。"""
    append_to = getattr(args, 'append_to', None)
    base_entry = None
    if append_to:
        base_entry = wiki_index.get_entry(db_path, append_to)
        if not base_entry:
            msg = f'Entry not found for append: {append_to}'
            if args.json:
                _out_json({"ok": False, "error": "NOT_FOUND", "message": msg})
            else:
                print(msg)
            sys.exit(1)

    try:
        intake_result = intake.prepare_intake(
            input_list=args.input,
            inputs_file=args.inputs_file,
            source_prompt=args.source_prompt,
        )
    except Exception as e:
        msg = f'Invalid input: {e}'
        if args.json:
            _out_json({"ok": False, "error": "INVALID_INPUT", "message": msg})
        else:
            print(msg)
        sys.exit(1)

    joined_input = intake_result['joined_input']
    source_prompt = intake_result['source_prompt']

    if append_to:
        slug = append_to
        new_source_input = joined_input
        new_source_prompt = source_prompt
        title = base_entry.get('title') or new_source_input[:80]
    else:
        today = date.today().isoformat()
        h = int(hashlib.md5(joined_input.encode()).hexdigest()[:8], 16) % 10000
        slug = args.id or f"{today}_{h:04d}"
        new_source_input = joined_input
        new_source_prompt = source_prompt
        title = new_source_input[:80]

    input_type = args.input_type if args.input_type != 'unknown' else intake_result['primary_input_type']
    source_type = args.source_type if args.source_type != 'unknown' else intake_result['primary_source_type']

    # v3.4: 队列 owner 归属（来自 WIKI_OWNER 环境变量，避免跨 session 互取条目）
    import os as _os
    owner = (_os.environ.get("WIKI_OWNER") or "").strip() or "claude-code"

    entry = wiki_index.upsert_task(
        db_path, slug,
        source_input=new_source_input,
        source_prompt=new_source_prompt,
        input_type=input_type,
        source_type=source_type,
        depth=args.depth or 'brief',
        status='pending',
        title=title,
        owner=owner,
    )

    wiki_index.record_event(db_path, slug, 'ENQUEUE', {
        'input': new_source_input,
        'sources': len(intake_result['sources']),
        'input_type': input_type,
        'source_type': source_type,
        # 兼容性别名
        'type': input_type,
        'subtype': source_type,
        'depth': args.depth or 'brief',
        'append_to': append_to,
    })

    if args.json:
        _out_json({"id": entry['id'], "status": entry['status'], "source_type": source_type,
                   "joined_input": joined_input, "append_to": append_to or ""})
    else:
        print(f"Added: {entry['id']} (status={entry['status']}, source_type={source_type})")
