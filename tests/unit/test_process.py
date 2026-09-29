import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from google.genai import errors as genai_errors
from reelkit.exceptions import (
    DownloadError,
    DurationCapExceeded,
    ExtractionError,
    UnsupportedPlatformError,
)
from reelkit.models import ReelMetadata

from gcreelmap.config import Settings
from gcreelmap.pipeline.extract import ExtractedPlace, ExtractionResult
from gcreelmap.pipeline.process import ModelRetiredError, process_reel
from gcreelmap.store.collections import create_collection, get_collection
from gcreelmap.store.reels import claim_next_reel, enqueue_reel, get_reel
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource

_CAPTION_CANARY = "SECRET_CAPTION_TEXT_MUST_NOT_LEAK"


def _model_retired_error() -> genai_errors.ClientError:
    return genai_errors.ClientError(404, {"error": {"message": "model not found"}})


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "db.sqlite",
        download_temp_dir=tmp_path / "tmp",
        lock_path=tmp_path / "run.lock",
        log_level="INFO",
        gemini_model="gemini-3.5-flash",
        max_video_duration_seconds=120,
        ytdlp_cookies_file=None,
        ytdlp_cookies_from_browser=None,
        gemini_api_key="test-key",
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


def _temp_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"fake media")
    return path


def _make_metadata(tmp_path: Path) -> ReelMetadata:
    return ReelMetadata(
        source_url="https://instagram.com/reel/abc",
        platform="instagram",
        author="traveler_jane",
        posted_at=None,
        title=None,
        caption=_CAPTION_CANARY,
        video_path=_temp_file(tmp_path, "video.mp4"),
    )


@pytest.mark.asyncio
async def test_success_path(db: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            places=[
                ExtractedPlace(name="Ichiran", confidence=0.9, kind="place"),
                ExtractedPlace(name="A book", confidence=0.5, kind="other"),
            ]
        )

    outcome = await process_reel(
        db,
        reel,
        settings=_settings(tmp_path),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
    )

    assert outcome.status == "done"
    assert outcome.places_found == 2
    stored = get_reel(db, reel.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.author == "traveler_jane"
    collection = get_collection(db, reel.collection_id)
    assert collection is not None
    assert collection.version == 1
    assert metadata.video_path is not None and not metadata.video_path.exists()

    gemini_key = f"gemini_calls:{clock.now().strftime('%Y-%m-%d')}"
    row = db.execute("SELECT value FROM kv WHERE key = ?", (gemini_key,)).fetchone()
    assert row is not None
    assert row[0] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,expected_code",
    [
        (DurationCapExceeded(duration=200, cap=120), "duration_cap"),
        (UnsupportedPlatformError(url="https://example.com"), "unsupported"),
        (DownloadError(url="https://x", cause=RuntimeError("net")), "download_failed"),
        (ExtractionError(cause=RuntimeError("bad json")), "extraction_failed"),
    ],
)
async def test_exception_mapping(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path, exc: Exception, expected_code: str
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        raise exc

    outcome = await process_reel(
        db,
        reel,
        settings=_settings(tmp_path),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
    )
    assert outcome.error_code == expected_code
    assert metadata.video_path is not None and not metadata.video_path.exists()


@pytest.mark.asyncio
async def test_transient_failure_requeues_then_fails_at_attempt_three(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return _make_metadata(tmp_path)

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        raise DownloadError(url="https://x", cause=RuntimeError("net"))

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

    statuses = []
    for _ in range(3):
        reel = claim_next_reel(db, now=clock.now())
        assert reel is not None
        outcome = await process_reel(
            db,
            reel,
            settings=_settings(tmp_path),
            client=object(),
            clock=clock,
            fetch=fake_fetch,
            extract=fake_extract,
        )
        statuses.append(outcome.status)

    assert statuses == ["requeued", "requeued", "failed"]


@pytest.mark.asyncio
async def test_no_places_is_terminal_and_bumps_no_version(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(places=[])

    before = get_collection(db, reel.collection_id)
    assert before is not None
    outcome = await process_reel(
        db,
        reel,
        settings=_settings(tmp_path),
        client=object(),
        clock=clock,
        fetch=fake_fetch,
        extract=fake_extract,
    )
    assert outcome.status == "failed"
    assert outcome.error_code == "no_places"
    after = get_collection(db, reel.collection_id)
    assert after is not None
    assert after.version == before.version
    assert metadata.video_path is not None and not metadata.video_path.exists()


@pytest.mark.asyncio
async def test_model_retired_leaves_reel_queued_with_attempts_restored(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        raise _model_retired_error()

    with pytest.raises(ModelRetiredError):
        await process_reel(
            db,
            reel,
            settings=_settings(tmp_path),
            client=object(),
            clock=clock,
            fetch=fake_fetch,
            extract=fake_extract,
        )

    stored = get_reel(db, reel.id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.attempts == 0
    assert metadata.video_path is not None and not metadata.video_path.exists()


@pytest.mark.asyncio
async def test_cancelled_error_leaves_reel_processing(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    import asyncio

    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await process_reel(
            db,
            reel,
            settings=_settings(tmp_path),
            client=object(),
            clock=clock,
            fetch=fake_fetch,
            extract=fake_extract,
        )

    stored = get_reel(db, reel.id)
    assert stored is not None
    assert stored.status == "processing"


@pytest.mark.asyncio
async def test_internal_exception_is_recorded_and_loop_continues(
    db: sqlite3.Connection, clock: FakeClock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reel = _claimed_reel(db, clock)
    metadata = _make_metadata(tmp_path)

    async def fake_fetch(url: str, settings: Settings) -> ReelMetadata:
        return metadata

    async def fake_extract(*args: Any, **kwargs: Any) -> ExtractionResult:
        raise RuntimeError("totally unexpected")

    with caplog.at_level(logging.DEBUG):
        outcome = await process_reel(
            db,
            reel,
            settings=_settings(tmp_path),
            client=object(),
            clock=clock,
            fetch=fake_fetch,
            extract=fake_extract,
        )

    assert outcome.error_code == "internal"
    for record in caplog.records:
        assert _CAPTION_CANARY not in record.getMessage()
    assert metadata.video_path is not None and not metadata.video_path.exists()
