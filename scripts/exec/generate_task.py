#!/usr/bin/env python3
"""
generate_task.py — Record 提取任务生成器（v3.1 record-only）。

直接调用 scripts.records.interpret_record.generate_record_task。
文章任务生成已于 v3.1 废除（见独立 article-writer skill）。

Usage:
  python skills/wiki-curation/scripts/exec/generate_task.py --slug <slug> --source-type <type> --json
"""
import json
import argparse
import os
import sys
from pathlib import Path

if not os.environ.get('WIKI_WORKSPACE'):
    script_dir = Path(__file__).resolve().parent
    inferred_ws = script_dir.parent.parent.parent.parent / 'wiki'
    if inferred_ws.exists():
        os.environ['WIKI_WORKSPACE'] = str(inferred_ws)
    else:
        cwd_ws = Path.cwd() / 'wiki'
        if cwd_ws.exists():
            os.environ['WIKI_WORKSPACE'] = str(cwd_ws)

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from scripts.records.interpret_record import generate_record_task


def main():
    parser = argparse.ArgumentParser(description="Record 提取任务生成器（v3.1 record-only）")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--source-type", "--type", dest="source_type", default="paper")
    parser.add_argument("--mode", choices=["record"], default="record")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate_record_task(args.slug, args.source_type)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"  📝 Record 任务: {result['slug']}")
        print(f"  输出: {result['output_path']}")
        print(f"\n  --- Task ---")
        print(result["task"])


if __name__ == "__main__":
    main()
