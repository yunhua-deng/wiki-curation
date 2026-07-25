"""Tests for lib.run_cmd."""
from unittest import mock

from scripts import lib


def test_run_cmd_success(monkeypatch):
    monkeypatch.setattr(
        lib.subprocess, "run",
        lambda *a, **k: mock.MagicMock(returncode=0, stdout="ok", stderr=""),
    )
    r = lib.run_cmd(["echo", "hello"])
    assert r["ok"]
    assert r["exit_code"] == 0
    assert r["stdout"] == "ok"


def test_run_cmd_failure(monkeypatch):
    monkeypatch.setattr(
        lib.subprocess, "run",
        lambda *a, **k: mock.MagicMock(returncode=1, stdout="", stderr="boom"),
    )
    r = lib.run_cmd(["false"])
    assert not r["ok"]
    assert r["exit_code"] == 1
    assert "boom" in r["stderr"]


def test_run_cmd_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise lib.subprocess.TimeoutExpired("cmd", 1)
    monkeypatch.setattr(lib.subprocess, "run", raise_timeout)
    r = lib.run_cmd(["sleep", "10"], timeout=1)
    assert not r["ok"]
    assert r["exit_code"] == -1
    assert r["stderr"] == "timeout"


def test_run_cmd_exception(monkeypatch):
    def raise_oserror(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(lib.subprocess, "run", raise_oserror)
    r = lib.run_cmd(["cmd"])
    assert not r["ok"]
    assert r["exit_code"] == -2
    assert "nope" in r["stderr"]


def test_run_cmd_string_cmd(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lib.subprocess, "run", fake_run)
    lib.run_cmd('echo "hello world"')
    assert len(calls) == 1
    assert isinstance(calls[0], list)


def test_run_cmd_retries_once_by_default(monkeypatch):
    side_effects = [
        lib.subprocess.TimeoutExpired("cmd", 1),
        mock.MagicMock(returncode=0, stdout="ok", stderr=""),
    ]
    monkeypatch.setattr(lib.subprocess, "run", lambda *a, **k: side_effects.pop(0))
    monkeypatch.setattr(lib.time, "sleep", lambda x: None)
    r = lib.run_cmd(["cmd"])
    assert r["ok"]
    assert len(side_effects) == 0
