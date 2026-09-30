import sqlite3
from datetime import datetime

from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.store.collections import create_collection, get_collection
from gcreelmap.store.items import rebuild_items
from gcreelmap.store.reels import enqueue_reel, mark_done
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def _make_trip(conn: sqlite3.Connection, clock: FakeClock, name: str = "Tokyo") -> int:
    return create_collection(
        conn, owner_type="user", owner_id=0, name=name, now=clock.now(), tokens=FakeTokenSource()
    ).id


def _seed_reel(conn: sqlite3.Connection, collection_id: int, url: str, now: datetime) -> int:
    reel_id, _ = enqueue_reel(
        conn,
        collection_id=collection_id,
        canonical_url=url,
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=now,
    )
    mark_done(conn, reel_id, author=None, now=now)
    return reel_id


def _seed_mention(
    conn: sqlite3.Connection,
    reel_id: int,
    *,
    raw_name: str,
    raw_area: str | None = None,
    raw_category: str | None = "food",
    raw_blurb: str | None = "great",
    confidence: float = 0.9,
    status: str,
    reason: str | None = None,
    match_score: float | None = None,
    resolved_provider: str | None = None,
    resolved_canonical_id: str | None = None,
    resolved_name: str | None = None,
    resolved_address: str | None = None,
    resolved_lat: float | None = None,
    resolved_lng: float | None = None,
    now: datetime,
) -> int:
    cur = conn.execute(
        "INSERT INTO item_mentions "
        "(reel_id, kind, raw_name, raw_area, raw_category, raw_blurb, confidence, "
        "resolution_status, resolution_reason, match_score, resolved_provider, "
        "resolved_canonical_id, resolved_name, resolved_address, resolved_lat, resolved_lng, "
        "created_at) VALUES (?, 'place', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            reel_id,
            raw_name,
            raw_area,
            raw_category,
            raw_blurb,
            confidence,
            status,
            reason,
            match_score,
            resolved_provider,
            resolved_canonical_id,
            resolved_name,
            resolved_address,
            resolved_lat,
            resolved_lng,
            utc_iso(now),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _resolved(conn, reel_id, *, canonical_id, name, lat, lng, match_score=0.9, now, **kwargs):
    return _seed_mention(
        conn,
        reel_id,
        raw_name=name,
        status="resolved",
        match_score=match_score,
        resolved_provider="google",
        resolved_canonical_id=canonical_id,
        resolved_name=name,
        resolved_address="1 Main St",
        resolved_lat=lat,
        resolved_lng=lng,
        now=now,
        **kwargs,
    )


def _snapshot_items(conn: sqlite3.Connection, collection_id: int) -> list[tuple]:
    rows = conn.execute(
        "SELECT merge_key, canonical_id, provider, name, address, lat, lng, category, blurb, "
        "mention_count, needs_review, review_reason FROM items "
        "WHERE collection_id = ? ORDER BY merge_key",
        (collection_id,),
    ).fetchall()
    return [tuple(row) for row in rows]


def test_rebuild_inserts_items_and_sets_mention_item_id(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    reel_id = _seed_reel(db, collection_id, "https://instagram.com/reel/a", clock.now())
    mention_id = _resolved(
        db, reel_id, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now()
    )

    result = rebuild_items(db, collection_id, now=clock.now())
    assert len(result.items) == 1
    assert result.changed is True

    item_row = db.execute(
        "SELECT id FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    mention_row = db.execute(
        "SELECT item_id FROM item_mentions WHERE id = ?", (mention_id,)
    ).fetchone()
    assert mention_row[0] == item_row[0]


def test_second_identical_rebuild_is_unchanged_and_no_version_bump(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    reel_id = _seed_reel(db, collection_id, "https://instagram.com/reel/a", clock.now())
    _resolved(db, reel_id, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now())

    rebuild_items(db, collection_id, now=clock.now())
    before = get_collection(db, collection_id)
    assert before is not None

    second = rebuild_items(db, collection_id, now=clock.now())
    assert second.changed is False
    after = get_collection(db, collection_id)
    assert after is not None
    assert after.version == before.version


def test_merging_mention_keeps_existing_item_id(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    reel1 = _seed_reel(db, collection_id, "https://instagram.com/reel/a", clock.now())
    _resolved(db, reel1, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now())
    rebuild_items(db, collection_id, now=clock.now())
    item_id_before = db.execute(
        "SELECT id FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]

    reel2 = _seed_reel(db, collection_id, "https://instagram.com/reel/b", clock.now())
    _resolved(db, reel2, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now())
    result = rebuild_items(db, collection_id, now=clock.now())

    item_id_after = db.execute(
        "SELECT id FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]
    assert item_id_after == item_id_before
    assert result.items[0].mention_count == 2


def test_merge_key_change_still_reuses_id_via_candidate_keys(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    reel1 = _seed_reel(db, collection_id, "https://instagram.com/reel/a", clock.now())
    _resolved(
        db,
        reel1,
        canonical_id="X",
        name="Ichiran",
        lat=35.0,
        lng=139.0,
        match_score=0.5,
        now=clock.now(),
    )
    rebuild_items(db, collection_id, now=clock.now())
    row = db.execute(
        "SELECT id, merge_key FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    item_id_before, merge_key_before = row[0], row[1]
    assert merge_key_before == "id:google:X"

    # A second, better-matching mention 80m away with a *different* canonical id
    # merges into the same cluster (distance + name similarity) and becomes the
    # new best member, changing merge_key -- but the DB row must be reused.
    reel2 = _seed_reel(db, collection_id, "https://instagram.com/reel/b", clock.now())
    _resolved(
        db,
        reel2,
        canonical_id="Y",
        name="Ichiran",
        lat=35.0007,
        lng=139.0,
        match_score=0.95,
        now=clock.now(),
    )
    rebuild_items(db, collection_id, now=clock.now())

    row_after = db.execute(
        "SELECT id, merge_key FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    assert row_after[0] == item_id_before
    assert row_after[1] == "id:google:Y"


def test_item_with_no_remaining_mentions_is_deleted(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    reel_id = _seed_reel(db, collection_id, "https://instagram.com/reel/a", clock.now())
    mention_id = _resolved(
        db, reel_id, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now()
    )
    rebuild_items(db, collection_id, now=clock.now())
    assert (
        db.execute(
            "SELECT COUNT(*) FROM items WHERE collection_id = ?", (collection_id,)
        ).fetchone()[0]
        == 1
    )

    db.execute("UPDATE item_mentions SET resolution_status = 'skipped' WHERE id = ?", (mention_id,))
    rebuild_items(db, collection_id, now=clock.now())
    assert (
        db.execute(
            "SELECT COUNT(*) FROM items WHERE collection_id = ?", (collection_id,)
        ).fetchone()[0]
        == 0
    )


def test_reels_not_done_are_ignored(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    reel_id, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/a",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    # reel stays 'queued' -- never marked done
    _seed_mention(
        db,
        reel_id,
        raw_name="Ichiran",
        status="resolved",
        match_score=0.9,
        resolved_provider="google",
        resolved_canonical_id="X",
        resolved_name="Ichiran",
        resolved_lat=35.0,
        resolved_lng=139.0,
        now=clock.now(),
    )
    result = rebuild_items(db, collection_id, now=clock.now())
    assert result.items == []


def test_incremental_equals_from_scratch(clock: FakeClock) -> None:
    from gcreelmap.store.db import connect
    from gcreelmap.store.migrate import migrate

    db_a = connect(":memory:")
    migrate(db_a, db_path=None)
    db_b = connect(":memory:")
    migrate(db_b, db_path=None)

    collection_a = _make_trip(db_a, clock)
    collection_b = _make_trip(db_b, clock)

    reel_a1 = _seed_reel(db_a, collection_a, "https://instagram.com/reel/a", clock.now())
    _resolved(db_a, reel_a1, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now())
    rebuild_items(db_a, collection_a, now=clock.now())  # incremental: rebuild after reel 1

    reel_a2 = _seed_reel(db_a, collection_a, "https://instagram.com/reel/b", clock.now())
    _resolved(
        db_a, reel_a2, canonical_id="Y", name="Meiji Shrine", lat=10.0, lng=10.0, now=clock.now()
    )
    rebuild_items(db_a, collection_a, now=clock.now())  # incremental: rebuild after reel 2

    reel_b1 = _seed_reel(db_b, collection_b, "https://instagram.com/reel/a", clock.now())
    _resolved(db_b, reel_b1, canonical_id="X", name="Ichiran", lat=35.0, lng=139.0, now=clock.now())
    reel_b2 = _seed_reel(db_b, collection_b, "https://instagram.com/reel/b", clock.now())
    _resolved(
        db_b, reel_b2, canonical_id="Y", name="Meiji Shrine", lat=10.0, lng=10.0, now=clock.now()
    )
    rebuild_items(db_b, collection_b, now=clock.now())  # from scratch: one rebuild

    assert _snapshot_items(db_a, collection_a) == _snapshot_items(db_b, collection_b)
