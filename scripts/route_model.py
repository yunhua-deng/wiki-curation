#!/usr/bin/env python3
"""
模型路由器（v3.2 record-only）— 记录提取任务的模型选择。

配置优先级：
  1. 环境变量 WIKI_MODEL_RECORD
  2. references/models.yaml 的 record 档
  3. OpenClaw Gateway agents.defaults.model（继承配置）
  4. 硬编码默认值

Usage:
  python route_model.py --tier record [--json]
"""
import json
import os
import sys
import argparse
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_MODEL = "kimi/kimi-for-coding"
DEFAULT_FALLBACK = ["deepseek/deepseek-v4-flash"]


def _load_gateway_model_config() -> dict:
    """Load model config from OpenClaw Gateway (~/.openclaw/openclaw.json)."""
    config_path = Path(
        os.environ.get(
            "OPENCLAW_CONFIG_PATH",
            str(Path.home() / ".openclaw" / "openclaw.json"),
        )
    )
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("agents", {}).get("defaults", {}).get("model", {})
    except (json.JSONDecodeError, KeyError):
        return {}


def _load_yaml_config() -> dict | None:
    """从 skill 的 references/ 加载 models.yaml。"""
    if yaml is None:
        return None
    skill_refs = Path(__file__).resolve().parent.parent / "references" / "models.yaml"
    if not skill_refs.exists():
        return None
    try:
        with open(skill_refs, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[route_model] 读取 {skill_refs} 失败: {e}", file=sys.stderr)
    return None


def select_model(tier: str = "record") -> dict:
    """返回 {model, tier, downgraded, reason, fallback}。"""
    # 1. 环境变量
    env_val = os.environ.get(f"WIKI_MODEL_{tier.upper()}")
    if env_val:
        return {"model": env_val, "tier": tier, "downgraded": False,
                "reason": "", "fallback": DEFAULT_FALLBACK}

    # 2. yaml 配置
    yaml_config = _load_yaml_config()
    if yaml_config:
        tier_cfg = yaml_config.get(tier, {}) or yaml_config.get("record", {})
        if isinstance(tier_cfg, dict) and "primary" in tier_cfg:
            fallback = tier_cfg.get("fallback")
            return {"model": tier_cfg["primary"], "tier": tier,
                    "downgraded": False, "reason": "",
                    "fallback": fallback if isinstance(fallback, list) else DEFAULT_FALLBACK}

    # 3. Gateway 继承
    gateway = _load_gateway_model_config()
    if gateway.get("primary"):
        return {"model": gateway["primary"], "tier": tier,
                "downgraded": False, "reason": "",
                "fallback": gateway.get("fallbacks") or DEFAULT_FALLBACK}

    # 4. 硬编码默认
    return {"model": DEFAULT_MODEL, "tier": tier, "downgraded": False,
            "reason": "", "fallback": DEFAULT_FALLBACK}


def main():
    parser = argparse.ArgumentParser(description="Wiki 模型路由器（record-only）")
    parser.add_argument("--tier", default="record")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = select_model(args.tier)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        fb = ", ".join(result.get("fallback", []))
        print(f"  Model: {result['model']}" + (f", fallback: [{fb}]" if fb else ""))


if __name__ == "__main__":
    main()
