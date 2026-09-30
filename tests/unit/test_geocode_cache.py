import sqlite3
from datetime import timedelta

import pytest

from gcreelmap.domain.geo import LatLng
from gcreelmap.pipeline.resolve.base import ResolvedPlace, ResolveQuery
from gcreelmap.store.geocode_cache import cache_key, get_cached, put_cached, resolve_with_cache
from tests.support.clock import FakeClock

_PLACE = ResolvedPlace(
    provider="google",
    canonical_id="X",
    name="Ichiran",
    address="1 Main St",
    lat=35.0,
    lng=139.0,
    match_score=0.9,
)


def test_key_stable_across_case_whitespace_accents() -> None:
    k1 = cache_key("google", "Café  Nook", "Shibuya", "Tokyo, Japan", None)
    k2 = cache_key("google", "cafe nook", "shibuya", "tokyo, japan", None)
    assert k1 == k2


def test_key_differs_for_different_bias_cell() -> None:
    k1 = cache_key("google", "Ichiran", None, None, LatLng(35.0, 139.0))
    k2 = cache_key("google", "Ichiran", None, None, LatLng(36.0, 140.0))
    assert k1 != k2


def test_positive_hit_returns_stored_place(db: sqlite3.Connection, clock: FakeClock) -> None:
    key = cache_key("google", "Ichiran", None, None, None)
    put_cached(db, key, "google", _PLACE, clock.now(), timedelta(days=30))
    hit = get_cached(db, key, clock.now())
    assert hit is not None
    assert hit.result == _PLACE


def test_expired_row_is_a_miss(db: sqlite3.Connection, clock: FakeClock) -> None:
    key = cache_key("google", "Ichiran", None, None, None)
    put_cached(db, key, "google", _PLACE, clock.now(), timedelta(days=1))
    clock.advance(timedelta(days=2))
    assert get_cached(db, key, clock.now()) is None


def test_negative_result_cached_as_hit_with_none(db: sqlite3.Connection, clock: FakeClock) -> None:
    key = cache_key("google", "Nonexistent", None, None, None)
    put_cached(db, key, "google", None, clock.now(), timedelta(days=7))
    hit = get_cached(db, key, clock.now())
    assert hit is not None
    assert hit.result is None


class _FakeResolver:
    provider = "google"

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        self.calls += 1
        return _PLACE


@pytest.mark.asyncio
async def test_resolve_with_cache_hit_reports_no_network_lookup(
    db: sqlite3.Connection, clock: FakeClock
) -> None:
    resolver = _FakeResolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    result1, network1 = await resolve_with_cache(
        db, resolver, query, now=clock.now(), ttl_pos=timedelta(days=30), ttl_neg=timedelta(days=7)
    )
    assert network1 is True
    assert resolver.calls == 1

    result2, network2 = await resolve_with_cache(
        db, resolver, query, now=clock.now(), ttl_pos=timedelta(days=30), ttl_neg=timedelta(days=7)
    )
    assert network2 is False
    assert resolver.calls == 1  # cache hit, no second network call
    assert result1 == result2


class _RaisingResolver:
    provider = "google"

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_errors_are_not_cached(db: sqlite3.Connection, clock: FakeClock) -> None:
    resolver = _RaisingResolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    with pytest.raises(RuntimeError):
        await resolve_with_cache(
            db,
            resolver,
            query,
            now=clock.now(),
            ttl_pos=timedelta(days=30),
            ttl_neg=timedelta(days=7),
        )
    key = cache_key("google", "Ichiran", None, None, None)
    assert get_cached(db, key, clock.now()) is None
