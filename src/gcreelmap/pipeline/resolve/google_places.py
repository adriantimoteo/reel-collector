import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from gcreelmap.domain.normalize import match_score, script_of
from gcreelmap.pipeline.resolve.base import (
    ResolvedPlace,
    ResolveQuery,
    ResolverError,
    ResolverUnavailable,
)

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.types"

CROSS_SCRIPT_SCORE = 0.65


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _score_candidate(query_name: str, display_name: str, *, has_bias_or_city: bool) -> float:
    query_script = script_of(query_name)
    display_script = script_of(display_name)
    if query_script != "none" and display_script != "none" and query_script != display_script:
        return CROSS_SCRIPT_SCORE if has_bias_or_city else 0.0
    return match_score(query_name, display_name)


def _extract_error_message(response: httpx.Response) -> str:
    try:
        raw = response.json()
    except ValueError:
        return f"Places API error {response.status_code}"
    if not isinstance(raw, dict):
        return f"Places API error {response.status_code}"
    data = cast(dict[str, Any], raw)
    error = data.get("error")
    if isinstance(error, dict):
        message = cast(dict[str, Any], error).get("message")
        if isinstance(message, str) and message:
            return message
    return f"Places API error {response.status_code}"


class GooglePlacesResolver:
    provider = "google"

    def __init__(
        self,
        api_key: str,
        http: httpx.AsyncClient,
        *,
        retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._http = http
        self._retries = retries
        self._sleep = sleep

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        text_query = ", ".join(part for part in (q.name, q.area, q.city_country) if part)
        body: dict[str, Any] = {
            "textQuery": text_query,
            "maxResultCount": 3,
            "languageCode": "en",
        }
        if q.bias is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": q.bias.lat, "longitude": q.bias.lng},
                    "radius": float(q.bias_radius_m),
                }
            }
        headers = {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": _FIELD_MASK}

        data = await self._request_with_retry(body, headers)
        places = cast(list[dict[str, Any]], data.get("places") or [])
        if not places:
            logger.info(
                "places resolve: provider=%s query_len=%d outcome=no_candidates",
                self.provider,
                len(text_query),
            )
            return None

        has_bias_or_city = q.bias is not None or bool(q.city_country)
        best_index = 0
        best_score = -1.0
        best_display = ""
        for i, place in enumerate(places):
            display_name_dict = cast(dict[str, Any], place.get("displayName") or {})
            display_name = cast(str, display_name_dict.get("text", ""))
            score = _score_candidate(q.name, display_name, has_bias_or_city=has_bias_or_city)
            if score > best_score:
                best_score = score
                best_index = i
                best_display = display_name

        best = places[best_index]
        logger.info(
            "places resolve: provider=%s query_len=%d outcome=resolved",
            self.provider,
            len(text_query),
        )
        location = cast(dict[str, Any], best.get("location") or {})
        return ResolvedPlace(
            provider=self.provider,
            canonical_id=cast(str, best.get("id", "")),
            name=best_display,
            address=cast("str | None", best.get("formattedAddress")),
            lat=cast(float, location.get("latitude", 0.0)),
            lng=cast(float, location.get("longitude", 0.0)),
            match_score=best_score,
        )

    async def _request_with_retry(
        self, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(1, self._retries + 1):
            is_last = attempt == self._retries
            try:
                response = await self._http.post(_SEARCH_URL, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if is_last:
                    logger.warning(
                        "places resolve: provider=%s outcome=unavailable (network)", self.provider
                    )
                    raise ResolverUnavailable(str(exc)) from exc
                await self._sleep(delay)
                delay *= 2
                continue

            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result

            if _is_retryable_status(response.status_code):
                if is_last:
                    logger.warning(
                        "places resolve: provider=%s outcome=unavailable (status %d)",
                        self.provider,
                        response.status_code,
                    )
                    raise ResolverUnavailable(f"Places API unavailable: {response.status_code}")
                await self._sleep(delay)
                delay *= 2
                continue

            message = _extract_error_message(response)
            logger.warning(
                "places resolve: provider=%s outcome=error (status %d)",
                self.provider,
                response.status_code,
            )
            raise ResolverError(message)

        raise ResolverUnavailable("exhausted retries")  # pragma: no cover
