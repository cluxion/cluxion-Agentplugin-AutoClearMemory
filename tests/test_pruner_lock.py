from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from forgetforge import pruner
from forgetforge.config import load_config


def test_second_pruner_refuses_when_lock_held(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.home / ".pruner.lock"
    holder = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        pruner.run_pruner_daemon(run_once=True, max_cycles=1)
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is False
        assert payload["error"] == "pruner_already_running"
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)


def test_pruner_runs_normally_when_lock_free(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
