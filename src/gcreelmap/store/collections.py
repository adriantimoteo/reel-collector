import sqlite3
from dataclasses import dataclass
from datetime import datetime

from gcreelmap.domain.slug import new_slug
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.domain.tokens import TokenSource
from gcreelmap.store.db import transaction

_MAX_SLUG_ATTEMPTS = 5


@dataclass(frozen=True)
class Collection:
    id: int
    slug: str
    owner_type: str
    owner_id: int
    name: str
    start_date: str | None
    end_date: str | None
    expires_at: str | None
    tier: str
    state: str
    version: int
    published_version: int
    created_at: str
    expired_at: str | None
    unpublished_at: str | None


def _row_to_collection(row: sqlite3.Row) -> Collection:
    return Collection(
        id=row["id"],
        slug=row["slug"],
        owner_type=row["owner_type"],
        owner_id=row["owner_id"],
        name=row["name"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        expires_at=row["expires_at"],
        tier=row["tier"],
        state=row["state"],
        version=row["version"],
        published_version=row["published_version"],
        created_at=row["created_at"],
        expired_at=row["expired_at"],
        unpublished_at=row["unpublished_at"],
    )


def create_collection(
    conn: sqlite3.Connection,
    *,
    owner_type: str,
    owner_id: int,
    name: str,
    now: datetime,
    tokens: TokenSource,
    expires_at: datetime | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Collection:
    now_text = utc_iso(now)
    expires_text = utc_iso(expires_at) if expires_at is not None else None

    last_error: sqlite3.IntegrityError | None = None
    for _ in range(_MAX_SLUG_ATTEMPTS):
        slug = new_slug(name, tokens)
        try:
            with transaction(conn):
                cur = conn.execute(
                    "INSERT INTO collections "
                    "(slug, owner_type, owner_id, name, start_date, end_date, expires_at, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        slug,
                        owner_type,
                        owner_id,
                        name,
                        start_date,
                        end_date,
                        expires_text,
                        now_text,
                    ),
                )
                collection_id = cur.lastrowid
                if collection_id is None:
                    raise RuntimeError("INSERT did not report a lastrowid")
        except sqlite3.IntegrityError as exc:
            last_error = exc
            continue
        collection = get_collection(conn, collection_id)
        if collection is None:
            raise RuntimeError(f"just-inserted collection {collection_id} is missing")
        return collection

    if last_error is None:
        raise RuntimeError("unreachable: loop exited without success or a recorded error")
    raise last_error


def get_collection(conn: sqlite3.Connection, collection_id: int) -> Collection | None:
    row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
    return _row_to_collection(row) if row is not None else None


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Collection | None:
    row = conn.execute("SELECT * FROM collections WHERE slug = ?", (slug,)).fetchone()
    return _row_to_collection(row) if row is not None else None


def list_collections(conn: sqlite3.Connection) -> list[Collection]:
    rows = conn.execute("SELECT * FROM collections ORDER BY id").fetchall()
    return [_row_to_collection(row) for row in rows]


def bump_version(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute("UPDATE collections SET version = version + 1 WHERE id = ?", (collection_id,))
