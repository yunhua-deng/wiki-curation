#!/usr/bin/env python3
"""
Orchestrator — Wiki 执行模块（v3.1 record-only）。

只接受已入队并已 pop 的任务（--id），负责串联 classify → collect → interpret record → 输出 spawn JSON。
文章路径已于 v3.1 废除；使用 `skills/article-writer`（独立 skill）生成文章。

外部 agent 禁止直接调用本脚本；请通过 cli.py run --id <slug> 使用。

Usage:
  python orchestrate.py run --id <slug>
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

from scripts.lib import run_cmd
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
from scripts.records.schema import RECORD_VERSION


def _log(slug, action, detail=None):
    try:
        wiki_index.record_event(DB_PATH, slug, action, detail)
    except Exception as e:
        print(f"[orchestrate] _log warning ({slug}/{action}): {e}", file=sys.stderr)


def run_script(script_name: str, args: list, timeout: int = 120) -> dict:
    """调用 skill 子脚本（collect_materials / generate_task / wiki_db）。

    统一 retries=0：这些步骤有副作用且非零退出码本身就是正常失败信号，
    盲目重试会重复抓取、重复写盘。
    """
    script_path = Path(script_name) if os.path.isabs(script_name) else SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path)] + args
    return run_cmd(cmd, timeout=timeout, retries=0)


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


# ------------------------------------------------------------
# 声明来源 vs 已抓取来源（复用判定 + 缺料门禁的共用基础）
# ------------------------------------------------------------

APPEND_DIR_RE = re.compile(r'^append_(\d+)$')
ERROR_FIELD_LIMIT = 200


def _next_append_index(raw_dir: Path) -> int:
    """下一个 append 子目录序号：扫描 raw/append_*/ 取 max+1（无既有子目录则为 1）。"""
    if not raw_dir.exists():
        return 1
    indices = []
    for child in raw_dir.iterdir():
        if child.is_dir():
            m = APPEND_DIR_RE.match(child.name)
            if m:
                indices.append(int(m.group(1)))
    return max(indices) + 1 if indices else 1


def _normalize_source_key(text: str) -> str:
    """来源归一化：去 scheme / query / fragment、host 小写、去尾部 /，用于宽松匹配。"""
    s = (text or '').strip()
    if not s:
        return ''
    s = re.sub(r'^[A-Za-z][A-Za-z0-9+.\-]*://', '', s)
    s = s.split('#', 1)[0].split('?', 1)[0]
    if s.lower().startswith('www.'):
        s = s[4:]
    head, sep, tail = s.partition('/')
    return (head.lower() + sep + tail).rstrip('/')


def _source_keys_match(a: str, b: str) -> bool:
    """宽松匹配：归一化后相等，或一方包含另一方（容忍 handler 改写 URL 的装饰性差异）。"""
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _drill_status_map(raw_dir: Path) -> dict:
    """递归读取 raw/ 下所有 _drill_log.json 的 level-1 条目，返回 {归一化来源: 状态}。

    以 drill log 的 level-1 条目为准，而不是 _fetch_results.json：后者不含每个声明来源
    的记录（如 handler_arxiv 只记 PDF URL，从不记声明的 arxiv.org/abs/...）。
    同一来源出现多次时，越新的 drill log 越晚写入 → 以最后写入的状态为准。
    """
    statuses = {}
    if not raw_dir.exists():
        return statuses
    logs = sorted(raw_dir.rglob('_drill_log.json'),
                  key=lambda p: (p.stat().st_mtime if p.exists() else 0, p.as_posix()))
    for path in logs:
        try:
            log = json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except Exception:
            continue
        for level in log.get('levels') or []:
            if level.get('level') != 1:
                continue
            for entry in level.get('entries') or []:
                key = _normalize_source_key(entry.get('input') or '')
                if key:
                    statuses[key] = entry.get('status') or 'failed'
    return statuses


def _missing_declared_sources(raw_dir: Path, declarations: list) -> list:
    """声明来源中尚无可信材料的清单：[{'url': 原始输入, 'status': 状态或 missing}]。"""
    statuses = _drill_status_map(raw_dir)
    missing = []
    for c in declarations or []:
        raw_input = c.get('input') or ''
        key = _normalize_source_key(raw_input)
        status = ''
        for logged_key, logged_status in statuses.items():
            if _source_keys_match(key, logged_key):
                status = logged_status
                break
        if status != 'success':
            missing.append({'url': raw_input, 'status': status or 'missing'})
    return missing


def _last_stderr_line(stderr: str) -> str:
    """traceback 的有效信息在最后一行（异常类型 + 消息），比首行更能定位问题。"""
    lines = [l.strip() for l in (stderr or '').splitlines() if l.strip()]
    return lines[-1] if lines else ''


def _enqueue_append_to(slug: str) -> str:
    """从 ENQUEUE 事件里还原 append 意图（cmd_add 写 detail.append_to）。

    `run --id <base>` 不带 --append-to 时，append 意图只存在于事件流中。取最近若干条
    ENQUEUE 中最新一条带 append_to 的记录：orchestrate 自己每次 run 也会补一条无
    append_to 的 ENQUEUE，只看最新一条会让 requeue 重跑丢掉 append 意图。
    """
    try:
        events = wiki_index.get_events(DB_PATH, slug=slug, action='ENQUEUE', limit=20)
    except Exception:
        return ''
    for event in events:
        try:
            detail = json.loads(event.get('detail') or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        append_to = (detail.get('append_to') or '').strip() if isinstance(detail, dict) else ''
        if append_to:
            return append_to
    return ''


def _append_requires_published(json_mode: bool, append_to: str, status: str, reason: str = "") -> int:
    """append 前置条件不满足时的统一报错（返回 1，供调用方直接 return）。"""
    msg = (f"条目 {append_to} 未发布（当前状态: {status}{reason}），"
           f"append 仅支持已发布条目；多来源请在首次 add 时一并提供")
    if json_mode:
        _json_error("APPEND_REQUIRES_PUBLISHED", msg)
    else:
        print(f"  ❌ APPEND_REQUIRES_PUBLISHED: {msg}", file=sys.stderr)
    return 1


def _base_is_published(slug: str) -> bool:
    """base 是否发布过：有 record.json 或 DONE 事件（append 自条目时用）。"""
    try:
        if paths.record_path(slug).exists():
            return True
    except Exception:
        pass
    return _event_exists(slug, 'DONE')


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

    append_to = getattr(args, 'append_to', None) or _enqueue_append_to(slug)
    joined_input = '\n'.join(input_sources)
    log(f"\n{'='*50}")
    log(f"  Wiki Pipeline: {joined_input[:80]}")
    log(f"  Slug: {slug}" + (f" | Append to: {append_to}" if append_to else ""))
    log(f"{'='*50}")

    # record append: verify base entry exists and is published
    if append_to:
        base = wiki_index.get_entry(DB_PATH, append_to)
        if not base:
            msg = f"append target not found: {append_to}"
            if json_mode: _json_error("ENTRY_NOT_FOUND", msg); return 1
            else: err(f"  ❌ {msg}"); return 1
        if append_to == slug:
            # 自 append（add --append-to <自身>）：cmd_add 已把本条目标记为 pending/running，
            # 所以「状态=done」不再可判——改用「曾发布过」的证据（record.json / DONE 事件）。
            if not _base_is_published(append_to):
                return _append_requires_published(
                    json_mode, append_to, base.get('status', ''), '，且没有已发布记录')
        elif base.get('status') != 'done':
            return _append_requires_published(json_mode, append_to, base.get('status', ''))

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
    accept_manual = getattr(args, 'accept_manual', False)
    raw_dir = paths.raw_dir(slug)
    has_raw = _raw_dir_has_content(slug)
    # 声明来源 = URL 源（local 直接复制、keywords/search 按设计不产生 fetch 记录）
    declared = [c for c in classifications if c.get('input_type') == 'url']
    pending = _missing_declared_sources(raw_dir, declared)
    reuse_existing_raw = (has_raw and not pending and not force_collect)

    if reuse_existing_raw:
        log("\n[2/4] 跳过素材收集（声明来源均已抓取且 raw/ 非空，使用 --force-collect 可强制重跑）")
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
        # 已有素材时（append 补料 / 失败来源重试）新素材落 raw/append_<N>/，绝不覆盖旧材料
        dest_subdir = f"append_{_next_append_index(raw_dir)}" if has_raw else None
        if dest_subdir:
            log(f"  → 新素材落盘 raw/{dest_subdir}/（保留既有材料）")
        max_depth = args.max_depth if getattr(args, 'max_depth', None) is not None else None
        coll_sources = [{
            'input_type': c['input_type'], 'source_type': c['source_type'], 'input': c['input'],
            'type': c.get('type'), 'subtype': c.get('subtype'),
        } for c in classifications]

        local_base = (raw_dir / dest_subdir) if dest_subdir else raw_dir
        for i, c in enumerate(classifications):
            if c.get('source_type') == 'local':
                src_dir = local_base / f"s{i}" if len(classifications) > 1 else local_base
                src_dir.mkdir(parents=True, exist_ok=True)
                _copy_local_source(c['input'], src_dir)

        if len(coll_sources) == 1 and not dest_subdir and coll_sources[0].get('source_type') != 'local':
            c = coll_sources[0]
            collect_args = ["--slug", slug, "--input-type", c['input_type'],
                           "--source-type", c['source_type'], "--input", c['input']]
            if max_depth is not None:
                collect_args += ["--max-depth", str(max_depth)]
            r = run_script("exec/collect_materials.py", collect_args, timeout=180)
        else:
            collect_args = ["--slug", slug, "--sources-json", json.dumps(coll_sources, ensure_ascii=False)]
            if max_depth is not None:
                collect_args += ["--max-depth", str(max_depth)]
            if dest_subdir:
                collect_args += ["--dest-subdir", dest_subdir]
            r = run_script("exec/collect_materials.py", collect_args, timeout=180)

        if r["ok"]:
            log((r["stdout"] or "")[:500])
            missing = _missing_declared_sources(raw_dir, declared)
            if missing and not accept_manual:
                # 声明了来源却没有材料 → 不许静默放行（否则会产出与旧材料等价的记录）
                _log(slug, 'FETCH', {'status': 'failed', 'missing': missing})
                msg = (f"{len(missing)} 个声明来源没有对应材料，已阻止生成提取任务："
                       + "; ".join(f"{m['url']} ({m['status']})" for m in missing)
                       + "。请手动补料后重跑，或加 --accept-manual 接受手工抓取")
                if json_mode:
                    _json_error("MATERIALS_MISSING", msg, detail={'missing': missing})
                else:
                    err(f"  ❌ MATERIALS_MISSING: {msg}")
                    for m in missing:
                        err(f"     - {m['url']} (status={m['status']})")
                return 1
            if missing:
                err(f"  ⚠️ 接受 {len(missing)} 个声明来源的手工抓取（--accept-manual）")
                for m in missing:
                    err(f"     - {m['url']} (status={m['status']})")
                _log(slug, 'FETCH', {'status': 'manual (accepted)', 'missing': missing})
            else:
                _log(slug, 'FETCH', {'status': 'success'})
            wiki_index.upsert_task(DB_PATH, slug, materials_ready=1)
        else:
            err(f"  ⚠️ Collect: {(r['stderr'] or 'unknown error')[:200]}")
            _log(slug, 'FETCH', {'status': 'failed', 'error': (r['stderr'] or 'unknown error')[:200]})
            # 采集器硬失败：与缺料门禁一致，两种输出模式都必须中止（不得静默继续生成提取任务）
            if json_mode:
                _json_error("COLLECT_FAILED", r.get("stderr", "collect failed"))
            else:
                err("  ❌ COLLECT_FAILED: 采集器失败，已中止（未生成提取任务）")
            return 1

    # === Step 4: Generate record extraction task ===
    log(f"\n[3/4] Record 记录提取任务生成...")
    _log(slug, 'GATE', {'materials_ready': True, 'source_type': primary_source_type})

    interp_args = ["--slug", slug, "--source-type", primary_source_type, "--json", "--mode", "record"]
    if append_to:
        interp_args += ["--append-to", append_to]
    r = run_script("exec/generate_task.py", interp_args, timeout=120)
    if not r["ok"]:
        # error 列限 200 字符：保留 traceback 最后一行（异常类型 + 消息），首行只有 "Traceback ..."
        wiki_index.update_status(
            DB_PATH, slug, 'failed',
            error=f"interpret failed: {_last_stderr_line(r.get('stderr'))}"[:ERROR_FIELD_LIMIT])
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

    _log(slug, 'WRITE', {'taskName': task_spec.get('taskName')})

    # === Step 5: spawn spec ===
    publish_cmd = f"{sys.executable} {SCRIPTS_DIR / 'wiki_db.py'} publish --id {slug}"
    spawn_spec = {
        "task": task_spec["task"],
        "taskName": task_spec["taskName"],
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

    if json_mode:
        print(json.dumps(spawn_spec, ensure_ascii=False, indent=2))
        return 0

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


def main():
    parser = argparse.ArgumentParser(description="Wiki 工作流总入口")
    sub = parser.add_subparsers(dest="command", help="命令")

    p_run = sub.add_parser("run", help="执行已 pop 的任务（record 提取）")
    p_run.add_argument("--id", required=True)
    p_run.add_argument("--max-depth", type=int, default=3)
    p_run.add_argument("--force-collect", action="store_true")
    p_run.add_argument("--accept-manual", action="store_true",
                       help="接受手工抓取的缺失来源（LinkedIn / 微信等需登录态场景），不阻断 run")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    # 以下参数仅用于返回明确的废除错误（v3.1）
    p_run.add_argument("--mode", choices=["record", "article"], default=None)
    p_run.add_argument("--depth", choices=["brief", "deep"], default=None)
    p_run.add_argument("--append-to")

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args) or 0)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
