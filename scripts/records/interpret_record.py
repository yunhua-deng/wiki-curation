#!/usr/bin/env python3
"""
scripts/records/interpret_record.py — 记录提取任务（record task）生成器。

与 exec/interpret.py（文章任务）平级：读 raw/ 材料清单 + references/record_schema.json
约束，产出供提取 agent 执行的 portable task payload（envelope 与文章任务对齐）。

提取 agent 的唯一产物：wiki/artifacts/{slug}/record.json
"""
import argparse
import json
import sys
from pathlib import Path

from scripts import paths
from scripts import source_config as sc
from scripts.records import schema as record_schema

MAX_RAW_LIST = 30


def _summarize_raw_files(raw_dir: Path, slug: str, max_entries: int = MAX_RAW_LIST) -> tuple[list[str], str, bool]:
    """返回 raw 文件列表、分类摘要（与 interpret.py 同款截断策略）与 agent_notes 标记。"""
    has_agent_notes = (raw_dir / "agent_notes.md").is_file()
    files = []
    if raw_dir.exists():
        for f in sorted(raw_dir.rglob("*")):
            if f.is_file() and f.name != "_drill_log.json":
                size_kb = f.stat().st_size // 1024
                rel = f.relative_to(raw_dir)
                files.append((rel, size_kb))

    lines = []
    for rel, size_kb in files[:max_entries]:
        rel_posix = str(rel).replace("\\", "/")
        lines.append(f"  {paths.raw_rel(slug)}{rel_posix} ({size_kb}KB)")
    if len(files) > max_entries:
        total_size = sum(sz for _, sz in files)
        lines.append(f"  ... 还有 {len(files) - max_entries} 个文件，总计 {total_size}KB")

    summary = {}
    for rel, _ in files:
        if has_agent_notes and rel.name == "agent_notes.md":
            continue  # agent_notes 单独标注，不计入常规后缀统计
        ext = Path(rel).suffix.lower() or "(无后缀)"
        summary[ext] = summary.get(ext, 0) + 1
    summary_str = ", ".join(f"{cnt}×{ext}" for ext, cnt in sorted(summary.items(), key=lambda x: -x[1])[:6])
    if has_agent_notes:
        summary_str = f"{summary_str}, 1×.agent_notes" if summary_str else "1×.agent_notes"
    return lines, summary_str, has_agent_notes


def _read_drill_targets(raw_dir: Path) -> list[str]:
    """从 _drill_log.json 读出已抓取的关联目标，作为 explicit 候选提示。"""
    log_path = raw_dir / "_drill_log.json"
    if not log_path.exists():
        return []
    try:
        log = json.loads(log_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    urls = []
    for level in log.get("levels", []):
        for entry in level.get("entries", []):
            if entry.get("input"):
                urls.append(entry["input"])
            for target in entry.get("drill_targets", []) or []:
                if target.get("input"):
                    urls.append(target["input"])
    seen = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


def build_record_task(slug: str, source_type: str, raw_files: list[str], raw_summary: str,
                      drill_urls: list[str], append_to: str = None, has_agent_notes: bool = False) -> str:
    """构造提取 agent 的 task prompt。"""
    c = record_schema._load_constraints()
    record_out = str(paths.record_path(slug).resolve())
    type_label = sc.get_label_cn(source_type)
    drill_block = "\n".join(f"- {u}" for u in drill_urls[:20]) if drill_urls else "- （无）"
    append_section = ""
    if append_to:
        base_record_path = paths.record_path(append_to)
        if base_record_path.exists():
            try:
                base = json.loads(base_record_path.read_text(encoding="utf-8", errors="replace"))
                rev = base.get("revision", 0)
                append_section = f"""
## Append 模式：基于已有记录合并

已有 record.json：`{base_record_path}`（revision={rev}）。请基于已有记录 + 新材料做合并更新：

1. **不动字段**：id、date（老条目整体时间不变）、original_source
2. **合并字段**：links 使用已有 links + 新材料关联（去重、保持 same_kind canonical 唯一）；tags/entities 同样合并去重
3. **更新字段**：title 可按新材料扩展；tldr 重新提炼
4. **revision**：设为 `{rev + 1}`
5. **history**：追加一条 `{{"revision": {rev + 1}, "date": "today", "note": "append", "added_sources": [原文需要填入]}}`
"""
            except Exception:
                append_section = "\n## Append 模式\n（无法读取已有 record.json，请首先生成全新记录）\n"

    return f"""你是知识记录提取 agent。从 raw/ 材料中提取一份**结构化知识记录**，写入唯一输出文件：
`{record_out}`
{append_section}
输入：{paths.raw_rel(slug)}（{type_label} 材料）

## 输出契约（record.json，schema v{record_schema.RECORD_VERSION}）

必须写入一个合法 JSON 文件（不要 Markdown 代码块），字段如下：

```json
{{
  "version": "{record_schema.RECORD_VERSION}",
  "id": "{slug}",
  "title": "内容标题",
  "date": "YYYY-MM-DD（无法确定留空字符串）",
  "topic_type": "paper|project|tool|company|institution|researcher|concept|whitepaper|best_practice|comparison|trend|article|observation|product 之一",
  "tldr": "一句话总结（≤80字，客观陈述，不评价）",
  "summary": "解读摘要（400-800 字，2-5 个短段落，每段以 **小标题** 开头再接内容，如 **核心要点**：…。第 1 段一句话点题（这是什么）；中间段核心要点（方法/数据/结果）；末段背景与意义或待验证处。客观陈述，不评价。"
  "tags": ["3-5 个关键词"],
  "entities": {{"company": [], "author": [], "product": [], "series": []}},
  "links": [
    {{"url": "https://...", "kind": "github|arxiv|huggingface|homepage|weixin|linkedin|docs|other",
      "role": "canonical|related", "origin": "explicit|inferred", "fetched": null, "verified": null}}
  ],
  "source": {{"input_type": "url|keywords|local", "source_type": "{source_type}",
             "direct_source": "用户给出的入口 URL", "original_source": "最原始出处 URL"}}
}}
```

## 硬性规则

1. **禁止编造**：所有字段必须来自 raw/ 材料中的真实信息；无法确定的字段留空字符串/空数组。
2. **links 双通道嗅探**（本任务的核心价值）：
   - `explicit`：URL 显式出现在材料正文中（HTML 链接、README 引用、文中裸 URL）。
   - `inferred`：材料只提到了名字（项目名/论文标题/模型名）而没给链接，你通过搜索工具解析出的规范 URL。解析不了就不要写，严禁猜测式拼接 URL。
   - `role`：主题本体对应的官方入口标 `canonical`（每个 kind 至多一个，如该论文的 arXiv 页、该项目的 GitHub 仓库）；其余标 `related`。
   - `fetched` / `verified` 一律填 null，由系统回填。
   - links 允许为空数组（实在解不出关联链接也合法）。
3. **entities 归一化**：公司/作者/产品名优先对齐 `skills/wiki-curation/references/entity_aliases.yaml` 中的 canonical 写法；四个桶必须齐全（可为空数组）。
4. **topic_type** 必须是上面枚举之一，按内容主题判断（不是按来源平台）。
5. **tldr** 一句话，≤80 字；禁止评价、对比、个人观点（不写"我认为/优于/最强"）。
6. **summary** 400-800 字；每段以 `**小标题**` 开头（如 **核心要点**、**关键数据**、**背景与意义**），小标题后接该段内容；客观陈述，不评价。
7. **tags** 3-5 个，能用于后续检索与趋势聚合。

## 已抓取的关联目标（explicit 候选，供参考）

{drill_block}

## 执行约束

- 你的职责仅限于读取 raw/ 材料并写入上面指定的 record.json 一个文件。
- **禁止执行任何 git 操作**（git add/commit/push 等）。
- **不要运行 publish / verify 命令**；不要修改 wiki.db 或 wiki/site/。
- 完成后只返回简洁完成摘要（提取到的链接数、实体数即可）。

## raw/ 材料清单

{raw_summary}

{chr(10).join(raw_files) if raw_files else "  (raw/ 目录为空或不存在)"}

{_agent_notes_section(has_agent_notes)}
"""


def _agent_notes_section(has_agent_notes: bool) -> str:
    """当 agent_notes.md 存在时，在 task prompt 末尾追加参考说明。"""
    if not has_agent_notes:
        return ""
    return (
        """
## 补充参考：agent_notes.md

`agent_notes.md` 是主 agent 在阅读 raw 材料后编写的初步解读笔记，包含分析角度、结构化对比和关联定位。
**建议同时阅读以辅助分析**，但 record.json 中的事实信息仍需以 raw 材料正文为唯一依据，
agent_notes.md 仅作思路参考，禁止直接复制其表述。
"""
    )


def generate_record_task(slug: str, source_type: str, append_to: str = None) -> dict:
    """生成 record 提取任务 envelope（与文章任务 envelope 字段对齐 + mode=record）。"""
    raw_dir = paths.raw_dir(slug)
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw/ 目录不存在: {raw_dir}")

    raw_files, raw_summary, has_agent_notes = _summarize_raw_files(raw_dir, slug)
    drill_urls = _read_drill_targets(raw_dir)
    task = build_record_task(slug, source_type, raw_files, raw_summary, drill_urls, append_to=append_to, has_agent_notes=has_agent_notes)

    return {
        "task": task,
        "taskName": f"record-{slug}",
        "mode": "run",
        "task_mode": "record",
        "cleanup": "keep",
        "context": "isolated",
        "slug": slug,
        "wiki_type": source_type,
        "type_label": sc.get_label_cn(source_type),
        "raw_files": raw_files,
        "output_path": str(paths.record_path(slug).resolve()),
        "depth": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Record 提取任务生成器")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--source-type", "--type", dest="source_type", default="paper")
    parser.add_argument("--append-to")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate_record_task(args.slug, args.source_type, append_to=args.append_to)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"  📝 Record 任务: {result['slug']}")
        print(f"  输出: {result['output_path']}")
        print(f"\n  --- Task ---")
        print(result["task"])


if __name__ == "__main__":
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
