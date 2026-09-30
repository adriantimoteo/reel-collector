import pytest

from gcreelmap.domain.geo import LatLng, centroid, haversine_m

_TOKYO_STATION = LatLng(35.6812, 139.7671)
_SHIBUYA_STATION = LatLng(35.6580, 139.7016)


def test_haversine_known_distance() -> None:
    distance = haversine_m(_TOKYO_STATION, _SHIBUYA_STATION)
    assert 6400 <= distance <= 6900


def test_haversine_zero_for_identical_points() -> None:
    p = LatLng(35.0, 139.0)
    assert haversine_m(p, p) == pytest.approx(0.0, abs=1e-6)


def test_centroid_of_three_points() -> None:
    points = [LatLng(0.0, 0.0), LatLng(3.0, 0.0), LatLng(0.0, 3.0)]
    c = centroid(points)
    assert c.lat == pytest.approx(1.0)
    assert c.lng == pytest.approx(1.0)
