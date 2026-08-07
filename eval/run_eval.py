#!/usr/bin/env python3
"""
skills/wiki-curation/eval/run_eval.py — Local skillgrade-style evaluator.

Usage:
    python eval/run_eval.py --deterministic
    python eval/run_eval.py --llm        # also run LLM-rubric tasks
    python eval/run_eval.py --all
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EVAL_DIR = Path(__file__).resolve().parent
SKILL_ROOT = EVAL_DIR.parent


def _make_workspace() -> Path:
    """Create a temporary wiki workspace with default references and assets."""
    ws = Path(tempfile.mkdtemp(prefix="wiki_eval_"))
    (ws / "data").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_ROOT / "references", ws / "references", dirs_exist_ok=True)
    shutil.copytree(SKILL_ROOT / "assets", ws / "assets", dirs_exist_ok=True)
    return ws


def _run(cmd: list[str], shell_setup: list[str] | None = None,
         setup_files: dict[str, str] | None = None, timeout: int = 120,
         workspace: Path | None = None) -> dict:
    """Run a command and return captured output.

    Files in ``setup_files`` are created relative to the workspace using Python
    so the evaluator works on Windows without relying on shell syntax.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ws = workspace if workspace else SKILL_ROOT / "wiki"
    env["WIKI_WORKSPACE"] = str(ws)

    if setup_files:
        for rel_path, content in setup_files.items():
            dest = ws / rel_path
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            except Exception as e:
                return {
                    "ok": False,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"setup file failed ({rel_path}): {e}",
                }

    if shell_setup:
        for step in shell_setup:
            r = subprocess.run(step, shell=True, cwd=ws, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                return {
                    "ok": False,
                    "exit_code": r.returncode,
                    "stdout": r.stdout,
                    "stderr": f"setup failed ({step}): {r.stderr}",
                }

    return _run_raw(cmd, env=env, timeout=timeout)


def _run_raw(cmd: list[str], env: dict, timeout: int) -> dict:
    try:
        r = subprocess.run(cmd, cwd=SKILL_ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "exit_code": -1,
                "stdout": e.stdout or "", "stderr": f"timeout after {timeout}s"}
    return {
        "ok": r.returncode == 0,
        "exit_code": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
    }


def _get_path(obj, path: str):
    """Naive dot-path getter; supports `data.commands` and `data.total`."""
    parts = path.split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _check(result: dict, check: dict) -> tuple[bool, str]:
    """Evaluate a single deterministic check."""
    ctype = check.get("type")
    name = check.get("name", ctype)

    if ctype == "exit_code":
        expected = check.get("value")
        if result["exit_code"] != expected:
            return False, f"{name}: expected exit_code {expected}, got {result['exit_code']}"
        return True, f"{name}: exit_code {expected}"

    if ctype == "stdout_contains":
        expected = check.get("value", "")
        if expected not in result["stdout"]:
            return False, f"{name}: stdout missing {expected!r}"
        return True, f"{name}: stdout contains {expected!r}"

    if ctype == "json_path_exists":
        path = check.get("path", "")
        try:
            data = json.loads(result["stdout"])
        except json.JSONDecodeError as e:
            return False, f"{name}: stdout is not valid JSON: {e}"
        if _get_path(data, path) is None:
            return False, f"{name}: path {path} missing"
        return True, f"{name}: path {path} exists"

    if ctype == "json_path":
        path = check.get("path", "")
        expected = check.get("value")
        regex = check.get("regex")
        try:
            data = json.loads(result["stdout"])
        except json.JSONDecodeError as e:
            return False, f"{name}: stdout is not valid JSON: {e}"
        actual = _get_path(data, path)
        if regex is not None:
            if actual is None or not re.search(regex, str(actual)):
                return False, f"{name}: {path}={actual!r} does not match {regex!r}"
            return True, f"{name}: {path}={actual!r} matches {regex!r}"
        if actual != expected:
            return False, f"{name}: {path} expected {expected!r}, got {actual!r}"
        return True, f"{name}: {path} = {actual!r}"

    if ctype == "json_contains":
        path = check.get("path", "")
        item = check.get("item")
        key = check.get("key")
        try:
            data = json.loads(result["stdout"])
        except json.JSONDecodeError as e:
            return False, f"{name}: stdout is not valid JSON: {e}"
        collection = _get_path(data, path)
        if not isinstance(collection, list):
            return False, f"{name}: {path} is not a list"
        found = any((c.get(key) if isinstance(c, dict) else c) == item for c in collection)
        if not found:
            return False, f"{name}: {path} does not contain {key}={item!r}"
        return True, f"{name}: {path} contains {key}={item!r}"

    return False, f"{name}: unknown check type {ctype}"


def _llm_score(prompt: str, model: str | None = None) -> dict:
    """Score a rubric prompt using an LLM if credentials are available.

    Provider selection:
      1. Explicit model prefix (e.g. ``kimi/kimi-k2.7-code``,
         ``deepseek/deepseek-v4-flash``).
      2. Environment keys in priority order: DeepSeek, Kimi, Anthropic, OpenAI.
    """
    keys = {
        "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
        "kimi": os.environ.get("KIMI_API_KEY") or os.environ.get("KIMI_CODE_API_KEY"),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
    }

    model = os.environ.get("WIKI_EVAL_MODEL") or model
    provider: str | None = None

    if model and "/" in model:
        prefix = model.split("/", 1)[0].lower()
        if prefix in keys:
            provider = prefix

    if provider is None:
        for name, key in keys.items():
            if key:
                provider = name
                break

    if provider is None:
        return {
            "ok": False,
            "error": "No LLM API key found (DEEPSEEK_API_KEY, KIMI_API_KEY/KIMI_CODE_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY)",
        }

    api_key = keys[provider]
    if not api_key:
        return {"ok": False, "error": f"Model prefix '{provider}' requested but no matching API key found"}

    if provider == "deepseek":
        return _llm_deepseek(prompt, model or "deepseek/deepseek-v4-flash", api_key)
    if provider == "kimi":
        return _llm_kimi(prompt, model or "kimi/kimi-for-coding", api_key)
    if provider == "anthropic":
        return _llm_anthropic(prompt, model or "claude-sonnet-4-6", api_key)
    if provider == "openai":
        return _llm_openai(prompt, model or "gpt-4o", api_key)

    return {"ok": False, "error": f"Unknown provider {provider}"}


def _llm_deepseek(prompt: str, model: str, api_key: str) -> dict:
    """Call DeepSeek Chat Completions API (OpenAI-compatible)."""
    import urllib.request
    import urllib.error

    # DeepSeek API expects model names like "deepseek-v4-flash"
    body_model = model.removeprefix("deepseek/") if model.startswith("deepseek/") else model

    body = {
        "model": body_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return {"ok": True, "content": content}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"DeepSeek API error {e.code}: {e.read().decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}


def _llm_kimi(prompt: str, model: str, api_key: str) -> dict:
    """Call Kimi Code API (OpenAI-compatible coding endpoint).

    Endpoint docs: https://api.kimi.com/coding/v1/chat/completions
    Kimi Code keys typically start with ``sk-kimi-`` and require a recognized
    coding-agent User-Agent.

    For the Kimi Coding Plan subscription, use model ``kimi-for-coding``.
    """
    import urllib.request
    import urllib.error

    # Kimi Code expects model names like "kimi-k2.7-code"
    body_model = model.removeprefix("kimi/") if model.startswith("kimi/") else model

    body = {
        "model": body_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": 1024,
    }
    req = urllib.request.Request(
        "https://api.kimi.com/coding/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "user-agent": "claude-code/0.1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return {"ok": True, "content": content}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Kimi API error {e.code}: {e.read().decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}


def _llm_anthropic(prompt: str, model: str, api_key: str) -> dict:
    import urllib.request
    import urllib.error

    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["content"][0]["text"]
        return {"ok": True, "content": content}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Anthropic API error {e.code}: {e.read().decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}


def _llm_openai(prompt: str, model: str, api_key: str) -> dict:
    import urllib.request
    import urllib.error

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return {"ok": True, "content": content}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"OpenAI API error {e.code}: {e.read().decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}


def _extract_score(content: str, max_score: int) -> int | None:
    """Try to extract an integer score from LLM response text."""
    # Look for patterns like "Score: 3/4" or "Score: 3" or "3/4"
    m = re.search(r"(?:score|得分)\s*[:=]?\s*(\d+)(?:\s*/\s*\d+)?", content, re.I)
    if m:
        return int(m.group(1))
    # Fallback: count explicit PASS/YES items up to max_score
    passes = len(re.findall(r"\b(PASS|YES|✅)\b", content, re.I))
    if passes > 0:
        return min(passes, max_score)
    return None


def _build_llm_prompt(task: dict, output: dict) -> str:
    rubric = "\n".join(f"{i+1}. {item}" for i, item in enumerate(task["rubric"]))
    prompt = f"""You are evaluating the output of a wiki-curation skill task.

Task: {task.get('name', task['id'])}
Description: {task.get('description', '')}

Command output:
stdout:
{output['stdout'][:4000]}

stderr:
{output['stderr'][:500]}

Rubric:
{rubric}

For each rubric item, respond with PASS or FAIL and a one-sentence reason.
At the end, provide a total score in the exact format "Score: X/{len(task['rubric'])}".
"""
    return prompt


def run_task(task: dict, run_llm: bool, workspace: Path) -> dict:
    """Run one eval task and return pass/fail details."""
    tid = task["id"]
    ttype = task.get("type", "deterministic")
    print(f"\n▶ {tid} — {task.get('name', tid)} ({ttype})")

    cmd = [str(c) for c in task["command"]]
    result = _run(
        cmd,
        shell_setup=task.get("setup"),
        setup_files=task.get("setup_files"),
        timeout=task.get("timeout", 120),
        workspace=workspace,
    )

    details = {
        "id": tid,
        "type": ttype,
        "ok": False,
        "checks": [],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
    }

    if ttype == "deterministic":
        all_ok = True
        for check in task.get("checks", []):
            ok, msg = _check(result, check)
            details["checks"].append({"name": check.get("name"), "ok": ok, "message": msg})
            if not ok:
                all_ok = False
                print(f"  ❌ {msg}")
            else:
                print(f"  ✅ {msg}")
        details["ok"] = all_ok
        return details

    if ttype == "llm-rubric":
        scoring = task.get("scoring", {})
        max_score = scoring.get("max_score", len(task.get("rubric", [])))
        passing = scoring.get("passing_score", max_score)

        if not run_llm:
            details["ok"] = None
            details["checks"].append({"name": "llm", "ok": None,
                                      "message": "skipped: pass --llm to run LLM-rubric tasks"})
            print("  ⏭️  skipped (pass --llm to enable)")
            return details

        if not result["ok"]:
            details["checks"].append({"name": "command", "ok": False,
                                      "message": f"command failed: {result['stderr']}"})
            print(f"  ❌ command failed: {result['stderr']}")
            return details

        prompt = _build_llm_prompt(task, result)
        llm = _llm_score(prompt)
        if not llm["ok"]:
            details["checks"].append({"name": "llm", "ok": False, "message": llm["error"]})
            print(f"  ❌ {llm['error']}")
            return details

        score = _extract_score(llm["content"], max_score)
        if score is None:
            details["checks"].append({"name": "llm", "ok": False,
                                      "message": f"could not parse score from: {llm['content'][:200]}"})
            print(f"  ❌ could not parse LLM score")
            return details

        ok = score >= passing
        details["ok"] = ok
        details["checks"].append({
            "name": "llm-rubric",
            "ok": ok,
            "message": f"score {score}/{max_score} (passing {passing})",
        })
        print(f"  {'✅' if ok else '❌'} score {score}/{max_score}")
        return details

    details["checks"].append({"name": "type", "ok": False, "message": f"unknown task type {ttype}"})
    return details


def main():
    parser = argparse.ArgumentParser(description="wiki-curation skill evaluator")
    parser.add_argument("--deterministic", action="store_true",
                        help="Run only deterministic graders")
    parser.add_argument("--llm", action="store_true",
                        help="Run LLM-rubric graders (requires API key)")
    parser.add_argument("--all", action="store_true",
                        help="Run deterministic + LLM-rubric graders")
    args = parser.parse_args()

    run_llm = args.llm or args.all
    deterministic_only = args.deterministic and not run_llm

    eval_path = EVAL_DIR / "eval.yaml"
    if not eval_path.exists():
        print(f"eval.yaml not found at {eval_path}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    tasks = spec.get("tasks", [])

    workspace = _make_workspace()
    try:
        results = []
        for task in tasks:
            if deterministic_only and task.get("type") == "llm-rubric":
                print(f"\n▶ {task['id']} — {task.get('name', task['id'])} (llm-rubric) skipped (--deterministic)")
                results.append({"id": task["id"], "type": "llm-rubric", "ok": None,
                                "checks": [{"name": "skip", "ok": None, "message": "--deterministic"}]})
                continue
            results.append(run_task(task, run_llm, workspace))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.get("ok") is True)
    failed = sum(1 for r in results if r.get("ok") is False)
    skipped = sum(1 for r in results if r.get("ok") is None)

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped / {total} total")
    if failed:
        print("Failed tasks:")
        for r in results:
            if r.get("ok") is False:
                print(f"  - {r['id']}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
