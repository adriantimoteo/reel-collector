import sqlite3

from gcreelmap.pipeline.extract import ExtractedPlace
from gcreelmap.store.collections import create_collection
from gcreelmap.store.mentions import count_mentions_for_reel, insert_mentions, list_mentions
from gcreelmap.store.reels import enqueue_reel
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def _setup_reel(db: sqlite3.Connection, clock: FakeClock) -> int:
    collection = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=FakeTokenSource()
    )
    reel_id, _ = enqueue_reel(
        db,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/abc",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    return reel_id


def test_place_mentions_pending_other_skipped(db: sqlite3.Connection, clock: FakeClock) -> None:
    reel_id = _setup_reel(db, clock)
    places = [
        ExtractedPlace(name="Ichiran", confidence=0.9, kind="place"),
        ExtractedPlace(name="A Book", confidence=0.5, kind="other"),
    ]
    insert_mentions(db, reel_id, places, clock.now())
    rows = db.execute(
        "SELECT raw_name, resolution_status FROM item_mentions WHERE reel_id = ? ORDER BY id",
        (reel_id,),
    ).fetchall()
    assert rows[0][1] == "pending"
    assert rows[1][1] == "skipped"


def test_reinserting_replaces_not_duplicates(db: sqlite3.Connection, clock: FakeClock) -> None:
    reel_id = _setup_reel(db, clock)
    insert_mentions(
        db, reel_id, [ExtractedPlace(name="A", confidence=0.9, kind="place")], clock.now()
    )
    assert count_mentions_for_reel(db, reel_id) == 1
    insert_mentions(
        db,
        reel_id,
        [
            ExtractedPlace(name="B", confidence=0.8, kind="place"),
            ExtractedPlace(name="C", confidence=0.7, kind="place"),
        ],
        clock.now(),
    )
    assert count_mentions_for_reel(db, reel_id) == 2
    names = {
        row[0]
        for row in db.execute("SELECT raw_name FROM item_mentions WHERE reel_id = ?", (reel_id,))
    }
    assert names == {"B", "C"}


def test_deleting_reel_cascades_to_mentions(db: sqlite3.Connection, clock: FakeClock) -> None:
    reel_id = _setup_reel(db, clock)
    insert_mentions(
        db, reel_id, [ExtractedPlace(name="A", confidence=0.9, kind="place")], clock.now()
    )
    db.execute("DELETE FROM reels WHERE id = ?", (reel_id,))
    assert count_mentions_for_reel(db, reel_id) == 0


def test_list_mentions_scoped_to_collection(db: sqlite3.Connection, clock: FakeClock) -> None:
    reel_id = _setup_reel(db, clock)
    collection_id = db.execute(
        "SELECT collection_id FROM reels WHERE id = ?", (reel_id,)
    ).fetchone()[0]
    insert_mentions(
        db,
        reel_id,
        [ExtractedPlace(name="A", confidence=0.9, kind="place")],
        clock.now(),
    )
    rows = list_mentions(db, collection_id)
    assert len(rows) == 1
    assert rows[0]["raw_name"] == "A"
