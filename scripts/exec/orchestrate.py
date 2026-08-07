#!/usr/bin/env python3
"""
Orchestrator — Wiki 执行模块（v3.1 record-only）。

只接受已入队并已 pop 的任务（--id），负责串联 classify → collect → interpret record → 输出 spawn JSON。
文章路径已于 v3.1 废除；使用 `skills/article-writer`（独立 skill）生成文章。

外部 agent 禁止直接调用本脚本；请通过 cli.py run --id <slug> 使用。

Usage:
  python orchestrate.py run --id <slug>
  python orchestrate.py classify --input "..."
  python orchestrate.py collect --slug ... --type ... --subtype ... --input ...
"""
import json
import os
import re
import shlex
import shutil
import sys
import argparse
from pathlib import Path

# 本脚本位于 exec/，需要 scripts/ 根目录才能导入 lib、wiki_index、intake 等公共模块

from scripts.lib import run_cmd, get_workspace
from scripts import paths
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

WORKSPACE = paths.get_workspace()
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SCRIPTS_DIR / 'cli.py'}"
DB_PATH = paths.db_path()

META_FILE_NAMES = {'source.txt', 'source_info.md', '_drill_log.json', 'prompt.md', '_fetch_results.json'}

from scripts import wiki_index
from scripts import intake
from scripts.intake import classify_source
from scripts.records.schema import RECORD_VERSION


def _log(slug, action, detail=None):
    try:
        wiki_index.record_event(DB_PATH, slug, action, detail)
    except Exception as e:
        print(f"[orchestrate] _log warning ({slug}/{action}): {e}", file=sys.stderr)


def run_script(script_name: str, args: list, timeout: int = 120) -> dict:
    script_path = Path(script_name) if os.path.isabs(script_name) else SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path)] + args
    return run_cmd(cmd, timeout=timeout)


class _NullStream:
    def write(self, *args, **kwargs): pass
    def flush(self): pass


def _json_error(error: str, message: str, detail: dict = None, next_cmd: str = None):
    out = {"ok": False, "error": error, "message": message}
    if detail: out["detail"] = detail
    if next_cmd: out["next_cmd"] = next_cmd
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _copy_local_source(src: str, dest_dir: Path):
    src_path = Path(src).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src_path.is_file():
        if src_path.suffix.lower() in ('.zip', '.tar', '.tar.gz', '.tgz', '.7z', '.rar'):
            shutil.copy2(str(src_path), str(dest_dir / src_path.name))
            if src_path.suffix.lower() == '.zip':
                import zipfile
                with zipfile.ZipFile(str(dest_dir / src_path.name), 'r') as z:
                    z.extractall(str(dest_dir / src_path.stem))
            elif src_path.suffix.lower() in ('.tar', '.tar.gz', '.tgz'):
                import tarfile
                with tarfile.open(str(dest_dir / src_path.name), 'r:*') as t:
                    t.extractall(str(dest_dir / src_path.stem))
        else:
            shutil.copy2(str(src_path), str(dest_dir / src_path.name))
    elif src_path.is_dir():
        shutil.copytree(str(src_path), str(dest_dir / src_path.name), dirs_exist_ok=True)


def _raw_dir_has_content(slug: str) -> bool:
    raw_dir = paths.raw_dir(slug)
    if not raw_dir.exists(): return False
    for f in raw_dir.rglob('*'):
        if f.is_file() and f.name not in META_FILE_NAMES: return True
    return False


def _event_exists(slug: str, action: str) -> bool:
    try:
        return bool(wiki_index.get_events(DB_PATH, slug=slug, action=action, limit=1))
    except Exception:
        return False


# ============================================================
# run（v3.1 record-only）
# ============================================================

def cmd_run(args):
    """执行已 pop 的任务：classify → collect → interpret record → spawn JSON。"""
    json_mode = getattr(args, 'json', False)
    quiet = getattr(args, 'quiet', False) or json_mode

    def log(msg=""):
        if not quiet: print(msg, file=sys.stdout)
    def err(msg=""):
        if not quiet: print(msg, file=sys.stderr)

    # 文章已废除
    if getattr(args, 'mode', None) == 'article' or getattr(args, 'depth', None):
        msg = "文章模式已于 v3.1 废除。请使用 cli.py run --id <slug>（record 模式），或独立的 article-writer skill。"
        if json_mode: _json_error("DEPRECATED_MODE", msg); return 1
        else: err(f"  ❌ {msg}"); return 1

    if not getattr(args, 'id', None):
        msg = "run 必须指定 --id。"
        if json_mode:
            _json_error("DEPRECATED_WORKFLOW", msg, next_cmd=f"{CLI_CMD} add --input ... && {CLI_CMD} pop && {CLI_CMD} run --id <slug>")
        else:
            err(f"  ❌ {msg}")
        return 1

    slug = args.id
    entry = wiki_index.get_entry(DB_PATH, slug)
    if not entry:
        msg = f"Entry not found: {slug}"
        if json_mode:
            _json_error("ENTRY_NOT_FOUND", msg)
        else:
            err(f"  ❌ {msg}")
        return 1

    if entry.get('status', '') not in {'pending', 'running'}:
        msg = f"Entry status is '{entry.get('status')}', expected pending/running."
        if json_mode:
            _json_error("INVALID_STATUS", msg, next_cmd=f"{CLI_CMD} requeue --id {slug}")
        else:
            err(f"  ❌ {msg}")
        return 1

    if not _event_exists(slug, 'ENQUEUE'):
        msg = f"Entry {slug} 缺少 ENQUEUE 事件。"
        if json_mode:
            _json_error("WORKFLOW_BYPASSED", msg, next_cmd=f"{CLI_CMD} add --input ... --id {slug}")
        else:
            err(f"  ❌ {msg}")
        return 1

    if not _event_exists(slug, 'STARTED'):
        msg = f"Entry {slug} 尚未被 pop（缺少 STARTED 事件）。"
        if json_mode:
            _json_error("WORKFLOW_BYPASSED", msg, next_cmd=f"{CLI_CMD} pop")
        else:
            err(f"  ❌ {msg}")
        return 1

    # Resolve input sources
    raw_input = entry.get('source_prompt') or entry.get('source_input') or ''
    input_sources = [s for s in raw_input.split('\n') if s.strip()]
    if not input_sources:
        if json_mode:
            _json_error("MISSING_SOURCE_INPUT", f"Entry has no source_input or source_prompt: {slug}")
        else:
            err(f"  ❌ Entry has no source_input or source_prompt: {slug}")
        return 1

    append_to = getattr(args, 'append_to', None)
    joined_input = '\n'.join(input_sources)
    log(f"\n{'='*50}")
    log(f"  Wiki Pipeline: {joined_input[:80]}")
    log(f"  Slug: {slug}" + (f" | Append to: {append_to}" if append_to else ""))
    log(f"{'='*50}")

    # record append: verify base entry exists
    if append_to:
        base = wiki_index.get_entry(DB_PATH, append_to)
        if not base:
            msg = f"append target not found: {append_to}"
            if json_mode: _json_error("ENTRY_NOT_FOUND", msg); return 1
            else: err(f"  ❌ {msg}"); return 1

    # === Step 1: Classify ===
    if entry.get('input_type') == 'local':
        log("\n[1/4] 跳过分类（local 输入）")
        classifications = [{
            'type': 'local', 'input_type': 'local', 'subtype': 'local_file',
            'source_type': 'local', 'confidence': 'high',
            'input': input_sources[0] if input_sources else '', 'label_cn': '本地文件',
        }]
    else:
        log("\n[1/4] 来源分类...")
        classifications = []
        for src in input_sources:
            try:
                c = intake.classify_one(src)
            except Exception as e:
                if json_mode:
                    _json_error("CLASSIFY_FAILED", str(e),
                                next_cmd=f"{CLI_CMD} classify --input {shlex.quote(src)}")
                else:
                    err(f"  ❌ Classify failed: {e}")
                return 1
            classifications.append(c)
            log(f"  ✅ {c['label_cn']} ({c['confidence']}) — {c['input'][:60]}")

    primary_source_type = intake.pick_primary_source_type(classifications)
    primary_input_type = 'local' if primary_source_type == 'local' else classifications[0].get('input_type', 'url')

    # === Step 2: Update metadata ===
    wiki_index.upsert_task(
        DB_PATH, slug,
        source_input=joined_input, source_prompt=joined_input,
        input_type=primary_input_type, source_type=primary_source_type,
        depth='brief', status='running', spec_version=RECORD_VERSION,
    )
    _log(slug, 'ENQUEUE', {'input': joined_input, 'sources': len(classifications),
                            'input_type': primary_input_type, 'source_type': primary_source_type})

    # === Step 3: Collect materials ===
    force_collect = getattr(args, 'force_collect', False)
    reuse_existing_raw = (_raw_dir_has_content(slug) and not force_collect)

    if reuse_existing_raw:
        log("\n[2/4] 跳过素材收集（raw/ 已存在且非空，使用 --force-collect 可强制重跑）")
        wiki_index.upsert_task(DB_PATH, slug, materials_ready=1)
        _log(slug, 'FETCH', {'status': 'skipped (reuse existing raw)'})
    elif primary_source_type == 'local' and len(classifications) == 1:
        log("\n[2/4] 跳过下载（local，复制素材到 raw/）")
        raw_dir = paths.raw_dir(slug)
        raw_dir.mkdir(parents=True, exist_ok=True)
        _copy_local_source(classifications[0]['input'], raw_dir)
        wiki_index.upsert_task(DB_PATH, slug, materials_ready=1)
        _log(slug, 'FETCH', {'status': 'skipped (local)'})
    else:
        log("\n[2/4] 收集素材...")
        if append_to:
            append_idx = _next_append_index(paths.raw_dir(slug))
            prefix = f"append_{append_idx}/"
            max_depth = 1
        else:
            prefix = ""
            max_depth = args.max_depth if getattr(args, 'max_depth', None) is not None else None
        coll_sources = [{
            'input_type': c['input_type'], 'source_type': c['source_type'], 'input': c['input'],
            'type': c.get('type'), 'subtype': c.get('subtype'),
        } for c in classifications]

        raw_dir = paths.raw_dir(slug)
        for i, c in enumerate(classifications):
            if c.get('source_type') == 'local':
                src_dir = raw_dir / f"s{i}" if len(classifications) > 1 else raw_dir
                src_dir.mkdir(parents=True, exist_ok=True)
                _copy_local_source(c['input'], src_dir)

        if len(coll_sources) == 1 and not prefix and coll_sources[0].get('source_type') != 'local':
            c = coll_sources[0]
            collect_args = ["--slug", slug, "--input-type", c['input_type'],
                           "--source-type", c['source_type'], "--input", c['input']]
            if max_depth is not None:
                collect_args += ["--max-depth", str(max_depth)]
            r = run_script("exec/collect_materials.py", collect_args, timeout=180)
        else:
            collect_args = ["--slug", slug, "--sources-json", json.dumps(coll_sources, ensure_ascii=False)]
            if prefix:
                collect_args += ["--max-depth", "1"]
            if max_depth is not None:
                collect_args += ["--max-depth", str(max_depth)]
            r = run_script("exec/collect_materials.py", collect_args, timeout=180)

        if r["ok"]:
            log((r["stdout"] or "")[:500])
            wiki_index.upsert_task(DB_PATH, slug, materials_ready=1)
            _log(slug, 'FETCH', {'status': 'success'})
        else:
            err(f"  ⚠️ Collect: {(r['stderr'] or 'unknown error')[:200]}")
            _log(slug, 'FETCH', {'status': 'failed', 'error': (r['stderr'] or 'unknown error')[:200]})
            if json_mode:
                _json_error("COLLECT_FAILED", r.get("stderr", "collect failed"))
                return 1

    # === Step 4: Generate record extraction task ===
    log(f"\n[3/4] Record 记录提取任务生成...")
    _log(slug, 'GATE', {'materials_ready': True, 'source_type': primary_source_type})

    interp_args = ["--slug", slug, "--source-type", primary_source_type, "--json", "--mode", "record"]
    if append_to:
        interp_args += ["--append-to", append_to]
    r = run_script("exec/generate_task.py", interp_args, timeout=120)
    if not r["ok"]:
        wiki_index.update_status(DB_PATH, slug, 'failed', error=f"interpret failed: {r['stderr'][:200]}")
        if json_mode:
            _json_error("INTERPRET_FAILED", r.get("stderr", "interpret failed"))
        else:
            err(f"  ❌ Interpret failed: {r['stderr']}")
        return 1

    try:
        task_spec = json.loads(r["stdout"])
    except json.JSONDecodeError:
        wiki_index.update_status(DB_PATH, slug, 'failed', error="invalid interpreter output")
        if json_mode:
            _json_error("INVALID_INTERPRET_OUTPUT", "interpret 输出不是有效 JSON")
        else:
            err(f"  ❌ Invalid interpret output")
        return 1

    _log(slug, 'WRITE', {'model': task_spec.get('model'), 'taskName': task_spec.get('taskName')})

    # === Step 5: spawn spec ===
    publish_cmd = f"{sys.executable} {SCRIPTS_DIR / 'wiki_db.py'} publish --id {slug}"
    spawn_spec = {
        "task": task_spec["task"],
        "taskName": task_spec["taskName"],
        "model": task_spec.get("model"),
        "mode": "run",
        "task_mode": "record",
        "cleanup": "keep",
        "context": "isolated",
        "depth": None,
        "slug": slug,
        "output_path": str(Path(task_spec.get("output_path", paths.record_path(slug))).resolve()),
        "verify_cmd": publish_cmd,
        "publish_cmd": publish_cmd,
        "spawn_cmd": (
            f"sessions_spawn --taskName {shlex.quote(task_spec['taskName'])} "
            f"--mode run "
            f"--task <see JSON 'task' field>"
        ),
        "raw_dir": str(paths.raw_dir(slug)),
        "sources_count": len(classifications),
        "source_inputs": input_sources,
    }
    if append_to:
        spawn_spec["append_to"] = append_to

    if getattr(args, 'one_liner', False):
        print(f"{spawn_spec['spawn_cmd']}; {publish_cmd}")
        return 0

    if json_mode:
        print(json.dumps(spawn_spec, ensure_ascii=False, indent=2))
        return 0

    log(f"  模型: 跟随调用方（skill 不配置）")
    log(f"  taskName: {task_spec['taskName']}")
    log(f"  output: {spawn_spec['output_path']}")
    log(f"\n  --- sessions_spawn 参数 ---")
    log(f"  {task_spec['task'][:300]}...")

    log(f"\n[4/4] 验证并发布...")
    if paths.record_path(slug).exists():
        r = run_script("wiki_db.py", ["publish", "--id", slug], timeout=30)
        log(f"  ✅ 验证并发布成功" if r["ok"] else f"  ⚠️ 验证或发布失败:\n{r['stdout'][:500]}")
    else:
        log(f"  ⏳ 等待子 agent 完成写入后验证")
    return 0


def cmd_classify(args):
    r = run_script("intake/classify_source.py", ["--input", args.input])
    print(r["stdout"] if r["ok"] else r["stderr"])
    return r["exit_code"] if not r["ok"] else 0


def cmd_collect(args):
    collect_args = ["--slug", args.slug, "--input-type", args.input_type,
                   "--source-type", args.source_type, "--input", args.input]
    r = run_script("exec/collect_materials.py", collect_args, timeout=180)
    print(r["stdout"] if r["ok"] else r["stderr"])
    return r["exit_code"] if not r["ok"] else 0


def main():
    parser = argparse.ArgumentParser(description="Wiki 工作流总入口")
    sub = parser.add_subparsers(dest="command", help="命令")

    p_run = sub.add_parser("run", help="执行已 pop 的任务（record 提取）")
    p_run.add_argument("--id", required=True)
    p_run.add_argument("--max-depth", type=int, default=3)
    p_run.add_argument("--force-collect", action="store_true")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--one-liner", action="store_true")
    # 以下参数仅用于返回明确的废除错误（v3.1）
    p_run.add_argument("--mode", choices=["record", "article"], default=None)
    p_run.add_argument("--depth", choices=["brief", "deep"], default=None)
    p_run.add_argument("--append-to")

    p_cls = sub.add_parser("classify", help="来源分类")
    p_cls.add_argument("--input", "-i", required=True)

    p_col = sub.add_parser("collect", help="收集素材")
    p_col.add_argument("--slug", required=True)
    p_col.add_argument("--input-type", required=True, dest="input_type")
    p_col.add_argument("--source-type", required=True, dest="source_type")
    p_col.add_argument("--input", required=True)

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args) or 0)
    elif args.command == "classify":
        sys.exit(cmd_classify(args))
    elif args.command == "collect":
        sys.exit(cmd_collect(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
