#!/usr/bin/env python3
"""
reconcile.py — 子 agent 完成对账（artifact 事实源兜底）。

harness 的 subagent 完成事件是 best-effort（可能静默丢失，
见 wiki/docs/issues/2026-08-28_001_subagent-completion-no-reconcile.md）。
本命令以 wiki/artifacts/<slug>/record.json 为事实源对账 expected slugs：
record 存在且非空即视为提取完成，可直接 publish。

只读命令：不写 db、不改状态，只输出对账结果与下一步命令。

用法：
    python scripts/cli.py --json reconcile --id A --id B   # 对账 ledger 全量
    python scripts/cli.py --json reconcile                 # 默认对账所有 status=running 条目
"""
import argparse
import json
import sys

from scripts import paths
from scripts.wiki_index import store


def reconcile(slugs=None, ws=None):
    """对账 expected slugs；slugs 为 None/空时自动取 db 中 status=running 的条目。

    返回 {"checked", "all_done", "done": [...], "missing": [...], "next_cmds": [...]}
    """
    ws = paths._resolve_ws(ws)
    db = paths.db_path(ws)
    if not slugs:
        slugs = [e["id"] for e in store.list_entries(db, status="running")]
    done, missing = [], []
    for slug in slugs:
        entry = store.get_entry(db, slug)
        status = (entry or {}).get("status")
        rp = paths.record_path(slug, ws)
        size = rp.stat().st_size if rp.exists() else 0
        if size > 0:
            done.append({"id": slug, "status": status, "record_bytes": size})
        else:
            missing.append({"id": slug, "status": status})
    # 已产出 record 但尚未发布的条目 → 给出 publish 命令
    next_cmds = ["python scripts/cli.py --json publish --id " + d["id"]
                 for d in done if d["status"] != "done"]
    return {
        "checked": len(slugs),
        "all_done": not missing,
        "done": done,
        "missing": missing,
        "next_cmds": next_cmds,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="子 agent 完成对账（artifact 事实源）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--id", action="append", dest="ids",
                        help="expected slug（可重复；缺省对账全部 running 条目）")
    args = parser.parse_args(argv)
    try:
        data = reconcile(args.ids)
    except Exception as e:
        result = {"ok": False, "error": "RECONCILE_FAILED", "message": str(e)}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
              else f"❌ RECONCILE_FAILED: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    else:
        print(f"checked={data['checked']} done={len(data['done'])} missing={len(data['missing'])}")
        for d in data["done"]:
            mark = "✅" if d["status"] == "done" else "📦"
            print(f"  {mark} {d['id']}  status={d['status']} record={d['record_bytes']}B")
        for m in data["missing"]:
            print(f"  ⏳ {m['id']}  status={m['status']} record=MISSING")
        for c in data["next_cmds"]:
            print(f"  next: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
