from pathlib import Path

from forgetforge import db, recall


def test_recall_records_retrieval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    row = db.upsert_memory(conn, memory_id="docker-setup", content="User prefers docker compose v2")
    assert row.retrieval_count == 0.0
    results = recall.recall_query(conn, "docker")
    assert len(results) == 1
    updated = db.get_memory(conn, "docker-setup")
    assert updated is not None
    assert updated.retrieval_count == 0.45
    assert updated.tier == "hot"
    conn.close()


def test_keep_forever_tag(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    db.upsert_memory(conn, memory_id="arch", content="Rust-first architecture")
    db.mark_keep_forever(conn, "arch")
    row = db.get_memory(conn, "arch")
    assert row is not None
    assert row.keep_forever is True
    conn.close()


def test_connect_accepts_str_path(tmp_path: Path, monkeypatch):
    # Regression: a live run passed str and crashed on Path-only handling.
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(str(tmp_path / "db.sqlite"))
    db.upsert_memory(conn, memory_id="s", content="str path works")
    conn.commit()
    assert db.get_memory(conn, "s") is not None
    conn.close()


def test_busy_timeout_is_set(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000
    conn.close()


def test_connect_skips_schema_on_repeat_same_process(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    db_path = tmp_path / "db.sqlite"
    conn1 = db.connect(db_path)
    db.upsert_memory(conn1, memory_id="x", content="repeat connect")
    conn1.close()

    statements: list[str] = []
    conn2 = db.connect(db_path)
    conn2.set_trace_callback(statements.append)
    row = db.get_memory(conn2, "x")
    conn2.set_trace_callback(None)
    assert row is not None
    assert not any("CREATE" in stmt.upper() for stmt in statements)
    conn2.close()


def test_connect_initializes_distinct_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn_a = db.connect(tmp_path / "a.sqlite")
    conn_b = db.connect(tmp_path / "b.sqlite")
    db.upsert_memory(conn_a, memory_id="a", content="db a")
    db.upsert_memory(conn_b, memory_id="b", content="db b")
    assert db.get_memory(conn_a, "a") is not None
    assert db.get_memory(conn_b, "b") is not None
    conn_a.close()
    conn_b.close()
