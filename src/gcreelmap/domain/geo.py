import math
from collections.abc import Sequence
from dataclasses import dataclass

_EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


def haversine_m(a: LatLng, b: LatLng) -> float:
    lat1, lng1, lat2, lng2 = (math.radians(v) for v in (a.lat, a.lng, b.lat, b.lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def centroid(points: Sequence[LatLng]) -> LatLng:
    lat = sum(p.lat for p in points) / len(points)
    lng = sum(p.lng for p in points) / len(points)
    return LatLng(lat=lat, lng=lng)
