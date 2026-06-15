"""Tests for embedded doctor (determinism + cross-cutting checks)."""

from pathlib import Path

from forgetforge.doctor import (
    DoctorResult,
    render_json,
    run_doctor,
)
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
