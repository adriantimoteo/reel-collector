import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

from gcreelmap.domain.failure import ErrorCode
from gcreelmap.store.collections import create_collection
from gcreelmap.store.db import connect
from gcreelmap.store.migrate import migrate
from gcreelmap.store.reels import (
    claim_next_reel,
    enqueue_reel,
    get_reel,
    mark_failed,
    record_outcome,
    requeue_stale,
)
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def _make_collection(conn: sqlite3.Connection, clock: FakeClock, name: str = "Tokyo") -> int:
    collection = create_collection(
        conn, owner_type="user", owner_id=0, name=name, now=clock.now(), tokens=FakeTokenSource()
    )
    return collection.id


def test_enqueue_reel_twice_returns_same_id(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_collection(db, clock)
    id1, created1 = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/abc",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    id2, created2 = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/abc",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    assert id1 == id2
    assert created1 is True
    assert created2 is False


def test_claim_next_reel_oldest_first_and_increments_attempts(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_collection(db, clock)
    id1, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/1",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    clock.advance(timedelta(seconds=1))
    id2, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/2",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )

    first = claim_next_reel(db, now=clock.now())
    assert first is not None
    assert first.id == id1
    assert first.attempts == 1
    assert first.status == "processing"

    second = claim_next_reel(db, now=clock.now())
    assert second is not None
    assert second.id == id2

    assert claim_next_reel(db, now=clock.now()) is None


def test_two_concurrent_claims_never_return_the_same_reel(db_path: Path, clock: FakeClock) -> None:
    setup_conn = connect(db_path)
    migrate(setup_conn, db_path=db_path)
    collection_id = _make_collection(setup_conn, clock)
    for i in range(20):
        enqueue_reel(
            setup_conn,
            collection_id=collection_id,
            canonical_url=f"https://instagram.com/reel/{i}",
            platform="instagram",
            submitted_by=None,
            source_message_id=None,
            now=clock.now(),
        )
    setup_conn.close()

    claimed: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = connect(db_path)
        try:
            while True:
                reel = claim_next_reel(conn, now=clock.now())
                if reel is None:
                    return
                with lock:
                    claimed.append(reel.id)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 20
    assert len(set(claimed)) == 20


def test_requeue_stale_only_requeues_old_processing_reels(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_collection(db, clock)
    old_id, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/old",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    recent_id, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/recent",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    claim_next_reel(db, now=clock.now())  # claims old_id
    clock.advance(timedelta(minutes=40))
    claim_next_reel(db, now=clock.now())  # claims recent_id, 40 min after old_id

    requeued = requeue_stale(db, older_than=timedelta(minutes=30), now=clock.now())
    assert requeued == 1

    old = get_reel(db, old_id)
    recent = get_reel(db, recent_id)
    assert old is not None
    assert old.status == "queued"
    assert recent is not None
    assert recent.status == "processing"


def test_record_outcome_requeues_then_fails_at_attempt_three(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_collection(db, clock)
    reel_id, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/x",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )

    for expected_status in ("requeued", "requeued", "failed"):
        reel = claim_next_reel(db, now=clock.now())
        assert reel is not None
        status = record_outcome(
            db, reel, code=ErrorCode.DOWNLOAD_FAILED, detail="net error", now=clock.now()
        )
        assert status == expected_status

    final = get_reel(db, reel_id)
    assert final is not None
    assert final.status == "failed"
    assert final.attempts == 3


def test_record_outcome_non_transient_fails_immediately(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_collection(db, clock)
    enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://example.com/not-a-reel",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    reel = claim_next_reel(db, now=clock.now())
    assert reel is not None
    status = record_outcome(
        db, reel, code=ErrorCode.UNSUPPORTED, detail="not a reel", now=clock.now()
    )
    assert status == "failed"


def test_mark_failed_sets_processed_at(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_collection(db, clock)
    reel_id, _ = enqueue_reel(
        db,
        collection_id=collection_id,
        canonical_url="https://instagram.com/reel/x",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_failed(db, reel_id, code=ErrorCode.INTERNAL, detail="boom", now=clock.now())
    reel = get_reel(db, reel_id)
    assert reel is not None
    assert reel.status == "failed"
    assert reel.processed_at is not None


def test_same_url_allowed_across_two_collections(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_a = _make_collection(db, clock, name="Trip A")
    collection_b = _make_collection(db, clock, name="Trip B")
    id_a, created_a = enqueue_reel(
        db,
        collection_id=collection_a,
        canonical_url="https://instagram.com/reel/shared",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    id_b, created_b = enqueue_reel(
        db,
        collection_id=collection_b,
        canonical_url="https://instagram.com/reel/shared",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    assert created_a is True
    assert created_b is True
    assert id_a != id_b
