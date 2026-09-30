from dataclasses import dataclass
from typing import Protocol

from gcreelmap.domain.geo import LatLng


@dataclass(frozen=True)
class ResolveQuery:
    name: str
    area: str | None
    city_country: str | None
    bias: LatLng | None
    bias_radius_m: int


@dataclass(frozen=True)
class ResolvedPlace:
    provider: str
    canonical_id: str
    name: str
    address: str | None
    lat: float
    lng: float
    match_score: float


class ResolverError(Exception):
    """Non-retryable failure (bad key, quota exhausted, 4xx)."""


class ResolverUnavailable(Exception):
    """Transient failure after retries (429/5xx/timeout); mention stays pending."""


class Resolver(Protocol):
    provider: str

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None: ...  # None = no result


class ResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, Resolver] = {}

    def register(self, kind: str, resolver: Resolver) -> None:
        self._resolvers[kind] = resolver

    def get(self, kind: str) -> Resolver | None:
        return self._resolvers.get(kind)
