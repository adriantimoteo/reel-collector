import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from reelkit.models import ReelMetadata

from gcreelmap.config import Settings
from gcreelmap.pipeline.extract import ExtractedPlace, ExtractionResult
from gcreelmap.pipeline.process import process_reel
from gcreelmap.pipeline.resolve.base import (
    ResolvedPlace,
    ResolveQuery,
    ResolverError,
    ResolverRegistry,
)
from gcreelmap.pipeline.resolve_stage import LookupBudget
from gcreelmap.store.collections import create_collection, get_collection
from gcreelmap.store.reels import claim_next_reel, enqueue_reel
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def _settings(tmp_path: Path, *, places_key: str | None) -> Settings:
    return Settings(
        db_path=tmp_path / "db.sqlite",
        download_temp_dir=tmp_path / "tmp",
        lock_path=tmp_path / "run.lock",
        log_level="INFO",
        gemini_model="gemini-3.5-flash",
        max_video_duration_seconds=120,
        ytdlp_cookies_file=None,
        ytdlp_cookies_from_browser=None,
        places_max_lookups_per_run=150,
        places_max_lookups_per_day=300,
        geocode_cache_ttl_days=30,
        geocode_negative_ttl_days=7,
        places_bias_radius_m=50_000,
        gemini_api_key="test-key",
        google_places_api_key=places_key,
    )


def _claimed_reel(db: sqlite3.Connection, clock: FakeClock):
    collection = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=FakeTokenSource()
    )
    enqueue_reel(
        db,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/abc",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    reel = claim_next_reel(db, now=clock.now())
    assert reel is not None
    return reel


def _metadata(tmp_path: Path) -> ReelMetadata:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    return ReelMetadata(
        source_url="https://instagram.com/reel/abc",
        platform="instagram",
        author="traveler_jane",
        posted_at=None,
        title=None,
        caption="caption",
        video_path=video,
    )


class _AlwaysResolves:
    provider = "google"

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        return ResolvedPlace(
            provider="google",
            canonical_id="X",
            name=q.name,
            address="1 Main St",
            lat=35.0,
            lng=139.0,
            match_score=0.95,
        )


class _AlwaysErrors:
    provider = "google"

    async def resolve(self, q: ResolveQuery) -> ResolvedPlace | None:
        raise ResolverError("Places API rejected the key")


@pytest.mark.asyncio
async def test_process_reel_ends_with_items_present(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            places=[ExtractedPlace(name="Ichiran", confidence=0.9, kind="place")]
        )

    registry = ResolverRegistry()
    registry.register("place", _AlwaysResolves())

    outcome = await process_reel(
        db,
        reel,
        settings=_settings(tmp_path, places_key="test-places-key"),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
        registry=registry,
        budget=LookupBudget(150, 300),
    )
    assert outcome.status == "done"
    assert outcome.resolver_error is None
    items = db.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id = ?", (reel.collection_id,)
    ).fetchone()[0]
    assert items == 1


@pytest.mark.asyncio
async def test_resolver_error_leaves_reel_done_mentions_pending(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            places=[ExtractedPlace(name="Ichiran", confidence=0.9, kind="place")]
        )

    registry = ResolverRegistry()
    registry.register("place", _AlwaysErrors())

    outcome = await process_reel(
        db,
        reel,
        settings=_settings(tmp_path, places_key="test-places-key"),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
        registry=registry,
        budget=LookupBudget(150, 300),
    )
    assert outcome.status == "done"
    assert outcome.resolver_error is not None
    row = db.execute(
        "SELECT resolution_status FROM item_mentions WHERE reel_id = ?", (reel.id,)
    ).fetchone()
    assert row[0] == "pending"


@pytest.mark.asyncio
async def test_missing_places_key_logs_and_skips_resolution(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            places=[ExtractedPlace(name="Ichiran", confidence=0.9, kind="place")]
        )

    with caplog.at_level(logging.INFO):
        outcome = await process_reel(
            db,
            reel,
            settings=_settings(tmp_path, places_key=None),
            client=object(),
            clock=clock,
            fetch=fake_fetch,
            extract=fake_extract,
        )
    assert outcome.status == "done"
    row = db.execute(
        "SELECT resolution_status FROM item_mentions WHERE reel_id = ?", (reel.id,)
    ).fetchone()
    assert row[0] == "pending"
    assert any("GOOGLE_PLACES_API_KEY" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_version_increases(db: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _metadata(tmp_path)
    before = get_collection(db, reel.collection_id)
    assert before is not None

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            places=[ExtractedPlace(name="Ichiran", confidence=0.9, kind="place")]
        )

    registry = ResolverRegistry()
    registry.register("place", _AlwaysResolves())

    await process_reel(
        db,
        reel,
        settings=_settings(tmp_path, places_key="test-places-key"),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
        registry=registry,
        budget=LookupBudget(150, 300),
    )
    after = get_collection(db, reel.collection_id)
    assert after is not None
    assert after.version > before.version
