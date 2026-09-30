import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from gcreelmap.config import Settings
from gcreelmap.domain.clock import Clock
from gcreelmap.domain.geo import LatLng, centroid
from gcreelmap.domain.merge import MergeConfig
from gcreelmap.pipeline.resolve.base import (
    ResolvedPlace,
    ResolveQuery,
    ResolverError,
    ResolverRegistry,
    ResolverUnavailable,
)
from gcreelmap.store.geocode_cache import cache_key, get_cached, put_cached
from gcreelmap.store.items import rebuild_items

logger = logging.getLogger(__name__)

_UNAVAILABLE_BREAKER_LIMIT = 3


class LookupBudget:
    def __init__(self, max_per_run: int, max_per_day: int) -> None:
        self.max_per_run = max_per_run
        self.max_per_day = max_per_day
        self._run_count = 0

    def consume(self, conn: sqlite3.Connection, now: datetime) -> bool:
        if self._run_count >= self.max_per_run:
            return False
        key = f"places_lookups:{now.strftime('%Y-%m-%d')}"
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        day_count = int(row[0]) if row is not None else 0
        if day_count >= self.max_per_day:
            return False
        self._run_count += 1
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(day_count + 1)),
        )
        return True


@dataclass(frozen=True)
class ResolveReport:
    resolved: int
    unresolved: int
    still_pending: int
    lookups: int
    budget_exhausted: bool


def _count_pending(conn: sqlite3.Connection, collection_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? AND r.status = 'done' AND m.kind = 'place' "
        "AND m.resolution_status = 'pending'",
        (collection_id,),
    ).fetchone()
    return row[0]


def _load_pending_mentions(conn: sqlite3.Connection, collection_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT m.id, m.raw_name, m.raw_area, m.raw_city_country "
        "FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? AND r.status = 'done' AND m.kind = 'place' "
        "AND m.resolution_status = 'pending' "
        "ORDER BY m.id",
        (collection_id,),
    ).fetchall()


def _compute_bias(conn: sqlite3.Connection, collection_id: int) -> LatLng | None:
    rows = conn.execute(
        "SELECT DISTINCT m.resolved_canonical_id, m.resolved_lat, m.resolved_lng "
        "FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? AND m.resolution_status = 'resolved' "
        "AND m.match_score >= ? AND m.resolved_lat IS NOT NULL AND m.resolved_lng IS NOT NULL",
        (collection_id, MergeConfig().review_min_match_score),
    ).fetchall()
    if len(rows) < 3:
        return None
    return centroid([LatLng(row[1], row[2]) for row in rows])


def _write_resolved(conn: sqlite3.Connection, mention_id: int, result: ResolvedPlace) -> None:
    conn.execute(
        "UPDATE item_mentions SET resolution_status = 'resolved', resolution_reason = NULL, "
        "match_score = ?, resolved_provider = ?, resolved_canonical_id = ?, resolved_name = ?, "
        "resolved_address = ?, resolved_lat = ?, resolved_lng = ? WHERE id = ?",
        (
            result.match_score,
            result.provider,
            result.canonical_id,
            result.name,
            result.address,
            result.lat,
            result.lng,
            mention_id,
        ),
    )


def _write_unresolved(
    conn: sqlite3.Connection, mention_id: int, *, reason: str, result: ResolvedPlace | None
) -> None:
    if result is None:
        conn.execute(
            "UPDATE item_mentions SET resolution_status = 'unresolved', resolution_reason = ? "
            "WHERE id = ?",
            (reason, mention_id),
        )
        return
    conn.execute(
        "UPDATE item_mentions SET resolution_status = 'unresolved', resolution_reason = ?, "
        "match_score = ?, resolved_provider = ?, resolved_canonical_id = ?, resolved_name = ?, "
        "resolved_address = ?, resolved_lat = ?, resolved_lng = ? WHERE id = ?",
        (
            reason,
            result.match_score,
            result.provider,
            result.canonical_id,
            result.name,
            result.address,
            result.lat,
            result.lng,
            mention_id,
        ),
    )


async def resolve_pending_mentions(
    conn: sqlite3.Connection,
    *,
    collection_id: int,
    registry: ResolverRegistry,
    budget: LookupBudget,
    settings: Settings,
    clock: Clock,
) -> ResolveReport:
    resolver = registry.get("place")
    if resolver is None:
        return ResolveReport(
            resolved=0,
            unresolved=0,
            still_pending=_count_pending(conn, collection_id),
            lookups=0,
            budget_exhausted=False,
        )

    review_min_match_score = MergeConfig().review_min_match_score
    ttl_pos = timedelta(days=settings.geocode_cache_ttl_days)
    ttl_neg = timedelta(days=settings.geocode_negative_ttl_days)

    resolved_count = 0
    unresolved_count = 0
    lookups = 0
    budget_exhausted = False
    consecutive_unavailable = 0

    pending = _load_pending_mentions(conn, collection_id)
    for mention_id, raw_name, raw_area, raw_city_country in pending:
        now = clock.now()
        bias = _compute_bias(conn, collection_id)
        query = ResolveQuery(
            name=raw_name,
            area=raw_area,
            city_country=raw_city_country,
            bias=bias,
            bias_radius_m=settings.places_bias_radius_m,
        )

        key = cache_key(resolver.provider, query.name, query.area, query.city_country, query.bias)
        hit = get_cached(conn, key, now)
        if hit is not None:
            result = hit.result
        else:
            if not budget.consume(conn, now):
                budget_exhausted = True
                logger.warning("Places lookup budget exhausted; stopping resolve stage")
                break
            try:
                result = await resolver.resolve(query)
            except ResolverUnavailable:
                consecutive_unavailable += 1
                logger.warning("resolver unavailable (%d in a row)", consecutive_unavailable)
                if consecutive_unavailable >= _UNAVAILABLE_BREAKER_LIMIT:
                    break
                continue
            except ResolverError:
                rebuild_items(conn, collection_id, now=clock.now())
                raise
            lookups += 1
            ttl = ttl_pos if result is not None else ttl_neg
            put_cached(conn, key, resolver.provider, result, now, ttl)

        consecutive_unavailable = 0

        if result is None:
            _write_unresolved(conn, mention_id, reason="geocode_failed", result=None)
            unresolved_count += 1
        elif result.match_score < review_min_match_score:
            _write_unresolved(conn, mention_id, reason="low_match", result=result)
            unresolved_count += 1
        else:
            _write_resolved(conn, mention_id, result)
            resolved_count += 1

    rebuild_items(conn, collection_id, now=clock.now())

    return ResolveReport(
        resolved=resolved_count,
        unresolved=unresolved_count,
        still_pending=_count_pending(conn, collection_id),
        lookups=lookups,
        budget_exhausted=budget_exhausted,
    )
