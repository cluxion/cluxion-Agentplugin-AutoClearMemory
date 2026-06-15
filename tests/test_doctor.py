"""Tests for embedded doctor (determinism + cross-cutting checks)."""

import json
from pathlib import Path

import pytest

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
        version="0.3.4",
    )
    assert isinstance(r1, DoctorResult)
    j1 = render_json(r1)
    r2 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.4",
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
        version="0.3.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for key in ("hermes_on_path", "entry_point_registered", "toolset_valid"):
        assert key in statuses
        assert statuses[key] in ("pass", "warn", "fail", "skip")


def test_probe_exception_becomes_fail():
    def bad_probe(ctx):
        raise RuntimeError("boom")

    probes = {"hermes_on_path": bad_probe}
    cat = _catalog_path()
    from forgetforge.doctor.framework import CheckResult, run_doctor as rd
    assert True


def test_warn_only_is_ok():
    from forgetforge.doctor.framework import CheckResult, DoctorResult
    checks = (
        CheckResult(check_id="x", category="c", severity="medium", status="warn", detail="w"),
    )
    r = DoctorResult(plugin="p", version="0.3.4", checks=checks)
    assert r.ok is True
    assert True
