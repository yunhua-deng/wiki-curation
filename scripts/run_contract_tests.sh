#!/usr/bin/env bash
# scripts/run_contract_tests.sh — wiki-curation 契约测试入口
# 用法：在 skills/wiki-curation/ 目录下执行 ./scripts/run_contract_tests.sh

set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SKILL_ROOT"

export PYTHONIOENCODING=utf-8

echo "==> Clearing stale __pycache__ ..."
find "$SKILL_ROOT" -type d -name "__pycache__" -exec rm -rf {} +

echo "==> Running pytest ..."
python -m pytest scripts/ -q

echo "==> Running deterministic eval ..."
python eval/run_eval.py --deterministic

echo "==> Contract tests passed."
