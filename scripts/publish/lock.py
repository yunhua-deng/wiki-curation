#!/usr/bin/env python3
"""
scripts/publish/lock.py — lightweight cross-process file lock for publish.

Uses atomic mkdir() as the locking primitive, which works on both POSIX and
Windows without extra dependencies. The lock is released by rmdir().
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from contextlib import ContextDecorator
from dataclasses import dataclass


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
    """
    timeout: float = 30.0
    poll_interval: float = 0.1

    def __post_init__(self):
        from scripts import paths
        self._lock_dir: Path = paths.get_workspace() / ".publish.lock"

    def acquire(self) -> "PublishLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.mkdir(self._lock_dir)
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LockBusyError(
                        f"Could not acquire publish lock at {self._lock_dir} "
                        f"within {self.timeout}s; another agent is publishing."
                    )
                time.sleep(self.poll_interval)

    def release(self) -> None:
        try:
            os.rmdir(self._lock_dir)
        except FileNotFoundError:
            pass

    def __enter__(self) -> "PublishLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
        return False
