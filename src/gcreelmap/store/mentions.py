import sqlite3
from collections.abc import Sequence
from datetime import datetime

from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.pipeline.extract import ExtractedPlace
from gcreelmap.store.db import transaction


def _insert_all(
    conn: sqlite3.Connection, reel_id: int, places: Sequence[ExtractedPlace], now: str
) -> None:
    conn.execute("DELETE FROM item_mentions WHERE reel_id = ?", (reel_id,))
    for place in places:
        status = "pending" if place.kind == "place" else "skipped"
        conn.execute(
            "INSERT INTO item_mentions "
            "(reel_id, kind, raw_name, raw_area, raw_city_country, raw_category, raw_blurb, "
            "confidence, resolution_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reel_id,
                place.kind,
                place.name,
                place.area,
                place.city_country,
                place.category,
                place.blurb,
                place.confidence,
                status,
                now,
            ),
        )


def insert_mentions(
    conn: sqlite3.Connection, reel_id: int, places: Sequence[ExtractedPlace], now: datetime
) -> int:
    """Replaces any existing mentions for this reel (delete then insert), atomically.

    Participates in an already-open caller transaction if there is one (so an
    orchestrator like process_reel can compose this with other writes); opens
    its own otherwise, since P0's transaction() refuses to nest.
    """
    now_text = utc_iso(now)
    if conn.in_transaction:
        _insert_all(conn, reel_id, places, now_text)
    else:
        with transaction(conn):
            _insert_all(conn, reel_id, places, now_text)
    return len(places)


def list_mentions(conn: sqlite3.Connection, collection_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT m.* FROM item_mentions m "
        "JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? "
        "ORDER BY m.reel_id, m.id",
        (collection_id,),
    ).fetchall()


def count_mentions_for_reel(conn: sqlite3.Connection, reel_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM item_mentions WHERE reel_id = ?", (reel_id,)
    ).fetchone()
    return row[0]
