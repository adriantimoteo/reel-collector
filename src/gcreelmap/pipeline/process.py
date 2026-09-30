import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from reelkit.exceptions import (
    DownloadError,
    DurationCapExceeded,
    ExtractionError,
    UnsupportedPlatformError,
)
from reelkit.gemini import is_model_retired
from reelkit.models import ReelMetadata

from gcreelmap.config import Settings
from gcreelmap.domain.clock import Clock
from gcreelmap.domain.failure import ErrorCode
from gcreelmap.pipeline.extract import ExtractionResult, extract_places
from gcreelmap.pipeline.fetch import fetch_reel
from gcreelmap.pipeline.resolve.base import ResolverError, ResolverRegistry
from gcreelmap.pipeline.resolve_stage import LookupBudget, resolve_pending_mentions
from gcreelmap.store.collections import bump_version, get_collection
from gcreelmap.store.db import transaction
from gcreelmap.store.mentions import insert_mentions
from gcreelmap.store.reels import Reel, mark_done, record_outcome, requeue

logger = logging.getLogger(__name__)


class ModelRetiredError(Exception):
    pass


@dataclass(frozen=True)
class ProcessOutcome:
    reel_id: int
    status: str  # "done" | "requeued" | "failed"
    error_code: str | None
    places_found: int
    resolver_error: str | None = None


def _map_exception(exc: Exception) -> ErrorCode:
    if isinstance(exc, DurationCapExceeded):
        return ErrorCode.DURATION_CAP
    if isinstance(exc, UnsupportedPlatformError):  # covers UnsupportedCarouselError too
        return ErrorCode.UNSUPPORTED
    if isinstance(exc, DownloadError):
        return ErrorCode.DOWNLOAD_FAILED
    if isinstance(exc, ExtractionError):
        return ErrorCode.EXTRACTION_FAILED
    raise AssertionError(f"unmapped exception type: {type(exc)}")  # pragma: no cover


def _increment_gemini_counter(conn: sqlite3.Connection, now_date: str) -> None:
    key = f"gemini_calls:{now_date}"
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    current = int(row[0]) if row is not None else 0
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(current + 1)),
    )


async def process_reel(
    conn: sqlite3.Connection,
    reel: Reel,
    *,
    settings: Settings,
    client: Any,
    clock: Clock,
    fetch: Callable[[str, Settings], Awaitable[ReelMetadata]] = fetch_reel,
    extract: Callable[..., Awaitable[ExtractionResult]] = extract_places,
    registry: ResolverRegistry | None = None,
    budget: LookupBudget | None = None,
) -> ProcessOutcome:
    # Fresh instances per call unless the caller shares one across a batch --
    # mutable default arguments would leak state (registrations, run count)
    # across unrelated calls, since defaults are only evaluated once.
    if registry is None:
        registry = ResolverRegistry()
    if budget is None:
        budget = LookupBudget(max_per_run=150, max_per_day=300)

    collection = get_collection(conn, reel.collection_id)
    if collection is None:
        raise RuntimeError(f"reel {reel.id} references a missing collection {reel.collection_id}")

    metadata: ReelMetadata | None = None
    try:
        metadata = await fetch(reel.canonical_url, settings)

        _increment_gemini_counter(conn, clock.now().strftime("%Y-%m-%d"))
        result = await extract(
            metadata, trip_name=collection.name, client=client, model=settings.gemini_model
        )

        if not result.places:
            status = record_outcome(
                conn, reel, code=ErrorCode.NO_PLACES, detail="no places found", now=clock.now()
            )
            return ProcessOutcome(
                reel_id=reel.id,
                status=status,
                error_code=ErrorCode.NO_PLACES.value,
                places_found=0,
            )

        now = clock.now()
        with transaction(conn):
            insert_mentions(conn, reel.id, result.places, now)
            mark_done(conn, reel.id, author=metadata.author, now=now)
            bump_version(conn, reel.collection_id)

        resolver_error: str | None = None
        if settings.google_places_api_key is None:
            logger.info(
                "skipping place resolution for reel %d: GOOGLE_PLACES_API_KEY is unset", reel.id
            )
        else:
            try:
                await resolve_pending_mentions(
                    conn,
                    collection_id=reel.collection_id,
                    registry=registry,
                    budget=budget,
                    settings=settings,
                    clock=clock,
                )
            except ResolverError as exc:
                logger.warning("resolver error resolving places for reel %d: %s", reel.id, exc)
                resolver_error = str(exc)

        return ProcessOutcome(
            reel_id=reel.id,
            status="done",
            error_code=None,
            places_found=len(result.places),
            resolver_error=resolver_error,
        )
    except asyncio.CancelledError:
        raise
    except (DurationCapExceeded, UnsupportedPlatformError, DownloadError, ExtractionError) as exc:
        code = _map_exception(exc)
        status = record_outcome(conn, reel, code=code, detail=str(exc), now=clock.now())
        return ProcessOutcome(reel_id=reel.id, status=status, error_code=code.value, places_found=0)
    except Exception as exc:
        if is_model_retired(exc):
            conn.execute("UPDATE reels SET attempts = attempts - 1 WHERE id = ?", (reel.id,))
            requeue(conn, reel.id, code=ErrorCode.EXTRACTION_FAILED, detail="Gemini model retired")
            raise ModelRetiredError(str(exc)) from exc
        logger.exception("internal error processing reel %d (%s)", reel.id, reel.platform)
        status = record_outcome(
            conn, reel, code=ErrorCode.INTERNAL, detail=str(exc), now=clock.now()
        )
        return ProcessOutcome(
            reel_id=reel.id, status=status, error_code=ErrorCode.INTERNAL.value, places_found=0
        )
    finally:
        if metadata is not None:
            for path in metadata.temp_paths():
                try:
                    path.unlink(missing_ok=True)
                except OSError as unlink_exc:
                    logger.warning("failed to delete temp file %s: %s", path, unlink_exc)
