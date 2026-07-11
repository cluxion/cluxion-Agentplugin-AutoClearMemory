"""Recall hot-path batching: one transaction per recall_query and one
engine call per listing (score_memories)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from forgetforge import db, recall


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "db.sqlite")
    yield connection
    connection.close()


def test_recall_query_commits_once_for_many_matches(conn) -> None:
    for index in range(5):
        db.upsert_memory(conn, memory_id=f"m-{index}", content=f"postgres tuning note {index}")
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    results = recall.recall_query(conn, "postgres")
    conn.set_trace_callback(None)
    assert len(results) == 5
    commits = [stmt for stmt in statements if stmt.strip().upper().startswith("COMMIT")]
    assert len(commits) == 1  # one fsync for the whole recall, not one per row


def test_record_retrieval_still_commits_by_default(conn) -> None:
    db.upsert_memory(conn, memory_id="m-solo", content="redis on 6380")
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    recorded = recall.record_retrieval(conn, memory_id="m-solo", layer="explicit")
    conn.set_trace_callback(None)
    assert recorded is not None
    assert any(stmt.strip().upper().startswith("COMMIT") for stmt in statements)


def test_score_memories_matches_per_row_scoring(conn) -> None:
    for index in range(4):
        db.upsert_memory(
            conn,
            memory_id=f"m-{index}",
            content=f"note {index}",
            importance=0.2 * index,
            frequency=0.1 * index,
        )
    rows = db.list_memories(conn, limit=10)
    batched = recall.score_memories(rows)
    assert [entry["memory_id"] for entry in batched] == [row.id for row in rows]
    for row, entry in zip(rows, batched, strict=True):
        single = recall.score_memory(row)
        assert entry["tier"] == single["tier"]
        assert entry["action"] == single["action"]
        assert entry["retention"] == pytest.approx(single["retention"])


def test_score_memories_pins_keep_forever_retention(conn) -> None:
    db.upsert_memory(conn, memory_id="m-keep", content="never forget")
    db.mark_keep_forever(conn, "m-keep")
    rows = [db.get_memory(conn, "m-keep")]
    batched = recall.score_memories(rows)
    assert batched[0]["retention"] == 1.0
    assert batched[0]["action"] == "keep_forever_tag"


def test_score_memories_empty_is_empty() -> None:
    assert recall.score_memories([]) == []


def test_recall_golden_multi_match_stats(conn) -> None:
    """Golden baseline: tiers and stats after multi-match recall match pre-opt behavior."""
    db.upsert_memory(
        conn,
        memory_id="a",
        content="alpha project uses redis cache",
        importance=0.6,
        frequency=0.1,
    )
    db.upsert_memory(
        conn,
        memory_id="b",
        content="beta project uses redis queue",
        importance=0.4,
        frequency=0.0,
    )
    db.upsert_memory(conn, memory_id="c", content="gamma unrelated topic", importance=0.5, frequency=0.2)
    conn.commit()

    results = recall.recall_query(conn, "redis", layer="implicit")
    assert len(results) == 2
    assert {result.memory_id for result in results} == {"a", "b"}

    row_a = db.get_memory(conn, "a")
    row_b = db.get_memory(conn, "b")
    row_c = db.get_memory(conn, "c")
    assert row_a is not None and row_b is not None and row_c is not None

    implicit_boost = 0.35
    implicit_importance_delta = 0.02
    frequency_delta = 0.05
    assert row_a.retrieval_count == pytest.approx(implicit_boost)
    assert row_b.retrieval_count == pytest.approx(implicit_boost)
    assert row_a.tier == "hot"
    assert row_b.tier == "hot"
    assert row_a.importance == pytest.approx(0.6 + implicit_importance_delta)
    assert row_b.importance == pytest.approx(0.4 + implicit_importance_delta)
    assert row_a.frequency == pytest.approx(0.1 + frequency_delta)
    assert row_b.frequency == pytest.approx(0.0 + frequency_delta)
    assert row_c.retrieval_count == 0.0
    assert row_c.tier == "warm_episodic"
    assert row_c.importance == pytest.approx(0.5)
    assert row_c.frequency == pytest.approx(0.2)


def test_record_retrieval_skips_redundant_get_memory(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert_memory(conn, memory_id="solo", content="solo redis tuning note")
    conn.commit()
    calls: list[str] = []
    original_get_memory = db.get_memory

    def counting_get_memory(connection, memory_id: str):
        calls.append(memory_id)
        return original_get_memory(connection, memory_id)

    monkeypatch.setattr(db, "get_memory", counting_get_memory)
    recorded = recall.record_retrieval(conn, memory_id="solo", layer="explicit")
    assert recorded is not None
    assert calls == ["solo"]


def test_two_connections_record_retrieval_no_lost_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent record_retrieval must serialize RMW so both bumps land."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    db_path = tmp_path / "db.sqlite"
    setup = db.connect(db_path)
    db.upsert_memory(
        setup,
        memory_id="shared",
        content="shared concurrent recall target",
        importance=0.5,
        frequency=0.0,
    )
    setup.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            connection = db.connect(db_path)
            barrier.wait(timeout=5)
            recorded = recall.record_retrieval(connection, memory_id="shared", layer="explicit")
            assert recorded is not None
            connection.close()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []

    verify = db.connect(db_path)
    row = db.get_memory(verify, "shared")
    assert row is not None
    events = verify.execute(
        "SELECT COUNT(*) AS c FROM retrieval_events WHERE memory_id = ?",
        ("shared",),
    ).fetchone()["c"]
    assert int(events) == 2
    assert row.retrieval_count == pytest.approx(0.90)
    assert row.importance == pytest.approx(0.56)
    assert row.frequency == pytest.approx(0.10)
    verify.close()


def test_record_retrieval_commit_false_leaves_owned_transaction(conn) -> None:
    db.upsert_memory(conn, memory_id="owned", content="leave txn open for caller")
    conn.commit()
    assert not conn.in_transaction
    recorded = recall.record_retrieval(conn, memory_id="owned", layer="explicit", commit=False)
    assert recorded is not None
    assert conn.in_transaction is True
    conn.commit()


def test_record_retrieval_missing_rolls_back_owned_transaction(conn) -> None:
    assert not conn.in_transaction
    recorded = recall.record_retrieval(conn, memory_id="missing", layer="explicit", commit=False)
    assert recorded is None
    assert conn.in_transaction is False
