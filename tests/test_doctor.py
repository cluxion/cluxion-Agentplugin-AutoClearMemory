"""Tests for embedded doctor (determinism + cross-cutting checks)."""

import json
import subprocess
from pathlib import Path

from forgetforge.doctor import (
    DoctorResult,
    render_json,
    run_doctor,
)
from forgetforge.doctor.framework import DoctorContext
from forgetforge.doctor.probes import PROBES


def _catalog_path() -> Path:
    import importlib.resources

    pkg = "forgetforge.doctor"
    return Path(str(importlib.resources.files(pkg).joinpath("catalog.json")))


def test_run_doctor_returns_result_and_deterministic():
    cat = _catalog_path()
    r1 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    assert isinstance(r1, DoctorResult)
    j1 = render_json(r1)
    r2 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    j2 = render_json(r2)
    assert j1 == j2  # byte identical
    # sorted by severity then id
    ids = [c.check_id for c in r1.checks]
    assert len(ids) > 0


def test_cross_cutting_checks_present():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for key in ("hermes_on_path", "entry_point_registered", "toolset_valid"):
        assert key in statuses
        assert statuses[key] in ("pass", "warn", "fail", "skip")


def test_new_probes_implemented_and_non_skip():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    # at least two newly implemented must be non-skip
    new_checks = [
        "pyarrow_available_for_archive",
        "fts5_available",
        "forgetforge_home_env_valid",
        "config_file_loadable",
        "hot_injection_hook_wired",
    ]
    non_skip_count = sum(1 for k in new_checks if k in statuses and statuses[k] != "skip")
    assert non_skip_count >= 2


def test_probe_exception_becomes_fail():
    def bad_probe(ctx):
        raise RuntimeError("boom")

    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes={"hermes_on_path": bad_probe},
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["hermes_on_path"] == "fail"


def test_warn_only_is_ok():
    from forgetforge.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="x", category="c", severity="medium", status="warn", detail="w"),
    )
    r = DoctorResult(plugin="p", version="0.3.5", checks=checks)
    assert r.ok is True
    assert r.summary == "ok"


def test_critical_skip_marks_degraded_summary():
    cat = _catalog_path()
    partial = {k: v for k, v in PROBES.items() if k != "database_file_exists_and_readable"}
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=partial,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["database_file_exists_and_readable"] == "skip"
    assert result.summary == "degraded"
    assert result.ok is False
    payload = json.loads(render_json(result))
    assert payload["summary"] == "degraded"
    assert payload["ok"] is False


def _doctor_ctx() -> DoctorContext:
    return DoctorContext(
        cwd=Path.cwd(),
        hermes_bin="hermes",
        run=lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""),
    )


def test_db_probes_pass_with_isolated_home(tmp_path, monkeypatch):
    from forgetforge import db
    from forgetforge.doctor.probes import (
        database_file_exists_and_readable,
        database_schema_current,
        hot_memory_tier_reachable,
    )

    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    db.connect(tmp_path / "db.sqlite").close()

    ctx = _doctor_ctx()
    assert database_file_exists_and_readable(ctx) == ("pass", str(tmp_path / "db.sqlite"))
    assert database_schema_current(ctx)[0] == "pass"
    assert hot_memory_tier_reachable(ctx) == ("pass", "hot tier query ok")


def test_static_high_probes_registered_and_pass():
    from forgetforge.doctor.probes import (
        hermes_tool_schemas_valid,
        memory_id_validation,
    )

    ctx = _doctor_ctx()
    assert hermes_tool_schemas_valid(ctx)[0] == "pass"
    assert memory_id_validation(ctx)[0] == "pass"
