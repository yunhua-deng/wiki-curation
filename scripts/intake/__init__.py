#!/usr/bin/env python3
"""
intake/__init__.py — Wiki Intake 模块公共 helper。

负责把用户输入解析成源列表、逐个分类、选出主类型。
供 wiki_db.py add 与 exec/orchestrate.py run 复用，避免在多个脚本里重复实现同一套逻辑。
"""
from pathlib import Path

from . import classify_source


META_FILE_NAMES = {'source.txt', 'source_info.md', '_drill_log.json', 'prompt.md', '_fetch_results.json'}


def is_local_path(text: str) -> bool:
    """启发式判断字符串是否为本地文件/目录路径。"""
    if not text:
        return False
    p = Path(text)
    return p.exists() and (p.is_file() or p.is_dir())


def resolve_inputs(input_list=None, inputs_file=None) -> list[str]:
    """把 CLI 参数解析成源字符串列表（每个元素是一条 URL 或一个关键词/路径）。"""
    if inputs_file:
        p = Path(inputs_file)
        if not p.exists():
            raise FileNotFoundError(f"--inputs-file not found: {inputs_file}")
        lines = p.read_text(encoding='utf-8').splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith('#')]

    raw_inputs = []
    if input_list:
        if isinstance(input_list, list):
            raw_inputs = input_list
        else:
            raw_inputs = [input_list]

    expanded = []
    for ri in raw_inputs:
        if '\n' in ri:
            expanded.extend([line.strip() for line in ri.splitlines() if line.strip()])
        else:
            expanded.append(ri)

    # 单条字符串中包含多个 URL 时拆分，保留剩余文本作为关键词源
    sources = []
    for text in expanded:
        urls = classify_source.extract_urls(text)
        if len(urls) > 1 or (len(urls) == 1 and urls[0] != text.strip()):
            sources.extend(urls)
            remaining = text
            for url in urls:
                remaining = remaining.replace(url, ' ')
            remaining = ' '.join(remaining.split())
            if remaining:
                sources.append(remaining)
        else:
            sources.append(text)
    return sources


def classify_one(source_text: str) -> dict:
    """对单个源进行分类。返回包含 type/subtype/confidence/input/label_cn 的字典。"""
    if is_local_path(source_text):
        return {
            'type': 'local',
            'input_type': 'local',
            'subtype': 'local_file',
            'source_type': 'local',
            'confidence': 'high',
            'input': source_text,
            'label_cn': '本地文件',
        }
    result = classify_source.classify(source_text)
    if result["status"] == "unclassifiable" or not result.get("results"):
        raise ValueError(f"无法分类输入: {source_text}")
    best = result["results"][0]
    return {
        'type': best.get("type", "url"),
        'input_type': best.get("input_type", best.get("type", "url")),
        'subtype': best.get("subtype", "generic_web"),
        'source_type': best.get("source_type", best.get("subtype", "generic_web")),
        'confidence': best.get("confidence", "medium"),
        'input': best.get("url", best.get("source_text", source_text)),
        'label_cn': best.get("label_cn", best.get("subtype", "")),
    }


def pick_primary_source_type(classifications: list[dict]) -> str:
    """从多源分类结果中选出主 source_type（平台）。"""
    non_local = [c for c in classifications if c.get('source_type') != 'local']
    has_local = len(non_local) < len(classifications)
    if not non_local:
        return 'local'
    source_types = [c.get('source_type') for c in non_local]
    if len(set(source_types)) == 1 and not has_local:
        return source_types[0]
    return 'multi_source'


def prepare_intake(input_list=None, inputs_file=None, source_prompt: str = None) -> dict:
    """
    解析输入、分类、选出主类型，返回 intake 结果字典。

    返回：
        {
            "sources": [原始源字符串列表],
            "classifications": [分类结果列表],
            "primary_input_type": "url|keywords|local",
            "primary_source_type": "arxiv|github|weixin|...",
            "primary_type": "url|keywords|local",          # 旧别名
            "primary_subtype": "arxiv_paper|...",          # 旧别名
            "joined_input": "\\n".join(sources),
            "source_prompt": source_prompt or joined_input,
        }
    """
    sources = resolve_inputs(input_list, inputs_file)
    if not sources:
        raise ValueError("没有解析到任何输入源")

    classifications = []
    for src in sources:
        try:
            c = classify_one(src)
        except ValueError as e:
            # 单个源分类失败时回退为 generic_web，避免阻塞入队
            c = {
                'type': 'url',
                'input_type': 'url',
                'subtype': 'generic_web',
                'source_type': 'webpage',
                'confidence': 'low',
                'input': src,
                'label_cn': '未知网页',
            }
        classifications.append(c)

    primary_source_type = pick_primary_source_type(classifications)
    primary_input_type = 'local' if primary_source_type == 'local' else classifications[0].get('input_type', 'url')
    # 兼容性：保留旧键
    primary_subtype = classifications[0].get('subtype') if primary_source_type != 'multi_source' else 'multi_source'
    primary_type = primary_input_type
    joined_input = '\n'.join(sources)
    return {
        'sources': sources,
        'classifications': classifications,
        'primary_input_type': primary_input_type,
        'primary_source_type': primary_source_type,
        'primary_type': primary_type,
        'primary_subtype': primary_subtype,
        'joined_input': joined_input,
        'source_prompt': source_prompt or joined_input,
    }


if __name__ == "__main__":
    # 简单自测
    r = prepare_intake(input_list=["https://arxiv.org/abs/2605.26112", "https://github.com/user/repo"])
    print(r)
