from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from pydantic import BaseModel, Field, ValidationError, field_validator
from reelkit.exceptions import ExtractionError
from reelkit.gemini import generate_structured, is_carousel, media_paths
from reelkit.models import ReelMetadata

_CATEGORIES = (
    "food",
    "cafe",
    "bar",
    "attraction",
    "nature",
    "shopping",
    "lodging",
    "activity",
    "other",
)
_KINDS = ("place", "other")
_MAX_PLACES = 15

PLACES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "city_country": {"type": "string"},
                    "category": {"type": "string", "enum": list(_CATEGORIES)},
                    "blurb": {"type": "string"},
                    "confidence": {"type": "number"},
                    "kind": {"type": "string", "enum": list(_KINDS)},
                },
                "required": ["name", "kind", "confidence"],
            },
        },
    },
    "required": ["places"],
}


class ExtractedPlace(BaseModel):
    name: str
    area: str | None = None
    city_country: str | None = None
    category: str = "other"
    blurb: str | None = None
    confidence: float
    kind: str = "other"

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, v: object) -> str:
        text = str(v).strip() if v is not None else ""
        return text[:200]

    @field_validator("area", "city_country", mode="before")
    @classmethod
    def _clean_optional_text(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("category", mode="before")
    @classmethod
    def _clean_category(cls, v: object) -> str:
        text = str(v).strip().lower() if v is not None else ""
        return text if text in _CATEGORIES else "other"

    @field_validator("blurb", mode="before")
    @classmethod
    def _clean_blurb(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()[:200]
        return text or None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> float:
        try:
            value = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            value = 0.0
        return max(0.0, min(1.0, value))

    @field_validator("kind", mode="before")
    @classmethod
    def _clean_kind(cls, v: object) -> str:
        text = str(v).strip().lower() if v is not None else ""
        return text if text in _KINDS else "other"


class ExtractionResult(BaseModel):
    places: list[ExtractedPlace] = Field(default_factory=list[ExtractedPlace])


_BASE_PROMPT = (
    "You are extracting travel places from a single social media reel or post.\n"
    'The group\'s trip is called "{trip_name}" -- treat this only as a location hint '
    "(the name may be generic and should never override what the media actually shows).\n"
    "{carousel_note}"
    "List every place, business, attraction or location that is explicitly named or clearly "
    "identifiable in the media (video, images, spoken audio, on-screen text) or the caption "
    "below. Do not invent places and do not pad the list -- if nothing is identifiable, "
    "return an empty list.\n"
    "For each place, include an `area` (neighbourhood/district) and `city_country` whenever "
    "you can infer them from context.\n"
    "Set `confidence` using these tiers: 0.9-1.0 if the name is clearly shown on screen or "
    "clearly said aloud; 0.5-0.8 if it is partially legible, briefly shown, or somewhat "
    "ambiguous; below 0.5 if you are guessing.\n"
    "If the reel recommends something that is not a place (a book, a product, a recipe, "
    'etc.), still include it but set `kind` to "other" instead of "place".\n'
    "Write a `blurb` of at most 140 characters explaining why the place is recommended, "
    "based only on what the reel actually says or shows.\n"
    "Caption: {caption}\n"
    "Hashtags: {hashtags}\n"
)

_CAROUSEL_NOTE = (
    "This is a photo slideshow / carousel post -- the images given to you are consecutive "
    "slides from the same post, in order.\n"
)


def build_prompt(
    *, caption: str | None, trip_name: str, is_carousel: bool, hashtags: Sequence[str]
) -> str:
    return _BASE_PROMPT.format(
        trip_name=trip_name,
        carousel_note=_CAROUSEL_NOTE if is_carousel else "",
        caption=caption or "(no caption)",
        hashtags=" ".join(f"#{h}" for h in hashtags) if hashtags else "(none)",
    )


def _normalize_name_key(name: str) -> str:
    return " ".join(name.casefold().split())


async def extract_places(
    metadata: ReelMetadata,
    *,
    trip_name: str,
    client: Any,
    model: str,
    # Deliberately Any, not dict[str, Any]: the return shape is untrusted LLM
    # output validated just below, not something the type checker should assume.
    generate: Callable[..., Awaitable[Any]] = generate_structured,
) -> ExtractionResult:
    prompt = build_prompt(
        caption=metadata.caption,
        trip_name=trip_name,
        is_carousel=is_carousel(metadata),
        hashtags=metadata.hashtags,
    )
    raw = await generate(
        client,
        model,
        media_paths(metadata),
        prompt,
        PLACES_SCHEMA,
        description=f"places for {metadata.source_url}",
    )

    if not isinstance(raw, dict):
        raise ExtractionError(cause=TypeError(f"expected a dict, got {type(raw).__name__}"))
    raw_dict = cast(dict[str, object], raw)
    raw_places = raw_dict.get("places")
    if not isinstance(raw_places, list):
        raise ExtractionError(cause=TypeError("'places' is missing or not a list"))
    raw_places_list = cast(list[object], raw_places)

    parsed: list[ExtractedPlace] = []
    for item in raw_places_list:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(ExtractedPlace.model_validate(item))
        except ValidationError:
            continue

    if raw_places and not parsed:
        raise ExtractionError(cause=ValueError("every item in 'places' failed validation"))

    parsed = [place for place in parsed if place.name]

    best_by_key: dict[str, ExtractedPlace] = {}
    for place in parsed:
        key = _normalize_name_key(place.name)
        current = best_by_key.get(key)
        if current is None or place.confidence > current.confidence:
            best_by_key[key] = place

    deduped = sorted(best_by_key.values(), key=lambda p: p.confidence, reverse=True)
    return ExtractionResult(places=deduped[:_MAX_PLACES])
