from pathlib import Path

import pytest

from gcreelmap.store.db import connect, transaction


def test_transaction_commits_on_success() -> None:
    conn = connect(":memory:")
    conn.execute("CREATE TABLE t (v INTEGER)")
    with transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_transaction_rolls_back_on_exception() -> None:
    conn = connect(":memory:")
    conn.execute("CREATE TABLE t (v INTEGER)")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_transaction_refuses_to_nest() -> None:
    conn = connect(":memory:")
    with transaction(conn), pytest.raises(RuntimeError), transaction(conn):
        pass


def test_foreign_keys_pragma_on() -> None:
    conn = connect(":memory:")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_enabled_for_file_db(tmp_path: Path) -> None:
    path = tmp_path / "wal.db"
    conn = connect(path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
