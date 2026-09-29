from pathlib import Path
from typing import Any

import pytest
from reelkit.exceptions import ExtractionError
from reelkit.models import ReelMetadata

from gcreelmap.pipeline.extract import build_prompt, extract_places
from tests.support.fixtures import load_fixture


def _metadata(**overrides: Any) -> ReelMetadata:
    defaults: dict[str, Any] = {
        "source_url": "https://instagram.com/reel/abc",
        "platform": "instagram",
        "author": "traveler_jane",
        "posted_at": None,
        "title": None,
        "caption": "5 places to eat in Tokyo #foodie",
        "video_path": Path("video.mp4"),
        "hashtags": ["foodie", "tokyo"],
    }
    defaults.update(overrides)
    return ReelMetadata(**defaults)


def _fake_generate(response: dict[str, Any]) -> Any:
    calls: list[dict[str, Any]] = []

    async def generate(
        client: Any,
        model: str,
        paths: list[Path],
        prompt: str,
        schema: dict[str, Any],
        *,
        description: str,
    ) -> dict[str, Any]:
        calls.append(
            {
                "client": client,
                "model": model,
                "paths": paths,
                "prompt": prompt,
                "schema": schema,
                "description": description,
            }
        )
        return response

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


async def _extract(fixture_name: str) -> Any:
    response = load_fixture(f"gemini/{fixture_name}")
    metadata = _metadata()
    fake = _fake_generate(response)
    return await extract_places(
        metadata, trip_name="Tokyo test", client=object(), model="gemini-3.5-flash", generate=fake
    )


@pytest.mark.asyncio
async def test_multi_place_sorted_by_confidence_desc() -> None:
    result = await _extract("multi_place.json")
    confidences = [p.confidence for p in result.places]
    assert confidences == sorted(confidences, reverse=True)
    names = {p.name for p in result.places}
    assert names == {"Ichiran Shibuya", "Meiji Shrine", "Blue Bottle Coffee"}


@pytest.mark.asyncio
async def test_empty_returns_empty_result_no_exception() -> None:
    result = await _extract("empty.json")
    assert result.places == []


@pytest.mark.asyncio
async def test_malformed_not_dict_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        await _extract("malformed_not_dict.json")


@pytest.mark.asyncio
async def test_missing_places_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        await _extract("missing_places.json")


@pytest.mark.asyncio
async def test_duplicates_collapse_keeping_higher_confidence() -> None:
    result = await _extract("duplicates.json")
    assert len(result.places) == 1
    assert result.places[0].confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_too_many_capped_at_fifteen_top_by_confidence() -> None:
    result = await _extract("too_many.json")
    assert len(result.places) == 15
    assert result.places[0].confidence == max(p.confidence for p in result.places)


@pytest.mark.asyncio
async def test_drift_fixture_is_corrected_leniently() -> None:
    result = await _extract("drift.json")
    assert len(result.places) == 1
    place = result.places[0]
    assert place.category == "other"
    assert place.confidence == 1.0
    assert place.area is None
    assert place.blurb is not None
    assert len(place.blurb) <= 200
    assert place.kind == "other"


@pytest.mark.asyncio
async def test_whitespace_only_name_is_dropped() -> None:
    response = {"places": [{"name": "   ", "confidence": 0.9, "kind": "place"}]}
    metadata = _metadata()
    fake = _fake_generate(response)
    result = await extract_places(
        metadata, trip_name="Tokyo test", client=object(), model="gemini-3.5-flash", generate=fake
    )
    assert result.places == []


@pytest.mark.asyncio
async def test_generate_receives_media_paths_and_prompt_content() -> None:
    response = load_fixture("gemini/multi_place.json")
    metadata = _metadata()
    fake = _fake_generate(response)
    await extract_places(
        metadata, trip_name="Tokyo test", client=object(), model="gemini-3.5-flash", generate=fake
    )
    call = fake.calls[0]
    assert call["paths"] == [metadata.video_path]
    prompt = call["prompt"]
    assert "Tokyo test" in prompt
    assert metadata.caption in prompt
    assert "0.9" in prompt
    assert "do not invent" in prompt.lower()


@pytest.mark.asyncio
async def test_carousel_metadata_adds_slide_note() -> None:
    response = {"places": []}
    metadata = _metadata(video_path=None, image_paths=[Path("1.jpg"), Path("2.jpg")])
    fake = _fake_generate(response)
    await extract_places(
        metadata, trip_name="Tokyo test", client=object(), model="gemini-3.5-flash", generate=fake
    )
    prompt = fake.calls[0]["prompt"]
    assert "slideshow" in prompt.lower() or "carousel" in prompt.lower()


def test_build_prompt_contains_key_phrases() -> None:
    prompt = build_prompt(
        caption="a caption", trip_name="Seoul trip", is_carousel=False, hashtags=["a", "b"]
    )
    assert "Seoul trip" in prompt
    assert "a caption" in prompt
    assert "do not invent" in prompt.lower()
    assert "0.9" in prompt
