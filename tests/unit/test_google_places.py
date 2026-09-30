import json
import logging

import httpx
import pytest
import respx

from gcreelmap.domain.geo import LatLng
from gcreelmap.pipeline.resolve.base import ResolveQuery, ResolverError, ResolverUnavailable
from gcreelmap.pipeline.resolve.google_places import GooglePlacesResolver
from tests.support.fixtures import load_fixture

_URL = "https://places.googleapis.com/v1/places:searchText"
_API_KEY = "AIzaSyTestKeyShouldNeverLeak1234567890"


async def _noop_sleep(_delay: float) -> None:
    return None


def _resolver(*, retries: int = 3) -> GooglePlacesResolver:
    http = httpx.AsyncClient()
    return GooglePlacesResolver(_API_KEY, http, retries=retries, sleep=_noop_sleep)


@pytest.mark.asyncio
@respx.mock
async def test_request_shape_no_bias() -> None:
    route = respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/ichiran_ok.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(
        name="Ichiran", area="Shibuya", city_country="Tokyo, Japan", bias=None, bias_radius_m=50_000
    )
    await resolver.resolve(query)

    request = route.calls[0].request
    assert request.url == _URL
    assert request.headers["X-Goog-Api-Key"] == _API_KEY
    assert (
        request.headers["X-Goog-FieldMask"]
        == "places.id,places.displayName,places.formattedAddress,places.location,places.types"
    )
    payload = json.loads(request.content)
    assert payload["textQuery"] == "Ichiran, Shibuya, Tokyo, Japan"
    assert payload["maxResultCount"] == 3
    assert payload["languageCode"] == "en"
    assert "locationBias" not in payload


@pytest.mark.asyncio
@respx.mock
async def test_request_includes_location_bias_when_given() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/ichiran_ok.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(
        name="Ichiran", area=None, city_country=None, bias=LatLng(35.0, 139.0), bias_radius_m=1000
    )
    await resolver.resolve(query)

    request = respx.calls[0].request
    payload = json.loads(request.content)
    assert payload["locationBias"] == {
        "circle": {"center": {"latitude": 35.0, "longitude": 139.0}, "radius": 1000.0}
    }


@pytest.mark.asyncio
@respx.mock
async def test_picks_best_scoring_candidate() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/multi_candidates.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    result = await resolver.resolve(query)
    assert result is not None
    assert result.name == "Ichiran Shibuya"
    assert result.canonical_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"


@pytest.mark.asyncio
@respx.mock
async def test_zero_results_returns_none() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/zero_results.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(
        name="Nonexistent Place", area=None, city_country=None, bias=None, bias_radius_m=0
    )
    assert await resolver.resolve(query) is None


@pytest.mark.asyncio
@respx.mock
async def test_429_twice_then_success() -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(429, json=load_fixture("places/error_429.json")),
        httpx.Response(429, json=load_fixture("places/error_429.json")),
        httpx.Response(200, json=load_fixture("places/ichiran_ok.json")),
    ]
    resolver = _resolver(retries=3)
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    result = await resolver.resolve(query)
    assert result is not None
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_429_exhausted_raises_resolver_unavailable() -> None:
    route = respx.post(_URL)
    route.mock(return_value=httpx.Response(429, json=load_fixture("places/error_429.json")))
    resolver = _resolver(retries=3)
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    with pytest.raises(ResolverUnavailable):
        await resolver.resolve(query)
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_resolver_error_without_key_in_message() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(403, json=load_fixture("places/error_403.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    with pytest.raises(ResolverError) as excinfo:
        await resolver.resolve(query)
    assert _API_KEY not in str(excinfo.value)
    assert "invalid" in str(excinfo.value).lower()


@pytest.mark.asyncio
@respx.mock
async def test_timeouts_retry() -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.TimeoutException("timed out"),
        httpx.Response(200, json=load_fixture("places/ichiran_ok.json")),
    ]
    resolver = _resolver(retries=3)
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    result = await resolver.resolve(query)
    assert result is not None
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_cross_script_scores_065_with_bias_or_city() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/cross_script.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(
        name="Ichiran", area=None, city_country="Tokyo, Japan", bias=None, bias_radius_m=0
    )
    result = await resolver.resolve(query)
    assert result is not None
    assert result.match_score == pytest.approx(0.65)


@pytest.mark.asyncio
@respx.mock
async def test_cross_script_scores_zero_without_bias_or_city() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/cross_script.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    result = await resolver.resolve(query)
    assert result is not None
    assert result.match_score == pytest.approx(0.0)


@pytest.mark.asyncio
@respx.mock
async def test_api_key_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("places/ichiran_ok.json"))
    )
    resolver = _resolver()
    query = ResolveQuery(name="Ichiran", area=None, city_country=None, bias=None, bias_radius_m=0)
    with caplog.at_level(logging.DEBUG):
        await resolver.resolve(query)
    for record in caplog.records:
        assert _API_KEY not in record.getMessage()
