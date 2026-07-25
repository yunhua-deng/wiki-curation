#!/usr/bin/env pwsh
# scripts/run_contract_tests.ps1 — wiki-curation 契约测试入口
# 用法：在 skills/wiki-curation/ 目录下执行 .\scripts\run_contract_tests.ps1

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
Set-Location $skillRoot

$env:PYTHONIOENCODING = "utf-8"

Write-Host "==> Clearing stale __pycache__ ..." -ForegroundColor Cyan
Get-ChildItem -Path $skillRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "==> Running pytest ..." -ForegroundColor Cyan
python -m pytest scripts/ -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

Write-Host "==> Running deterministic eval ..." -ForegroundColor Cyan
python eval/run_eval.py --deterministic
if ($LASTEXITCODE -ne 0) { throw "deterministic eval failed" }

Write-Host "==> Contract tests passed." -ForegroundColor Green
