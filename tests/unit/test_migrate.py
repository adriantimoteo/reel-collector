from pathlib import Path

import pytest

from gcreelmap.store.db import connect
from gcreelmap.store.migrate import Migration, MigrationError, migrate


def test_fresh_db_applies_then_second_call_returns_empty() -> None:
    conn = connect(":memory:")
    assert migrate(conn, db_path=None) == [1]
    assert migrate(conn, db_path=None) == []


def test_schema_migrations_has_one_row() -> None:
    conn = connect(":memory:")
    migrate(conn, db_path=None)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1


def test_non_contiguous_versions_raise() -> None:
    conn = connect(":memory:")
    bad = [
        Migration(version=1, name="a", sql="CREATE TABLE a (id INTEGER);"),
        Migration(version=3, name="b", sql="CREATE TABLE b (id INTEGER);"),
    ]
    with pytest.raises(MigrationError):
        migrate(conn, db_path=None, migrations=bad)


def test_duplicate_versions_raise() -> None:
    conn = connect(":memory:")
    bad = [
        Migration(version=1, name="a", sql="CREATE TABLE a (id INTEGER);"),
        Migration(version=1, name="b", sql="CREATE TABLE b (id INTEGER);"),
    ]
    with pytest.raises(MigrationError):
        migrate(conn, db_path=None, migrations=bad)


def test_failing_migration_rolls_back() -> None:
    conn = connect(":memory:")
    bad = [Migration(version=1, name="broken", sql="CREATE TABLE a (id INTEGER); NOT VALID SQL;")]
    with pytest.raises(MigrationError):
        migrate(conn, db_path=None, migrations=bad)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "a" not in tables
    assert (
        "schema_migrations" not in tables
        or conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    )


def test_backup_created_for_existing_populated_db(tmp_path: Path) -> None:
    path = tmp_path / "gcreelmap.db"
    conn = connect(path)
    migrate(conn, db_path=path)
    conn.execute("INSERT INTO kv (key, value) VALUES ('x', '1')")

    # Rebuild the real bundled migration 1 plus a harmless migration 2.
    from gcreelmap.store.migrate import _load_bundled_migrations

    bundled = _load_bundled_migrations()
    extended = [*bundled, Migration(version=2, name="test", sql="CREATE TABLE t2 (id INTEGER);")]

    applied = migrate(conn, db_path=path, migrations=extended)
    assert applied == [2]

    backups = list(tmp_path.glob("gcreelmap.db.bak-*"))
    assert len(backups) == 1

    backup_conn = connect(backups[0])
    row = backup_conn.execute("SELECT value FROM kv WHERE key='x'").fetchone()
    assert row[0] == "1"
    tables = {
        r[0]
        for r in backup_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "t2" not in tables  # the backup predates migration 2
    backup_conn.close()


def test_no_backup_for_memory_fresh_db_or_backup_false(tmp_path: Path) -> None:
    conn = connect(":memory:")
    migrate(conn, db_path=None)  # in-memory: never backed up

    path = tmp_path / "fresh.db"
    conn2 = connect(path)
    migrate(conn2, db_path=path)  # fresh DB: nothing applied before, no backup
    assert list(tmp_path.glob("*.bak-*")) == []

    from gcreelmap.store.migrate import _load_bundled_migrations

    bundled = _load_bundled_migrations()
    extended = [*bundled, Migration(version=2, name="test", sql="CREATE TABLE t2 (id INTEGER);")]
    migrate(conn2, db_path=path, migrations=extended, backup=False)
    assert list(tmp_path.glob("*.bak-*")) == []
