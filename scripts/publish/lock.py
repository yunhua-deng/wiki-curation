#!/usr/bin/env python3
"""
scripts/publish/lock.py — lightweight cross-process file lock for publish.

Uses atomic mkdir() as the locking primitive, which works on both POSIX and
Windows without extra dependencies. The lock is released by rmdir().

锁目录内写入 `owner.json`（pid / host / started_at）作为持有者元数据：进程被强杀
（`__exit__` 未执行）时锁会残留，超过 `stale_after` 且持有者已不存在时由下一个
publisher 自动接管，避免一把死锁永久阻塞所有 publish。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from contextlib import ContextDecorator
from dataclasses import dataclass

OWNER_FILE = "owner.json"
DEFAULT_STALE_AFTER = 600.0
_WAIT_TIMEOUT = 0x102  # Windows WaitForSingleObject: 对象未就绪 = 进程仍存活


def _pid_alive(pid: int) -> bool:
    """判断 pid 是否仍存活。

    注意：Windows 上 `os.kill(pid, 0)` 不是探活而是**杀进程**
    （TerminateProcess），必须走 OpenProcess/WaitForSingleObject。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            return False  # 已不存在（或无权访问，按已退出处理）
        try:
            return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class LockBusyError(Exception):
    """Raised when the lock cannot be acquired within the requested timeout."""
    pass


@dataclass
class PublishLock(ContextDecorator):
    """A wiki-wide publish lock.

    Usage:
        with PublishLock(timeout=30):
            ...

    The lock directory lives inside the wiki workspace so multiple agents
    working on the same wiki are serialized, while different wikis remain
    independent.

    锁目录内的 `owner.json` 记录持有者（pid / host / started_at）。`acquire()` 遇到已存在
    的锁时：若锁龄超过 `stale_after` 且持有者已不存在（无 owner 元数据时只看锁龄），
    则接管该锁；否则轮询到 `timeout` 后抛 `LockBusyError`，错误信息附带持有者信息，
    便于区分「真的在发布」与「崩溃残留」。
    """
    timeout: float = 30.0
    poll_interval: float = 0.1
    stale_after: float = DEFAULT_STALE_AFTER

    def __post_init__(self):
        from scripts import paths
        self._lock_dir: Path = paths.get_workspace() / ".publish.lock"
        self._owner_path: Path = self._lock_dir / OWNER_FILE
        self._owner: dict | None = None

    # ---------- 持有者元数据 ----------

    def _read_owner(self) -> dict | None:
        try:
            data = json.loads(self._owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_owner(self) -> None:
        self._owner = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": time.time(),
        }
        self._owner_path.write_text(
            json.dumps(self._owner, ensure_ascii=False, indent=2), encoding="utf-8")

    def _owner_is_ours(self, owner: dict | None) -> bool:
        return bool(self._owner and owner
                    and owner.get("pid") == self._owner.get("pid")
                    and owner.get("started_at") == self._owner.get("started_at"))

    def _holder_age(self) -> float:
        """锁龄（秒）：优先 owner 元数据的 started_at，缺元数据时退回锁目录 mtime。"""
        owner = self._read_owner()
        started = owner.get("started_at") if owner else None
        if isinstance(started, (int, float)):
            return max(0.0, time.time() - float(started))
        try:
            return max(0.0, time.time() - self._lock_dir.stat().st_mtime)
        except OSError:
            return 0.0

    def _describe_holder(self) -> str:
        owner = self._read_owner()
        age = self._holder_age()
        if not owner:
            return f"无 owner 元数据（空锁目录，创建于 {age:.0f}s 前）"
        host = owner.get("host") or "?"
        where = "" if host == socket.gethostname() else f" host={host}"
        started = owner.get("started_at")
        when = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(started)))
                if isinstance(started, (int, float)) else "?")
        return f"pid={owner.get('pid')}{where} started_at={when}（已持有 {age:.0f}s）"

    def _reclaim_if_stale(self) -> bool:
        """残留锁接管：锁龄 > stale_after 且持有者已不存在。返回是否已回收。"""
        holder = self._read_owner()
        if self._holder_age() < self.stale_after:
            return False
        pid = holder.get("pid") if holder else None
        host = holder.get("host") if holder else None
        same_host = (not host) or host == socket.gethostname()
        if isinstance(pid, int) and same_host and _pid_alive(pid):
            return False  # 持有者仍活着，不抢（哪怕锁龄偏大）
        desc = self._describe_holder()
        # 竞态保护：删除前复核锁没有被他人接管或刷新
        snapshot = self._read_owner()
        if (snapshot or {}).get("pid") != pid or (snapshot or {}).get("started_at") != (holder or {}).get("started_at"):
            return False
        if self._holder_age() < self.stale_after:
            return False
        try:
            self._owner_path.unlink()
        except (FileNotFoundError, NotADirectoryError):
            pass
        try:
            os.rmdir(self._lock_dir)
        except OSError:
            return False
        print(f"⚠️ 接管残留的 publish 锁：{self._lock_dir}（{desc}）", file=sys.stderr)
        return True

    # ---------- 获取 / 释放 ----------

    def acquire(self) -> "PublishLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.mkdir(self._lock_dir)
                self._write_owner()
                return self
            except FileExistsError:
                if self._reclaim_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise LockBusyError(
                        f"Could not acquire publish lock at {self._lock_dir} "
                        f"within {self.timeout}s; another agent is publishing "
                        f"({self._describe_holder()})."
                    )
                time.sleep(self.poll_interval)

    def release(self) -> None:
        owner = self._read_owner()
        if owner and not self._owner_is_ours(owner):
            return  # 锁已被他人接管，不要动别人的锁
        try:
            self._owner_path.unlink()
        except (FileNotFoundError, NotADirectoryError, OSError):
            pass
        try:
            os.rmdir(self._lock_dir)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"⚠️ 释放 publish 锁失败：{self._lock_dir}（{e}）", file=sys.stderr)

    def __enter__(self) -> "PublishLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
        return False
