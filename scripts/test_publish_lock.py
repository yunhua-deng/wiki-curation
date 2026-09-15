"""test_publish_lock.py — publish 锁契约：持有者元数据 / 过期接管 / 安全释放。"""
import json
import os
import time
from pathlib import Path

import pytest

from scripts.publish import lock as lock_mod
from scripts.publish.lock import LockBusyError, PublishLock, OWNER_FILE

DEAD_PID = 999999999  # 极大概率不存在


def _patch_ws(tmp: Path, monkeypatch):
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


def _lock_dir(tmp: Path) -> Path:
    return tmp / ".publish.lock"


def _write_owner(tmp: Path, **fields):
    d = _lock_dir(tmp)
    d.mkdir(exist_ok=True)
    owner = {"pid": DEAD_PID, "host": lock_mod.socket.gethostname(), "started_at": time.time()}
    owner.update(fields)
    (d / OWNER_FILE).write_text(json.dumps(owner), encoding="utf-8")
    return d


def _age_dir(d: Path, seconds: float):
    old = time.time() - seconds
    os.utime(d, (old, old))


def test_acquire_writes_owner_metadata(tmp_path, monkeypatch):
    _patch_ws(tmp_path, monkeypatch)
    with PublishLock(timeout=1):
        d = _lock_dir(tmp_path)
        assert d.is_dir()
        owner = json.loads((d / OWNER_FILE).read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["host"] == lock_mod.socket.gethostname()
        assert isinstance(owner["started_at"], float)
    # 释放后锁目录整体消失
    assert not _lock_dir(tmp_path).exists()


def test_busy_when_holder_alive(tmp_path, monkeypatch):
    """持有者 pid 存活 → 即使锁龄超过 stale_after 也不接管，报 BUSY 并附带持有者信息。"""
    _patch_ws(tmp_path, monkeypatch)
    _write_owner(tmp_path, pid=os.getpid(), started_at=time.time() - 3600)
    with pytest.raises(LockBusyError) as ei:
        PublishLock(timeout=0.3, poll_interval=0.05, stale_after=600).acquire()
    msg = str(ei.value)
    assert f"pid={os.getpid()}" in msg and "已持有" in msg


def test_busy_when_lock_is_fresh_even_if_pid_dead(tmp_path, monkeypatch):
    """PID 已消失但锁龄未超 TTL → 不接管（避免误抢刚创建但尚未写完元数据的锁）。"""
    _patch_ws(tmp_path, monkeypatch)
    _write_owner(tmp_path, pid=DEAD_PID, started_at=time.time())
    with pytest.raises(LockBusyError):
        PublishLock(timeout=0.2, poll_interval=0.05, stale_after=600).acquire()


def test_reclaim_stale_lock_with_dead_pid(tmp_path, monkeypatch, capsys):
    """崩溃残留锁（pid 已消失 + 超过 TTL）→ 自动接管，不再 BUSY。"""
    _patch_ws(tmp_path, monkeypatch)
    _write_owner(tmp_path, pid=DEAD_PID, started_at=time.time() - 3600)
    with PublishLock(timeout=1, stale_after=600):
        owner = json.loads((_lock_dir(tmp_path) / OWNER_FILE).read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
    assert "接管" in capsys.readouterr().err


def test_reclaim_manual_empty_stale_lock_dir(tmp_path, monkeypatch):
    """手工遗留的空 .publish.lock（无 owner 元数据，mtime 超过 TTL）→ 自动接管。"""
    _patch_ws(tmp_path, monkeypatch)
    d = _lock_dir(tmp_path)
    d.mkdir()
    _age_dir(d, 3600)
    with PublishLock(timeout=1, stale_after=600):
        assert (_lock_dir(tmp_path) / OWNER_FILE).exists()
    assert not _lock_dir(tmp_path).exists()


def test_release_keeps_foreign_lock(tmp_path, monkeypatch):
    """锁被他人接管后，原持有者的 release 不得删掉别人的锁。"""
    _patch_ws(tmp_path, monkeypatch)
    lock = PublishLock(timeout=1)
    lock.acquire()
    _write_owner(tmp_path, pid=DEAD_PID, started_at=time.time())  # 模拟已被接管
    lock.release()
    assert _lock_dir(tmp_path).exists()
    assert json.loads((_lock_dir(tmp_path) / OWNER_FILE).read_text(encoding="utf-8"))["pid"] == DEAD_PID


def test_pid_alive_probe():
    assert lock_mod._pid_alive(os.getpid()) is True
    assert lock_mod._pid_alive(DEAD_PID) is False
    assert lock_mod._pid_alive(0) is False


WORKER_SCRIPT = '''
import time
from scripts.publish.lock import PublishLock

with PublishLock(timeout=30, poll_interval=0.02):
    with open(r"{log}", "a", encoding="utf-8") as fh:
        fh.write("start\\n")
    time.sleep(0.4)
    with open(r"{log}", "a", encoding="utf-8") as fh:
        fh.write("end\\n")
'''


def test_concurrent_publishers_are_serialized(tmp_path):
    """两个进程并发抢锁必须串行（不得双写）：日志只能是 start/end 成对且不交错。"""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    log = tmp_path / "log.txt"
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER_SCRIPT.format(root=str(root), log=str(log)), encoding="utf-8")
    (tmp_path / "wiki").mkdir()
    env = dict(os.environ, WIKI_WORKSPACE=str(tmp_path / "wiki"), PYTHONPATH=str(root))

    procs = [subprocess.Popen([sys.executable, str(worker)], env=env, cwd=str(tmp_path)) for _ in range(2)]
    assert [p.wait(timeout=90) for p in procs] == [0, 0]
    assert log.read_text(encoding="utf-8").split() == ["start", "end", "start", "end"]
