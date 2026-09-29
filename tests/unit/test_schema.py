import sqlite3

import pytest

from gcreelmap.domain.clock import SystemClock
from gcreelmap.domain.timeutil import utc_iso

_EXPECTED_COLUMNS = {
    "kv": {"key", "value"},
    "collections": {
        "id",
        "slug",
        "owner_type",
        "owner_id",
        "name",
        "start_date",
        "end_date",
        "expires_at",
        "tier",
        "state",
        "version",
        "published_version",
        "created_at",
        "expired_at",
        "unpublished_at",
    },
    "groups": {
        "telegram_chat_id",
        "active_collection_id",
        "reply_mode",
        "paused",
        "timezone",
        "tier",
        "added_at",
        "removed_at",
    },
    "reels": {
        "id",
        "collection_id",
        "canonical_url",
        "platform",
        "status",
        "attempts",
        "error_code",
        "error_detail",
        "author",
        "submitted_by",
        "source_message_id",
        "submitted_at",
        "claimed_at",
        "processed_at",
        "notified_at",
    },
    "items": {
        "id",
        "collection_id",
        "kind",
        "merge_key",
        "canonical_id",
        "provider",
        "name",
        "address",
        "lat",
        "lng",
        "category",
        "blurb",
        "mention_count",
        "last_mentioned_at",
        "needs_review",
        "review_reason",
        "created_at",
    },
    "item_mentions": {
        "id",
        "reel_id",
        "item_id",
        "kind",
        "raw_name",
        "raw_area",
        "raw_city_country",
        "raw_category",
        "raw_blurb",
        "confidence",
        "resolution_status",
        "resolution_reason",
        "match_score",
        "resolved_provider",
        "resolved_canonical_id",
        "resolved_name",
        "resolved_address",
        "resolved_lat",
        "resolved_lng",
        "created_at",
    },
    "geocode_cache": {"key", "provider", "result_json", "fetched_at", "expires_at"},
    "outbox": {
        "id",
        "chat_id",
        "kind",
        "payload",
        "dedupe_key",
        "created_at",
        "send_after",
        "attempts",
        "sent_at",
        "failed_at",
        "last_error",
    },
}


@pytest.mark.parametrize("table", sorted(_EXPECTED_COLUMNS))
def test_table_has_exactly_the_expected_columns(db: sqlite3.Connection, table: str) -> None:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in rows}
    assert columns == _EXPECTED_COLUMNS[table]


def _now() -> str:
    return utc_iso(SystemClock().now())


def _make_collection(db: sqlite3.Connection, *, id_: int = 1) -> None:
    db.execute(
        "INSERT INTO collections (id, slug, owner_type, owner_id, name, created_at) "
        "VALUES (?, ?, 'group', 1, 'Trip', ?)",
        (id_, f"trip-{id_}", _now()),
    )


def test_foreign_key_enforced_for_reels(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reels (collection_id, canonical_url, platform, submitted_at) "
            "VALUES (999, 'https://example.com/a', 'instagram', ?)",
            (_now(),),
        )


def test_unique_collection_canonical_url(db: sqlite3.Connection) -> None:
    _make_collection(db)
    db.execute(
        "INSERT INTO reels (collection_id, canonical_url, platform, submitted_at) "
        "VALUES (1, 'https://example.com/a', 'instagram', ?)",
        (_now(),),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reels (collection_id, canonical_url, platform, submitted_at) "
            "VALUES (1, 'https://example.com/a', 'instagram', ?)",
            (_now(),),
        )


def test_reply_mode_check_constraint(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO groups (telegram_chat_id, reply_mode, added_at) VALUES (1, 'loud', ?)",
            (_now(),),
        )


def test_items_lat_lng_must_both_be_set_or_null(db: sqlite3.Connection) -> None:
    _make_collection(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO items (collection_id, merge_key, name, lat, lng, created_at) "
            "VALUES (1, 'k', 'Place', 1.0, NULL, ?)",
            (_now(),),
        )


def test_deleting_collection_cascades(db: sqlite3.Connection) -> None:
    _make_collection(db)
    db.execute(
        "INSERT INTO reels (id, collection_id, canonical_url, platform, submitted_at) "
        "VALUES (1, 1, 'https://example.com/a', 'instagram', ?)",
        (_now(),),
    )
    db.execute(
        "INSERT INTO items (id, collection_id, merge_key, name, created_at) "
        "VALUES (1, 1, 'k', 'Place', ?)",
        (_now(),),
    )
    db.execute(
        "INSERT INTO item_mentions (reel_id, item_id, raw_name, confidence, created_at) "
        "VALUES (1, 1, 'Place', 0.9, ?)",
        (_now(),),
    )
    db.execute("DELETE FROM collections WHERE id = 1")
    assert db.execute("SELECT COUNT(*) FROM reels").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM item_mentions").fetchone()[0] == 0


def test_deleting_collection_sets_active_collection_id_null(db: sqlite3.Connection) -> None:
    _make_collection(db)
    db.execute(
        "INSERT INTO groups (telegram_chat_id, active_collection_id, added_at) VALUES (1, 1, ?)",
        (_now(),),
    )
    db.execute("DELETE FROM collections WHERE id = 1")
    row = db.execute(
        "SELECT active_collection_id FROM groups WHERE telegram_chat_id = 1"
    ).fetchone()
    assert row[0] is None


def test_outbox_dedupe_key_unique_but_allows_multiple_nulls(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO outbox (chat_id, kind, payload, dedupe_key, created_at, send_after) "
        "VALUES (1, 'text', '{}', 'k1', ?, ?)",
        (_now(), _now()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO outbox (chat_id, kind, payload, dedupe_key, created_at, send_after) "
            "VALUES (1, 'text', '{}', 'k1', ?, ?)",
            (_now(), _now()),
        )
    # Multiple NULL dedupe_keys are allowed.
    db.execute(
        "INSERT INTO outbox (chat_id, kind, payload, dedupe_key, created_at, send_after) "
        "VALUES (1, 'text', '{}', NULL, ?, ?)",
        (_now(), _now()),
    )
    db.execute(
        "INSERT INTO outbox (chat_id, kind, payload, dedupe_key, created_at, send_after) "
        "VALUES (1, 'text', '{}', NULL, ?, ?)",
        (_now(), _now()),
    )
