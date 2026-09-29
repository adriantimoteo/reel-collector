import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from gcreelmap.domain.failure import MAX_ATTEMPTS, ErrorCode, is_transient
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.store.db import transaction


@dataclass(frozen=True)
class Reel:
    id: int
    collection_id: int
    canonical_url: str
    platform: str
    status: str
    attempts: int
    error_code: str | None
    error_detail: str | None
    author: str | None
    submitted_by: int | None
    source_message_id: int | None
    submitted_at: str
    claimed_at: str | None
    processed_at: str | None
    notified_at: str | None


def _row_to_reel(row: sqlite3.Row) -> Reel:
    return Reel(
        id=row["id"],
        collection_id=row["collection_id"],
        canonical_url=row["canonical_url"],
        platform=row["platform"],
        status=row["status"],
        attempts=row["attempts"],
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        author=row["author"],
        submitted_by=row["submitted_by"],
        source_message_id=row["source_message_id"],
        submitted_at=row["submitted_at"],
        claimed_at=row["claimed_at"],
        processed_at=row["processed_at"],
        notified_at=row["notified_at"],
    )


def enqueue_reel(
    conn: sqlite3.Connection,
    *,
    collection_id: int,
    canonical_url: str,
    platform: str,
    submitted_by: int | None,
    source_message_id: int | None,
    now: datetime,
) -> tuple[int, bool]:
    existing = conn.execute(
        "SELECT id FROM reels WHERE collection_id = ? AND canonical_url = ?",
        (collection_id, canonical_url),
    ).fetchone()
    if existing is not None:
        return existing[0], False

    try:
        cur = conn.execute(
            "INSERT INTO reels "
            "(collection_id, canonical_url, platform, submitted_by, source_message_id, "
            "submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (collection_id, canonical_url, platform, submitted_by, source_message_id, utc_iso(now)),
        )
    except sqlite3.IntegrityError as exc:
        row = conn.execute(
            "SELECT id FROM reels WHERE collection_id = ? AND canonical_url = ?",
            (collection_id, canonical_url),
        ).fetchone()
        if row is None:
            raise RuntimeError("IntegrityError but no conflicting row found") from exc
        return row[0], False

    if cur.lastrowid is None:
        raise RuntimeError("INSERT did not report a lastrowid")
    return cur.lastrowid, True


def claim_next_reel(
    conn: sqlite3.Connection, *, now: datetime, collection_id: int | None = None
) -> Reel | None:
    with transaction(conn):
        query = "SELECT id FROM reels WHERE status = 'queued'"
        params: list[object] = []
        if collection_id is not None:
            query += " AND collection_id = ?"
            params.append(collection_id)
        query += " ORDER BY submitted_at, id LIMIT 1"
        row = conn.execute(query, params).fetchone()
        if row is None:
            return None
        reel_id = row[0]
        conn.execute(
            "UPDATE reels SET status = 'processing', claimed_at = ?, attempts = attempts + 1 "
            "WHERE id = ?",
            (utc_iso(now), reel_id),
        )
    return get_reel(conn, reel_id)


def mark_done(conn: sqlite3.Connection, reel_id: int, *, author: str | None, now: datetime) -> None:
    conn.execute(
        "UPDATE reels SET status = 'done', author = ?, processed_at = ? WHERE id = ?",
        (author, utc_iso(now), reel_id),
    )


def mark_failed(
    conn: sqlite3.Connection,
    reel_id: int,
    *,
    code: ErrorCode,
    detail: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE reels SET status = 'failed', error_code = ?, error_detail = ?, processed_at = ? "
        "WHERE id = ?",
        (code.value, detail, utc_iso(now), reel_id),
    )


def requeue(conn: sqlite3.Connection, reel_id: int, *, code: ErrorCode, detail: str | None) -> None:
    conn.execute(
        "UPDATE reels SET status = 'queued', error_code = ?, error_detail = ? WHERE id = ?",
        (code.value, detail, reel_id),
    )


def requeue_stale(conn: sqlite3.Connection, *, older_than: timedelta, now: datetime) -> int:
    cutoff = utc_iso(now - older_than)
    with transaction(conn):
        cur = conn.execute(
            "UPDATE reels SET status = 'queued' WHERE status = 'processing' AND claimed_at < ?",
            (cutoff,),
        )
        return cur.rowcount


def get_reel(conn: sqlite3.Connection, reel_id: int) -> Reel | None:
    row = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    return _row_to_reel(row) if row is not None else None


def list_reels(conn: sqlite3.Connection, collection_id: int) -> list[Reel]:
    rows = conn.execute(
        "SELECT * FROM reels WHERE collection_id = ? ORDER BY submitted_at, id", (collection_id,)
    ).fetchall()
    return [_row_to_reel(row) for row in rows]


def record_outcome(
    conn: sqlite3.Connection,
    reel: Reel,
    *,
    code: ErrorCode,
    detail: str | None,
    now: datetime,
) -> str:
    """Requeues transient failures within the attempt budget, else fails terminally.
    Returns "requeued" or "failed"."""
    if is_transient(code) and reel.attempts < MAX_ATTEMPTS:
        requeue(conn, reel.id, code=code, detail=detail)
        return "requeued"
    mark_failed(conn, reel.id, code=code, detail=detail, now=now)
    return "failed"
