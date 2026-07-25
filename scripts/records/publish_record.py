#!/usr/bin/env python3
"""
scripts/records/publish_record.py — 记录发布主逻辑（publish 的默认路径）。

流程对齐 publish/commands._do_publish：
存在性重试 → 工作流事件 → 确定性校验 → fetched 回填 →
links/relations/entities 入库 → entry 更新 → metadata/index/site 刷新。

全程纯本地确定性操作，不做网络请求（URL 验证归 verify-links 命令）。
"""
import json
import sys
import time
from pathlib import Path

from scripts import paths
from scripts import wiki_index
from scripts.records import links as L
from scripts.records import relations as REL
from scripts.records import schema as RS
from scripts.records.schema import normalize_url, RECORD_VERSION
# v3.2: metadata.json generation removed — record.json is single source of truth
from scripts.wiki_index.schema import normalize_topic_type

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI_CMD = f"{sys.executable} {SKILL_SCRIPTS_DIR / 'cli.py'}"


def _out_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _fail(args, db_path, entry_id, error, message, detail=None, next_cmd=None):
    wiki_index.update_status(db_path, entry_id, "failed", error=message)
    if args.json:
        out = {"ok": False, "error": error, "message": message}
        if detail:
            out["detail"] = detail
        if next_cmd:
            out["next_cmd"] = next_cmd
        _out_json(out)
    else:
        print(f"❌ {message}")
        if detail:
            print(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
    sys.exit(1)


def _canonicalize_entities(entities: dict) -> dict:
    """用 entity_aliases.yaml 归一化实体名（系统做，不赌 agent 自觉）。"""
    try:
        from scripts.site.entities import load_aliases
        aliases = load_aliases()
    except Exception:
        aliases = {}
    # 构建 variant→canonical 查找表
    variant_map = {}
    for canonical, variants in (aliases.get("terms") or {}).items():
        for v in [canonical] + (variants or []):
            variant_map[str(v).strip().lower()] = canonical
    for _etype, ent_map in (aliases.get("entities") or {}).items():
        for canonical, variants in (ent_map or {}).items():
            for v in [canonical] + (variants or []):
                variant_map[str(v).strip().lower()] = canonical

    result = {}
    for bucket in ("company", "author", "product", "series"):
        vals = entities.get(bucket) or []
        seen = []
        for v in vals:
            c = variant_map.get(str(v).strip().lower(), str(v).strip())
            if c and c not in seen:
                seen.append(c)
        result[bucket] = seen
    return result


def _collect_fetched_urls(slug, wiki_dir) -> set:
    """从 raw/ 的 _drill_log.json 与 _fetch_results.json 收集已成功抓取的 URL（归一化）。"""
    urls = set()
    raw_dir = paths.raw_dir(slug, wiki_dir)

    drill_log = raw_dir / "_drill_log.json"
    if drill_log.exists():
        try:
            log = json.loads(drill_log.read_text(encoding="utf-8", errors="replace"))
            for level in log.get("levels", []):
                for entry in level.get("entries", []):
                    if entry.get("status") == "success" and entry.get("input"):
                        urls.add(normalize_url(entry["input"]))
        except Exception:
            pass

    for fetch_results in raw_dir.rglob("_fetch_results.json"):
        try:
            data = json.loads(fetch_results.read_text(encoding="utf-8", errors="replace"))
            for r in data.get("results", []):
                if r.get("status") == "success" and r.get("url"):
                    urls.add(normalize_url(r["url"]))
        except Exception:
            pass

    return urls


def publish_record(args, db_path, wiki_dir, scripts_dir):
    """记录发布：校验 record.json → 入库 → 织边 → 刷新索引/站点。"""
    entry_id = args.id
    ws = paths.get_workspace()
    rec_path = paths.record_path(entry_id, ws)

    # 子 agent 落盘可能略有延迟；指数退避后再判缺失
    for delay in (0.5, 1.0, 1.5):
        if rec_path.exists():
            break
        time.sleep(delay)

    if not rec_path.exists():
        _fail(args, db_path, entry_id, "FILE_MISSING",
              f"output file missing: record.json (expected at {rec_path})",
              next_cmd=f"{CLI_CMD} run --id {entry_id}")

    # === 工作流事件：补 WRITE、记 VERIFY（与文章路径同构） ===
    try:
        events = wiki_index.get_events(db_path, slug=entry_id)
        actions = {e["action"] for e in events}
    except Exception:
        actions = set()
    workflow_warnings = []
    if "WRITE" not in actions:
        wiki_index.record_event(db_path, entry_id, "WRITE",
                                {"file": "record.json", "mode": "record", "auto": True})
        workflow_warnings.append("自动补录 WRITE 事件")
    for action in ["ENQUEUE", "FETCH", "GATE"]:
        if action not in actions:
            workflow_warnings.append(f"缺少 {action} 事件")
    wiki_index.record_event(db_path, entry_id, "VERIFY", {"file": "record.json", "mode": "record"})

    # === 确定性校验 ===
    record = RS.load_record(entry_id, ws)
    ok, errors = RS.validate_record(record or {})
    if not ok:
        _fail(args, db_path, entry_id, "VERIFY_FAILED",
              f"verify failed: record.json validation errors",
              detail=errors, next_cmd=f"{CLI_CMD} run --id {entry_id}")

    # === fetched 回填（对照 raw 抓取结果，同步回写 record.json） ===
    fetched_urls = _collect_fetched_urls(entry_id, ws)
    for link in record["links"]:
        link["fetched"] = 1 if normalize_url(link.get("url", "")) in fetched_urls else 0
        link.setdefault("verified", None)
    RS.save_record(entry_id, ws, record)

    # === 入库：links / entities / relations（relations 依赖前两者，必须最后织） ===
    n_links = L.replace_links(db_path, entry_id, record["links"])
    # v3.1：实体 canonical 化（系统做，不依赖 agent 自觉）
    canonical_entities = _canonicalize_entities(record["entities"])
    L.set_entry_entities(db_path, entry_id, canonical_entities)
    n_relations = REL.rewire_relations(db_path, entry_id)

    # === entry 更新 ===
    entry = wiki_index.get_entry(db_path, entry_id) or {}
    update_kwargs = {
        "title": record["title"],
        "overview": record["tldr"],
        "tags": record["tags"],
        "topic_type": normalize_topic_type(record.get("topic_type"), entry.get("source_type")),
        "status": "done",
        "spec_version": RECORD_VERSION,
    }
    if record.get("date"):
        update_kwargs["date"] = record["date"]
    wiki_index.upsert_task(db_path, entry_id, **update_kwargs)
    wiki_index.record_event(db_path, entry_id, "DONE",
                            {"file": "record.json", "mode": "record",
                             "links": n_links, "relations": n_relations})

    # === 站点（失败不阻塞） ===
    try:
        from scripts.publish.commands import _refresh_site
        _refresh_site(db_path, wiki_dir, json_mode=args.json)
    except Exception as e:
        if not args.json:
            print(f"  ⚠️ Index refresh skipped: {e}")

    if args.json:
        result = {"ok": True, "id": entry_id, "mode": "record", "status": "done",
                  "links": n_links, "relations": n_relations,
                  "record": paths.record_rel(entry_id)}
        if workflow_warnings:
            result["warnings"] = workflow_warnings
        _out_json(result)
    else:
        print(f"✅ Record published: {paths.record_rel(entry_id)} "
              f"(links={n_links}, relations={n_relations})")
        for w in workflow_warnings:
            print(f"  ⚠️ 流程告警: {w}")
