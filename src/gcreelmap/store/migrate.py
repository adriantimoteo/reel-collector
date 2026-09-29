import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from gcreelmap.domain.timeutil import utc_iso

_NAME_RE = re.compile(r"^(\d{4})_(.+)\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _load_bundled_migrations() -> list[Migration]:
    package = resources.files("gcreelmap.store.migrations")
    migrations: list[Migration] = []
    for entry in package.iterdir():
        match = _NAME_RE.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        name = match.group(2)
        sql = entry.read_text(encoding="utf-8")
        migrations.append(Migration(version=version, name=name, sql=sql))
    return sorted(migrations, key=lambda m: m.version)


def _validate_versions(migrations: Sequence[Migration]) -> None:
    versions = sorted(m.version for m in migrations)
    if len(versions) != len(set(versions)):
        raise MigrationError("duplicate migration version numbers")
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError(f"migration versions must be contiguous from 1, got {versions}")


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def pending_migrations(
    conn: sqlite3.Connection, migrations: Sequence[Migration] | None = None
) -> list[Migration]:
    resolved = list(migrations) if migrations is not None else _load_bundled_migrations()
    _validate_versions(resolved)
    applied = _applied_versions(conn)
    return sorted((m for m in resolved if m.version not in applied), key=lambda m: m.version)


def _backup_db(conn: sqlite3.Connection, db_path: Path) -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{timestamp}")
    dest = sqlite3.connect(str(backup_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    now_iso = utc_iso(datetime.now(UTC))
    script = (
        "BEGIN;\n" + migration.sql + "\nINSERT INTO schema_migrations (version, applied_at) VALUES "
        f"({migration.version}, '{now_iso}');\n" + "COMMIT;\n"
    )
    try:
        conn.executescript(script)
    except sqlite3.Error as exc:
        conn.rollback()
        raise MigrationError(
            f"migration {migration.version} ({migration.name}) failed: {exc}"
        ) from exc


def migrate(
    conn: sqlite3.Connection,
    *,
    db_path: Path | None,
    backup: bool = True,
    migrations: Sequence[Migration] | None = None,
) -> list[int]:
    resolved = list(migrations) if migrations is not None else _load_bundled_migrations()
    _validate_versions(resolved)

    applied_before = _applied_versions(conn)
    pending = sorted(
        (m for m in resolved if m.version not in applied_before), key=lambda m: m.version
    )
    if not pending:
        return []

    should_backup = (
        backup
        and bool(applied_before)
        and db_path is not None
        and str(db_path) != ":memory:"
        and Path(db_path).exists()
    )
    if should_backup and db_path is not None:
        _backup_db(conn, Path(db_path))

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )

    applied_versions: list[int] = []
    for migration in pending:
        _apply_migration(conn, migration)
        applied_versions.append(migration.version)
    return applied_versions
