from __future__ import annotations

import json

import pytest

from forgetforge import cli, pruner


def test_init_rejects_unknown_agents() -> None:
    with pytest.raises(ValueError) as exc:
        cli._parse_agents("nonsense")
    assert str(exc.value) == "unknown agent(s): nonsense"


def test_pruner_daemon_rejects_zero_max_cycles() -> None:
    with pytest.raises(ValueError):
        pruner.run_pruner_daemon(run_once=False, max_cycles=0)


def test_pruner_daemon_once_emits_json_summary(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    out = capsys.readouterr().out.strip()
    summary = json.loads(out)
    assert summary["ok"] is True
    assert summary["cycle"] == 1
    assert "duration_ms" in summary
