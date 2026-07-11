"""Pruner batch demotion: one sqlite transaction, one archive pass per run."""

from __future__ import annotations

import json
from pathlib import Path
from stat import S_IMODE

import pytest

from forgetforge import archive, db, pruner, recall, store
from forgetforge.config import load_config


def _isolated_conn(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    assert str(cfg.db_path).startswith(str(tmp_path))
    return db.connect(cfg.db_path), cfg


def test_pruner_batch_demotes_and_archives(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    for i in range(5):
        store.store_memory(
            conn,
            memory_id=f"mem-{i}",
            content=f"memory body {i}\nsecond line",
            importance=0.1,
        )
    # New memories start cold (recall-centric design); simulate a prior warm
    # state so the pruner has transitions to apply.
    conn.execute("UPDATE memories SET tier = 'warm_episodic'")
    conn.commit()

    result = pruner.run_pruner(conn, config=cfg)

    assert result["ok"] is True
    assert sorted(result["demoted_to_cold"]) == [f"mem-{i}" for i in range(5)]
    for i in range(5):
        row = db.get_memory(conn, f"mem-{i}")
        assert row is not None and row.tier == "cold"
        assert (cfg.archive_dir / f"mem-{i}.txt").exists()
    jsonl_lines = (cfg.archive_dir / "cold_archive.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl_lines) == 5
    assert {json.loads(line)["memory_id"] for line in jsonl_lines} == {f"mem-{i}" for i in range(5)}
    parquet_files = list(cfg.archive_dir.glob("cold_*.parquet"))
    try:
        import pyarrow.parquet as pq
    except ImportError:
        assert parquet_files == []
    else:
        assert len(parquet_files) == 1
        table = pq.read_table(parquet_files[0])
        assert table.num_rows == 5
    conn.close()


def test_pruner_noop_writes_nothing(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="solo", content="already cold", importance=0.1)
    result = pruner.run_pruner(conn, config=cfg)
    assert result["demoted_to_cold"] == []
    assert not (cfg.archive_dir / "cold_archive.jsonl").exists()
    assert list(cfg.archive_dir.glob("*.parquet")) == []
    conn.close()


def test_pruner_daemon_stops_at_max_cycles(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    sleeps: list[int] = []
    monkeypatch.setattr(pruner.time, "sleep", sleeps.append)

    pruner.run_pruner_daemon(interval_hours=1, max_cycles=2)

    assert sleeps == [3600]


def test_write_cold_archive_single_still_works(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)
    result = archive.write_cold_archive(cfg, memory_id="legacy", content="legacy body", retention=0.2, tier="cold")
    assert result["format"] in {"parquet", "jsonl"}
    paths = [cfg.archive_dir / "legacy.txt", cfg.archive_dir / "cold_archive.jsonl"]
    if result["parquet"]:
        paths.append(Path(result["parquet"]))
    for path in paths:
        assert path.exists()
        assert S_IMODE(path.stat().st_mode) == 0o600


def test_write_cold_archive_sanitizes_memory_id_filename(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)

    archive.write_cold_archive_batch(
        cfg,
        [{"memory_id": "a/b", "content": "body", "retention": 0.2, "tier": "cold"}],
    )

    assert (cfg.archive_dir / "a_b.txt").exists()


def test_write_cold_archive_batch_uses_unique_parquet_paths(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)
    real_datetime = archive.datetime

    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            return real_datetime(2026, 1, 1, tzinfo=tz)

    monkeypatch.setattr(archive, "datetime", FixedDateTime)
    first = archive.write_cold_archive_batch(
        cfg,
        [{"memory_id": "first", "content": "first body", "retention": 0.1, "tier": "cold"}],
    )
    second = archive.write_cold_archive_batch(
        cfg,
        [{"memory_id": "second", "content": "second body", "retention": 0.2, "tier": "cold"}],
    )
    if first["format"] == "jsonl" or second["format"] == "jsonl":
        return
    assert first["parquet"] != second["parquet"]
    assert Path(first["parquet"]).exists()
    assert Path(second["parquet"]).exists()


def test_write_cold_archive_batch_empty_is_noop(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)
    result = archive.write_cold_archive_batch(cfg, [])
    assert result == {"format": "noop", "parquet": None, "jsonl": None, "count": 0}
    assert not cfg.archive_dir.exists() or not any(cfg.archive_dir.iterdir())


def test_pruner_bounds_retrieval_events(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="evt", content="retrieval event pruning target memory")
    for _ in range(5):
        recall.recall_query(conn, "retrieval")
    old_ts = "2000-01-01T00:00:00+00:00"
    conn.execute("UPDATE retrieval_events SET created_at = ?", (old_ts,))
    conn.commit()
    assert db.memory_stats(conn)["retrieval_events"] == 5

    cfg = cfg.__class__(
        **{
            **cfg.__dict__,
            "retrieval_events_max_age_days": 30,
            "retrieval_events_max_per_memory": 2,
        }
    )
    result = pruner.run_pruner(conn, config=cfg)

    assert result["retrieval_events_gc"]["deleted_by_age"] >= 1
    assert db.memory_stats(conn)["retrieval_events"] <= 2
    row = db.get_memory(conn, "evt")
    assert row is not None
    assert row.retrieval_count > 0
    conn.close()


def test_prune_hard_deletes_expired_rows(tmp_path: Path, monkeypatch):
    # the scheduled `forgetforge prune` must sweep TTL'd rows (session archives
    # stored with --expire-days), not only demote by retention
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="ttl", content="expiring session archive", node_type="session", expire_days=1)
    conn.execute("UPDATE memories SET expire_at = 1 WHERE id = 'ttl'")
    conn.commit()
    result = pruner.run_pruner(conn, config=cfg)
    assert result["ttl_swept"] == 1
    assert db.get_memory(conn, "ttl") is None
    conn.close()


def _tier_update(new_tier: str, row) -> db.MemoryTierUpdate:
    return (
        new_tier,
        row.id,
        row.tier,
        row.retrieval_count,
        row.importance,
        row.frequency,
        row.is_procedural,
        row.keep_forever,
        row.forget_requested,
        row.last_recall_at,
        row.updated_at,
        row.content,
    )


def test_update_memory_tiers_batch(tmp_path: Path, monkeypatch):
    conn, _ = _isolated_conn(tmp_path, monkeypatch)
    for i in range(3):
        store.store_memory(conn, memory_id=f"t-{i}", content=f"tier test {i}", importance=0.5)
    rows = {row.id: row for row in (db.get_memory(conn, f"t-{i}") for i in range(3))}
    applied = db.update_memory_tiers(
        conn,
        [_tier_update("hot", rows["t-0"]), _tier_update("warm_semantic", rows["t-1"])],
    )
    assert applied == ["t-0", "t-1"]
    assert db.get_memory(conn, "t-0").tier == "hot"
    assert db.get_memory(conn, "t-1").tier == "warm_semantic"
    assert db.get_memory(conn, "t-2").tier != "hot"
    # CAS miss: expected snapshot no longer matches after prior update.
    assert db.update_memory_tiers(conn, [_tier_update("cold", rows["t-0"])]) == []
    assert db.get_memory(conn, "t-0").tier == "hot"
    assert db.update_memory_tiers(conn, []) == []
    conn.close()


def test_pruner_archive_failure_leaves_tiers_then_retries(tmp_path: Path, monkeypatch):
    """Demotions must not commit until the cold archive batch succeeds."""
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    for i in range(3):
        store.store_memory(conn, memory_id=f"mem-{i}", content=f"memory body {i}", importance=0.1)
    conn.execute("UPDATE memories SET tier = 'warm_episodic'")
    conn.commit()

    calls = {"n": 0}
    real_batch = archive.write_cold_archive_batch

    def flaky_batch(config, records):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated archive failure")
        return real_batch(config, records)

    monkeypatch.setattr(archive, "write_cold_archive_batch", flaky_batch)

    with pytest.raises(OSError, match="simulated archive failure"):
        pruner.run_pruner(conn, config=cfg)

    for i in range(3):
        row = db.get_memory(conn, f"mem-{i}")
        assert row is not None and row.tier == "warm_episodic"
    assert not (cfg.archive_dir / "cold_archive.jsonl").exists()

    result = pruner.run_pruner(conn, config=cfg)
    assert result["ok"] is True
    assert sorted(result["demoted_to_cold"]) == [f"mem-{i}" for i in range(3)]
    for i in range(3):
        row = db.get_memory(conn, f"mem-{i}")
        assert row is not None and row.tier == "cold"
        assert (cfg.archive_dir / f"mem-{i}.txt").exists()
    conn.close()


def test_pruner_cas_skips_when_concurrent_recall_during_archive(tmp_path: Path, monkeypatch):
    """Recall on a second connection during archive must not be overwritten by stale demotion."""
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="live", content="concurrent archive race target", importance=0.1)
    store.store_memory(conn, memory_id="idle", content="untouched sibling memory", importance=0.1)
    conn.execute(
        "UPDATE memories SET tier = 'warm_episodic', retrieval_count = 0.0, "
        "updated_at = '2020-01-01T00:00:00+00:00', last_recall_at = NULL"
    )
    conn.commit()

    real_batch = archive.write_cold_archive_batch

    def archive_with_second_connection_recall(config, records):
        other = db.connect(cfg.db_path)
        try:
            hits = recall.recall_query(other, "concurrent archive race")
            assert [h.memory_id for h in hits] == ["live"]
        finally:
            other.close()
        return real_batch(config, records)

    monkeypatch.setattr(archive, "write_cold_archive_batch", archive_with_second_connection_recall)

    result = pruner.run_pruner(conn, config=cfg)

    live = db.get_memory(conn, "live")
    idle = db.get_memory(conn, "idle")
    assert live is not None and idle is not None
    # Concurrent recall landed; stale demotion must CAS-miss (no count reset / no cold).
    assert db.memory_stats(conn)["retrieval_events"] == 1
    assert live.retrieval_count > 0
    assert live.tier != "cold"
    assert "live" not in result["demoted_to_cold"]
    # Unchanged sibling still demotes via CAS hit.
    assert idle.tier == "cold"
    assert "idle" in result["demoted_to_cold"]
    # Cold archive for the later-skipped live transition is an accepted stale backup.
    assert (cfg.archive_dir / "live.txt").exists()
    conn.close()


_FIXED_TS = "2020-01-01T00:00:00+00:00"


def _seed_warm_for_demotion(conn, memory_id: str, content: str) -> None:
    """Warm, never-recalled, low-importance row with a fixed updated_at for CAS races."""
    store.store_memory(conn, memory_id=memory_id, content=content, importance=0.1)
    conn.execute(
        """
        UPDATE memories SET
            tier = 'warm_episodic',
            retrieval_count = 0.0,
            importance = 0.1,
            frequency = 0.0,
            is_procedural = 0,
            keep_forever = 0,
            forget_requested = 0,
            last_recall_at = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (_FIXED_TS, memory_id),
    )
    conn.commit()


def test_pruner_cas_skips_same_second_pin_tier_unchanged(tmp_path: Path, monkeypatch):
    """keep_forever=1 mid-archive with identical tier/updated_at must CAS-miss (not demote pin)."""
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    _seed_warm_for_demotion(conn, "pin", "same-second pin race target")
    _seed_warm_for_demotion(conn, "idle", "untouched sibling for pin race")
    real_batch = archive.write_cold_archive_batch

    def pin_during_archive(config, records):
        other = db.connect(cfg.db_path)
        try:
            # Deliberately leave tier at warm_episodic (not warm_semantic) and
            # force the same updated_at so only keep_forever differs from snapshot.
            other.execute(
                "UPDATE memories SET keep_forever = 1, tier = 'warm_episodic', updated_at = ? WHERE id = 'pin'",
                (_FIXED_TS,),
            )
            other.commit()
        finally:
            other.close()
        return real_batch(config, records)

    monkeypatch.setattr(archive, "write_cold_archive_batch", pin_during_archive)
    result = pruner.run_pruner(conn, config=cfg)

    pin = db.get_memory(conn, "pin")
    idle = db.get_memory(conn, "idle")
    assert pin is not None and idle is not None
    assert pin.keep_forever is True
    assert pin.tier == "warm_episodic"
    assert pin.updated_at == _FIXED_TS
    assert "pin" not in result["demoted_to_cold"]
    assert "pin" not in result["promoted_from_cold"]
    assert idle.tier == "cold"
    assert "idle" in result["demoted_to_cold"]
    conn.close()


def test_pruner_cas_skips_same_second_score_input_mutation(tmp_path: Path, monkeypatch):
    """Score inputs changing with identical tier/count/updated_at must CAS-miss stale demotion."""
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    _seed_warm_for_demotion(conn, "score", "score-input mutation race target")
    _seed_warm_for_demotion(conn, "idle", "untouched sibling for score race")
    real_batch = archive.write_cold_archive_batch

    def mutate_score_inputs_during_archive(config, records):
        other = db.connect(cfg.db_path)
        try:
            # Keep tier, retrieval_count, updated_at identical — only score inputs change.
            other.execute(
                """
                UPDATE memories SET
                    importance = 0.99,
                    frequency = 0.8,
                    is_procedural = 1,
                    last_recall_at = ?,
                    tier = 'warm_episodic',
                    retrieval_count = 0.0,
                    updated_at = ?
                WHERE id = 'score'
                """,
                (_FIXED_TS, _FIXED_TS),
            )
            other.commit()
        finally:
            other.close()
        return real_batch(config, records)

    monkeypatch.setattr(archive, "write_cold_archive_batch", mutate_score_inputs_during_archive)
    result = pruner.run_pruner(conn, config=cfg)

    score = db.get_memory(conn, "score")
    idle = db.get_memory(conn, "idle")
    assert score is not None and idle is not None
    assert score.tier == "warm_episodic"
    assert score.updated_at == _FIXED_TS
    assert score.importance == 0.99
    assert score.frequency == 0.8
    assert score.is_procedural is True
    assert score.last_recall_at == _FIXED_TS
    assert score.retrieval_count == 0.0
    assert "score" not in result["demoted_to_cold"]
    assert "score" not in result["promoted_from_cold"]
    assert idle.tier == "cold"
    assert "idle" in result["demoted_to_cold"]
    conn.close()


def test_pruner_cas_skips_content_only_mutation(tmp_path: Path, monkeypatch):
    """Content-only change with identical updated_at: stale archive ok, DB row must not go cold."""
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    old_content = "content-only race original body"
    new_content = "content-only race rewritten body"
    _seed_warm_for_demotion(conn, "body", old_content)
    _seed_warm_for_demotion(conn, "idle", "untouched sibling for content race")
    real_batch = archive.write_cold_archive_batch

    def rewrite_content_during_archive(config, records):
        other = db.connect(cfg.db_path)
        try:
            other.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = 'body'",
                (new_content, _FIXED_TS),
            )
            other.commit()
        finally:
            other.close()
        return real_batch(config, records)

    monkeypatch.setattr(archive, "write_cold_archive_batch", rewrite_content_during_archive)
    result = pruner.run_pruner(conn, config=cfg)

    body = db.get_memory(conn, "body")
    idle = db.get_memory(conn, "idle")
    assert body is not None and idle is not None
    assert body.content == new_content
    assert body.tier == "warm_episodic"
    assert body.updated_at == _FIXED_TS
    assert "body" not in result["demoted_to_cold"]
    assert "body" not in result["promoted_from_cold"]
    assert idle.tier == "cold"
    assert "idle" in result["demoted_to_cold"]
    # Stale archive of pre-mutation content is accepted; DB row must not transition.
    assert (cfg.archive_dir / "body.txt").exists()
    assert old_content in (cfg.archive_dir / "body.txt").read_text(encoding="utf-8")
    conn.close()
