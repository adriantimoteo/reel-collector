import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gcreelmap.config import Settings
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.pipeline.resolve.base import (
    ResolvedPlace,
    ResolveQuery,
    ResolverError,
    ResolverRegistry,
    ResolverUnavailable,
)
from gcreelmap.pipeline.resolve_stage import LookupBudget, resolve_pending_mentions
from gcreelmap.store.collections import create_collection
from gcreelmap.store.reels import enqueue_reel, mark_done
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        db_path=Path("db.sqlite"),
        download_temp_dir=Path("tmp"),
        lock_path=Path("run.lock"),
        log_level="INFO",
        gemini_model="gemini-3.5-flash",
        max_video_duration_seconds=120,
        ytdlp_cookies_file=None,
        ytdlp_cookies_from_browser=None,
        places_max_lookups_per_run=150,
        places_max_lookups_per_day=300,
        geocode_cache_ttl_days=30,
        geocode_negative_ttl_days=7,
        places_bias_radius_m=50_000,
    )
    base.update(overrides)
    return Settings(**base)


class _ScriptedResolver:
    provider = "google"

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.queries: list[ResolveQuery] = []

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        self.queries.append(q)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


def _place(
    canonical_id: str, *, match_score: float, lat: float = 35.0, lng: float = 139.0
) -> ResolvedPlace:
    return ResolvedPlace(
        provider="google",
        canonical_id=canonical_id,
        name="Some Place",
        address="1 Main St",
        lat=lat,
        lng=lng,
        match_score=match_score,
    )


def _make_trip(conn: sqlite3.Connection, clock: FakeClock) -> int:
    return create_collection(
        conn, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=FakeTokenSource()
    ).id


def _seed_pending_mentions(
    conn: sqlite3.Connection, collection_id: int, n: int, clock: FakeClock
) -> list[int]:
    ids = []
    for i in range(n):
        reel_id, _ = enqueue_reel(
            conn,
            collection_id=collection_id,
            canonical_url=f"https://instagram.com/reel/{i}",
            platform="instagram",
            submitted_by=None,
            source_message_id=None,
            now=clock.now(),
        )
        mark_done(conn, reel_id, author=None, now=clock.now())
        cur = conn.execute(
            "INSERT INTO item_mentions (reel_id, kind, raw_name, confidence, resolution_status, "
            "created_at) VALUES (?, 'place', ?, 0.9, 'pending', ?)",
            (reel_id, f"Place {i}", utc_iso(clock.now())),
        )
        assert cur.lastrowid is not None
        ids.append(cur.lastrowid)
    return ids


def _registry(resolver: _ScriptedResolver) -> ResolverRegistry:
    reg = ResolverRegistry()
    reg.register("place", resolver)
    return reg


@pytest.mark.asyncio
async def test_resolved_and_unresolved_with_correct_reasons(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 2, clock)
    resolver = _ScriptedResolver([_place("A", match_score=0.9), None])
    report = await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(150, 300),
        settings=_settings(),
        clock=clock,
    )
    assert report.resolved == 1
    assert report.unresolved == 1
    rows = db.execute(
        "SELECT resolution_status, resolution_reason FROM item_mentions ORDER BY id"
    ).fetchall()
    assert rows[0][0] == "resolved"
    assert tuple(rows[1]) == ("unresolved", "geocode_failed")


@pytest.mark.asyncio
async def test_low_match_score_is_unresolved(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 1, clock)
    resolver = _ScriptedResolver([_place("A", match_score=0.5)])
    report = await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(150, 300),
        settings=_settings(),
        clock=clock,
    )
    assert report.unresolved == 1
    row = db.execute("SELECT resolution_status, resolution_reason FROM item_mentions").fetchone()
    assert tuple(row) == ("unresolved", "low_match")


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_stage(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 3, clock)
    resolver = _ScriptedResolver([_place("A", match_score=0.9), _place("B", match_score=0.9)])
    report = await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(max_per_run=2, max_per_day=300),
        settings=_settings(),
        clock=clock,
    )
    assert report.budget_exhausted is True
    assert report.still_pending == 1


@pytest.mark.asyncio
async def test_cache_hits_do_not_consume_budget(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 1, clock)
    # Pre-seed the cache so the resolver is never actually called.
    from gcreelmap.store.geocode_cache import cache_key, put_cached

    key = cache_key("google", "Place 0", None, None, None)
    put_cached(db, key, "google", _place("A", match_score=0.9), clock.now(), timedelta(days=30))

    resolver = _ScriptedResolver([])  # would raise IndexError if ever called
    report = await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(max_per_run=0, max_per_day=0),
        settings=_settings(),
        clock=clock,
    )
    assert report.resolved == 1
    assert report.lookups == 0


@pytest.mark.asyncio
async def test_daily_counter_persists_and_resets_next_day(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    budget = LookupBudget(max_per_run=100, max_per_day=1)
    assert budget.consume(db, clock.now()) is True
    assert budget.consume(db, clock.now()) is False  # exhausted for today

    # A fresh LookupBudget (new run) still sees the persisted daily count.
    budget2 = LookupBudget(max_per_run=100, max_per_day=1)
    assert budget2.consume(db, clock.now()) is False

    clock.advance(timedelta(days=1))
    budget3 = LookupBudget(max_per_run=100, max_per_day=1)
    assert budget3.consume(db, clock.now()) is True


@pytest.mark.asyncio
async def test_bias_none_below_three_centroid_at_three(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 4, clock)
    resolver = _ScriptedResolver(
        [
            _place("A", match_score=0.9, lat=10.0, lng=10.0),
            _place("B", match_score=0.9, lat=20.0, lng=20.0),
            _place("C", match_score=0.9, lat=30.0, lng=30.0),
            _place("D", match_score=0.9, lat=40.0, lng=40.0),
        ]
    )
    await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(150, 300),
        settings=_settings(),
        clock=clock,
    )
    # By the time each mention is queried, only *earlier* mentions have been
    # written as resolved yet: mentions 0-2 see fewer than 3 resolved places
    # so far (0, 1, then 2) -> no bias. Mention 3 sees 3 already-resolved
    # places (from 0, 1, 2) -> centroid bias.
    assert resolver.queries[0].bias is None
    assert resolver.queries[1].bias is None
    assert resolver.queries[2].bias is None
    assert resolver.queries[3].bias is not None


@pytest.mark.asyncio
async def test_resolver_unavailable_three_times_trips_breaker(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 4, clock)
    resolver = _ScriptedResolver(
        [ResolverUnavailable("down"), ResolverUnavailable("down"), ResolverUnavailable("down")]
    )
    report = await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(150, 300),
        settings=_settings(),
        clock=clock,
    )
    assert report.still_pending == 4  # nothing resolved; breaker stopped before any success
    assert len(resolver.queries) == 3


@pytest.mark.asyncio
async def test_resolver_error_stops_and_reraises_keeping_earlier_writes(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 2, clock)
    resolver = _ScriptedResolver([_place("A", match_score=0.9), ResolverError("bad key")])
    with pytest.raises(ResolverError):
        await resolve_pending_mentions(
            db,
            collection_id=collection_id,
            registry=_registry(resolver),
            budget=LookupBudget(150, 300),
            settings=_settings(),
            clock=clock,
        )
    rows = db.execute("SELECT resolution_status FROM item_mentions ORDER BY id").fetchall()
    assert rows[0][0] == "resolved"
    assert rows[1][0] == "pending"


@pytest.mark.asyncio
async def test_rebuild_items_called_once_at_end(db: sqlite3.Connection, clock: FakeClock) -> None:
    collection_id = _make_trip(db, clock)
    _seed_pending_mentions(db, collection_id, 1, clock)
    resolver = _ScriptedResolver([_place("A", match_score=0.9)])
    await resolve_pending_mentions(
        db,
        collection_id=collection_id,
        registry=_registry(resolver),
        budget=LookupBudget(150, 300),
        settings=_settings(),
        clock=clock,
    )
    count = db.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]
    assert count == 1
