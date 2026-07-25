#!/usr/bin/env python3
"""
Wiki Harness Doctor — record 时代健康自检（v3.2 精简版）。

检查项目：
  1. index freshness      — wiki.db 与最新 record.json 的新鲜度对比
  2. queue status         — wiki.db 队列统计
  3. db consistency       — db 与 artifacts 文件一致性
  4. git status           — wiki/ 未提交变更
  5. record tier          — 最近 done 条目是否有 record.json
  6. unindexed entities   — 全库 entities 与 entity_aliases 差集（信息性）

Usage:
  python skills/wiki-curation/scripts/doctor.py [--json] [--quick] [--fix-plan]
"""
import os, re, sys, json, glob, shlex
from pathlib import Path
from datetime import datetime, timezone

from scripts import paths
from scripts.lib import run_cmd

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

WORKSPACE = paths.get_workspace()
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_SCRIPTS_DIR = SCRIPT_DIR
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"
PROJECT_ROOT = WORKSPACE.parent

from scripts import wiki_index


# ============================================================
# 1. 索引新鲜度
# ============================================================
def check_index_freshness():
    db_path = paths.db_path()
    if not db_path.exists():
        return {'check': 'index freshness (wiki.db)', 'passed': False,
                'summary': 'wiki.db NOT FOUND'}
    db_time = datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc)
    records = sorted(glob.glob(str(paths.artifacts_dir() / '*' / 'record.json')))
    if not records:
        return {'check': 'index freshness', 'passed': True, 'summary': 'No records yet'}
    newest = datetime.fromtimestamp(os.path.getmtime(records[-1]), tz=timezone.utc)
    stale = db_time < newest
    status = 'FRESH' if not stale else 'STALE'
    return {
        'check': 'index freshness (wiki.db)',
        'passed': not stale,
        'summary': (f'{status}: db {db_time.strftime("%m-%d %H:%M")} vs '
                    f'newest record {newest.strftime("%m-%d %H:%M")}'),
    }


# ============================================================
# 2. 队列状态
# ============================================================
def check_queue_status():
    try:
        stats = wiki_index.get_stats(paths.db_path())
        sc = stats.get('status_counts', {})
        return {
            'check': 'queue status (wiki.db)',
            'passed': True,
            'summary': (f"total={stats.get('total', 0)}, pending={sc.get('pending', 0)}, "
                        f"running={sc.get('running', 0)}, done={sc.get('done', 0)}, "
                        f"failed={sc.get('failed', 0)}, orphan={sc.get('orphan', 0)}"),
        }
    except Exception as e:
        return {'check': 'queue status', 'passed': True, 'summary': f'Error: {e}'}


# ============================================================
# 3. db 与文件一致性
# ============================================================
def check_db_md_consistency():
    try:
        report = wiki_index.sync_with_files(paths.db_path(), paths.get_workspace())
        db_only = report.get('db_only', [])
        md_only = report.get('md_only', [])
        mismatch = report.get('mismatch', [])
        orphan_done = []
        for eid in db_only:
            e = wiki_index.get_entry(paths.db_path(), eid)
            if e and e.get('status') == 'done':
                orphan_done.append(eid)
        passed = len(orphan_done) == 0 and len(md_only) == 0 and len(mismatch) == 0
        return {
            'check': 'db vs files consistency',
            'passed': passed,
            'summary': (f'{report["both"]} matched, {len(db_only)} db-only '
                        f'({len(orphan_done)} done-orphan), {len(md_only)} file-only, '
                        f'{len(mismatch)} mismatch'),
            'details': report,
        }
    except Exception as e:
        return {'check': 'db vs files consistency', 'passed': False, 'summary': f'Error: {e}'}


# ============================================================
# 4. Git 状态
# ============================================================
def check_git_status():
    try:
        r = run_cmd(['git', 'status', '--porcelain', 'wiki/'], timeout=10, cwd=str(PROJECT_ROOT))
        dirty = [l for l in r.get('stdout', '').strip().split('\n') if l.strip()]
        return {
            'check': 'git status (wiki/)',
            'passed': len(dirty) == 0,
            'summary': f'{len(dirty)} uncommitted file(s)' if dirty else 'Clean',
            'detail': dirty[:10] if dirty else [],
        }
    except Exception as e:
        return {'check': 'git status', 'passed': True, 'summary': f'Not a git repo: {e}'}


# ============================================================
# 5. 记录层完整性
# ============================================================
def check_record_tier():
    """最近 20 条 done 条目应至少有 record.json（历史条目不追溯审查）。"""
    from scripts.wiki_index.store import list_entries
    db_path = paths.db_path()
    ws = paths.get_workspace()
    recent_done = [e for e in list_entries(db_path, order_by='date DESC, id DESC', status='done')][:20]
    missing = [e['id'] for e in recent_done
               if not paths.record_path(e['id'], ws).exists()]
    return {
        'check': 'record tier (recent done)',
        'passed': len(missing) == 0,
        'summary': (f'{len(recent_done) - len(missing)}/{len(recent_done)} recent done have record'
                    + (f', missing: {", ".join(missing[:5])}' if missing else '')),
        'details': missing,
    }


# ============================================================
# 6. 未收录实体（信息性）
# ============================================================
def check_unindexed_entities():
    """全库 entities 列与 entity_aliases.yaml 差集报告。"""
    try:
        from scripts.records import links as L
        from scripts.site.entities import load_aliases
        db_path = paths.db_path()
        aliases = load_aliases()
    except Exception as e:
        return {'check': 'unindexed entities', 'passed': True, 'summary': f'SKIP: {e}'}

    indexed = set()
    for canonical, variants in (aliases.get("terms") or {}).items():
        indexed.add(canonical.lower())
        for v in (variants or []):
            indexed.add(str(v).strip().lower())
    for _etype, ent_map in (aliases.get("entities") or {}).items():
        for canonical, variants in (ent_map or {}).items():
            indexed.add(canonical.lower())
            for v in (variants or []):
                indexed.add(str(v).strip().lower())

    all_ents = L.all_entry_entities(db_path)
    unindexed = set()
    for buckets in all_ents.values():
        for vals in buckets.values():
            for v in vals:
                v = str(v).strip()
                if v and v.lower() not in indexed and v not in unindexed:
                    unindexed.add(v)

    result = sorted(unindexed)[:30]
    return {
        'check': 'unindexed entities',
        'passed': True,  # 信息性检查，不 fail
        'summary': f'{len(result)} unindexed entities (top 30 shown)' if result else 'All entities indexed',
        'details': result,
    }


# ============================================================
# Fix plan（record 时代简化版）
# ============================================================
def generate_fix_plan(checks) -> list[dict]:
    by_name = {c['check']: c for c in checks}
    actions = []

    db_md = by_name.get('db vs files consistency', {})
    details = db_md.get('details', {}) if isinstance(db_md.get('details'), dict) else {}
    for eid in (details.get('db_only') or [])[:20]:
        actions.append({
            "type": "mark_orphan",
            "risk": "medium",
            "reason": "entry in db but no artifacts",
            "command": f"{CLI_CMD} update --id {shlex.quote(eid)} --status orphan",
        })
    if details.get('md_only'):
        actions.append({
            "type": "sync",
            "risk": "low",
            "reason": "files exist but not indexed in db",
            "command": f"{CLI_CMD} sync",
        })

    git_check = by_name.get('git status (wiki/)', {})
    if not git_check.get('passed', True):
        actions.append({
            "type": "commit",
            "risk": "low",
            "reason": "uncommitted changes in wiki/",
            "command": "git add wiki/ && git commit -m 'wiki: doctor fix plan' && git push",
        })
    return actions


# ============================================================
# main
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Wiki Harness Doctor — health check')
    parser.add_argument('--since', help='(unused, kept for CLI compat)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--fix-plan', action='store_true')
    parser.add_argument('--fix-metadata', action='store_true', help='(deprecated, no-op)')
    args = parser.parse_args()

    checks = []

    def run(fn, *fn_args):
        try:
            result = fn(*fn_args)
            if not isinstance(result, dict):
                result = {'check': fn.__name__, 'passed': False,
                          'summary': f'CRASH: check returned {type(result).__name__} instead of dict'}
        except Exception as e:
            result = {'check': fn.__name__, 'passed': False, 'summary': f'CRASH: {e}'}
        checks.append(result)

    run(check_index_freshness)
    run(check_queue_status)
    run(check_db_md_consistency)
    run(check_git_status)
    run(check_record_tier)
    run(check_unindexed_entities)

    passed = sum(1 for c in checks if c.get('passed', False))
    total = len(checks)
    score_pct = int(passed / total * 100) if total > 0 else 0

    if score_pct >= 90: grade = 'A'
    elif score_pct >= 70: grade = 'B'
    elif score_pct >= 50: grade = 'C'
    else: grade = 'F'

    fix_plan = generate_fix_plan(checks) if args.fix_plan else []

    if args.json:
        output = {
            'grade': grade,
            'score': f'{passed}/{total} ({score_pct}%)',
            'checks': checks,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        if args.fix_plan:
            output['fix_plan'] = fix_plan
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            icon = '✅' if c.get('passed', False) else '❌'
            print(f"{icon}  {c['check']}")
            print(f"    {c.get('summary', '')}")
        print(f'Grade: {grade}  ({passed}/{total} checks passed)')
        if args.fix_plan:
            print('\nFix plan:')
            for action in fix_plan:
                print(f"  [{action['type']}/{action['risk']}] {action.get('command', '')} — {action.get('reason', '')}")
    return 0 if grade != 'F' else 1


if __name__ == "__main__":
    sys.exit(main())
