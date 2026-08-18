#!/usr/bin/env python3
"""
lib.py — Wiki 工作流公共工具函数。

提供统一的子进程调用接口，供 orchestrate.py、collect_materials.py、
generate_task.py 等脚本使用。
"""
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from scripts import paths


def slugify_name(name: str) -> str:
    """实体名 → 目录 slug（小写、空格转连字符；CJK 保留）。"""
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9一-鿿_-]", "", s)
    return (s or "topic").strip("-_")[:48]
def get_workspace(fallback_script_path: str = None) -> Path:
    """解析 wiki 工作区根目录。"""
    return paths.get_workspace(fallback_script_path)


def run_cmd(cmd, timeout=120, retries=1, backoff=2.0, cwd=None, env=None) -> dict:
    """统一执行外部命令。

    参数:
        cmd: 命令，可以是 list[str] 或 str。
             如果是 str，使用 shlex.split 拆分（保留引号内内容）。
        timeout: 单次执行超时（秒）。
        retries: 失败/超时后的重试次数（默认 1）。
        backoff: 退避基数（秒），第 n 次重试等待 backoff * 2^n 秒。
        cwd: 执行命令的工作目录。
        env: 额外环境变量（会合并到 os.environ 中）。

    返回:
        {"ok": bool, "stdout": str, "stderr": str, "exit_code": int}
        超时返回 exit_code=-1，其他异常返回 exit_code=-2。
    """
    base_env = os.environ.copy()
    base_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        base_env.update(env)

    if isinstance(cmd, str):
        cmd = shlex.split(cmd, posix=False)

    # Windows: resolve .cmd/.bat/.exe via shutil.which (handles PATHEXT)
    if os.name == "nt" and isinstance(cmd, list) and len(cmd) > 0:
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd[0] = resolved

    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "env": base_env,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd

    last_stderr = ""
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(cmd, **kwargs)
            if r.returncode == 0:
                return {
                    "ok": True,
                    "stdout": r.stdout,
                    "stderr": r.stderr,
                    "exit_code": r.returncode,
                }
            last_stderr = r.stderr
            # 非零退出码，若还有重试机会则继续
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            return {
                "ok": False,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            last_stderr = "timeout"
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            return {
                "ok": False,
                "stdout": "",
                "stderr": "timeout",
                "exit_code": -1,
            }
        except Exception as e:
            last_stderr = str(e)
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "exit_code": -2,
            }


def headless_write_runner(prompt: str, ws, timeout: int = 900) -> dict:
    """headless `claude -p` 执行写作任务（acceptEdits；工作目录=wiki 工作区）。

    供 entities LLM 摘要等"agent 写文件"场景共用。
    prompt 应约束 agent 只读材料、只写目标文件；其他需授权操作在 headless 下自动拒绝。
    """
    import subprocess
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "stderr": "claude CLI not found"}
    cmd = [exe, "-p", prompt, "--permission-mode", "acceptEdits"]
    env = os.environ.copy()
    env["WIKI_WORKSPACE"] = str(ws)
    try:
        r = subprocess.run(cmd, cwd=str(ws), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout or "",
                "stderr": r.stderr or "", "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stderr": f"timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}
