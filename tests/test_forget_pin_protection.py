from __future__ import annotations

from pathlib import Path

import pytest

from forgetforge import db

PINNED_CONTENT = "User prefers docker compose v2 — pinned forever"
NORMAL_CONTENT = "postgres port is 5433"


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    connection = db.connect(tmp_path / "db.sqlite")
    yield connection
    connection.close()


def test_forget_refuses_keep_forever_memory(conn) -> None:
    db.upsert_memory(conn, memory_id="pinned", content=PINNED_CONTENT)
    db.mark_keep_forever(conn, "pinned")

    result = db.mark_forget(conn, "pinned")
    assert result["ok"] is False
    assert result["reason"] == "kept memory cannot be forgotten"

    row = db.get_memory(conn, "pinned")
    assert row is not None
    assert row.forget_requested is False
    assert row.keep_forever is True
    assert db.search_memories(conn, "docker")[0].id == "pinned"
    assert db.list_memories(conn)[0].id == "pinned"


def test_forget_still_soft_flags_normal_memory(conn) -> None:
    db.upsert_memory(conn, memory_id="normal", content=NORMAL_CONTENT)

    result = db.mark_forget(conn, "normal")
    assert result["ok"] is True

    row = db.get_memory(conn, "normal")
    assert row is not None
    assert row.forget_requested is True
    assert row.tier == "cold"
    assert row.content == NORMAL_CONTENT
    assert db.list_memories(conn) == []
    assert db.search_memories(conn, "postgres") == []


def test_forget_force_can_override_keep_forever(conn) -> None:
    db.upsert_memory(conn, memory_id="pinned", content=PINNED_CONTENT)
    db.mark_keep_forever(conn, "pinned")

    result = db.mark_forget(conn, "pinned", force=True)
    assert result["ok"] is True

    row = db.get_memory(conn, "pinned")
    assert row is not None
    assert row.forget_requested is True
    assert row.content == PINNED_CONTENT


def test_unforget_restores_reachability_and_content(conn) -> None:
    db.upsert_memory(conn, memory_id="recover", content=NORMAL_CONTENT)
    db.mark_forget(conn, "recover")

    result = db.unforget(conn, "recover")
    assert result["ok"] is True
    assert "tier" in result

    row = db.get_memory(conn, "recover")
    assert row is not None
    assert row.forget_requested is False
    assert row.content == NORMAL_CONTENT
    assert db.search_memories(conn, "postgres")[0].content == NORMAL_CONTENT
    assert db.list_memories(conn)[0].id == "recover"


def test_unforget_restores_keep_forever_to_warm_semantic(conn) -> None:
    db.upsert_memory(conn, memory_id="pinned", content=PINNED_CONTENT)
    db.mark_keep_forever(conn, "pinned")
    db.mark_forget(conn, "pinned", force=True)

    result = db.unforget(conn, "pinned")
    assert result["ok"] is True
    assert result["tier"] == "warm_semantic"

    row = db.get_memory(conn, "pinned")
    assert row is not None
    assert row.forget_requested is False
    assert row.keep_forever is True
    assert row.content == PINNED_CONTENT
    assert db.search_memories(conn, "docker")[0].id == "pinned"


def test_list_forgotten_memories(conn) -> None:
    db.upsert_memory(conn, memory_id="gone", content="temporary note")
    db.mark_forget(conn, "gone")

    forgotten = db.list_forgotten_memories(conn)
    assert len(forgotten) == 1
    assert forgotten[0].id == "gone"
    assert forgotten[0].content == "temporary note"