from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from forgetforge import cli, pruner
from forgetforge.config import load_config


def test_second_pruner_refuses_when_lock_held(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.home / ".pruner.lock"
    holder = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        code = cli.main(["pruner-daemon", "--once", "--max-cycles", "1"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["ok"] is False
        assert payload["error"] == "pruner_already_running"
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)


def test_pruner_runs_normally_when_lock_free(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    code = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["ok"] is True


def test_pruner_daemon_creates_missing_home_before_lock(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "missing-home"
    monkeypatch.setenv("FORGETFORGE_HOME", str(home))
    code = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["ok"] is True
    assert (home / ".pruner.lock").exists()


def test_pruner_daemon_summary_uses_effective_interval(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    code = pruner.run_pruner_daemon(interval_hours=2, run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["interval_hours"] == 2
