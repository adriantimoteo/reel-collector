import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from gcreelmap.domain.geo import LatLng
from gcreelmap.domain.normalize import normalize_name
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.pipeline.resolve.base import ResolvedPlace, ResolveQuery, Resolver


@dataclass(frozen=True)
class CacheHit:
    result: ResolvedPlace | None


def cache_key(
    provider: str, name: str, area: str | None, city_country: str | None, bias: LatLng | None
) -> str:
    bias_cell = "none" if bias is None else f"{round(bias.lat, 1)},{round(bias.lng, 1)}"
    raw = "|".join(
        [
            provider,
            normalize_name(name),
            normalize_name(area or ""),
            normalize_name(city_country or ""),
            bias_cell,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached(conn: sqlite3.Connection, key: str, now: datetime) -> CacheHit | None:
    row = conn.execute(
        "SELECT result_json, expires_at FROM geocode_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    result_json, expires_at = row[0], row[1]
    if expires_at <= utc_iso(now):
        return None
    if result_json is None:
        return CacheHit(result=None)
    data = json.loads(result_json)
    return CacheHit(result=ResolvedPlace(**data))


def put_cached(
    conn: sqlite3.Connection,
    key: str,
    provider: str,
    result: ResolvedPlace | None,
    now: datetime,
    ttl: timedelta,
) -> None:
    now_text = utc_iso(now)
    expires_text = utc_iso(now + ttl)
    result_json = None if result is None else json.dumps(asdict(result))
    conn.execute(
        "INSERT INTO geocode_cache (key, provider, result_json, fetched_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "provider = excluded.provider, result_json = excluded.result_json, "
        "fetched_at = excluded.fetched_at, expires_at = excluded.expires_at",
        (key, provider, result_json, now_text, expires_text),
    )


async def resolve_with_cache(
    conn: sqlite3.Connection,
    resolver: Resolver,
    query: ResolveQuery,
    *,
    now: datetime,
    ttl_pos: timedelta,
    ttl_neg: timedelta,
) -> tuple[ResolvedPlace | None, bool]:
    """Returns (result, was_network_lookup). Errors are never cached."""
    key = cache_key(resolver.provider, query.name, query.area, query.city_country, query.bias)
    hit = get_cached(conn, key, now)
    if hit is not None:
        return hit.result, False

    result = await resolver.resolve(query)
    ttl = ttl_pos if result is not None else ttl_neg
    put_cached(conn, key, resolver.provider, result, now, ttl)
    return result, True
