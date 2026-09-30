import argparse
import asyncio
import contextlib
import importlib.metadata
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import google.genai as genai
import httpx
from reelkit.exceptions import UnsupportedPlatformError
from reelkit.urls import resolve_canonical_url

from gcreelmap.config import ConfigError, Settings, load_settings, redact, resolve_db_path
from gcreelmap.domain.clock import SystemClock
from gcreelmap.domain.failure import ErrorCode, describe
from gcreelmap.domain.tokens import SecretsTokenSource
from gcreelmap.pipeline.process import ModelRetiredError, process_reel
from gcreelmap.pipeline.resolve.base import ResolverRegistry
from gcreelmap.pipeline.resolve.google_places import GooglePlacesResolver
from gcreelmap.pipeline.resolve_stage import LookupBudget, ResolveReport, resolve_pending_mentions
from gcreelmap.run_lock import RunLock, RunLockHeld
from gcreelmap.store.collections import (
    Collection,
    create_collection,
    get_by_slug,
    get_collection,
    list_collections,
)
from gcreelmap.store.db import connect
from gcreelmap.store.items import rebuild_items
from gcreelmap.store.migrate import migrate, pending_migrations
from gcreelmap.store.reels import Reel, claim_next_reel, enqueue_reel, list_reels

_SECRET_NOTES = (
    ("telegram_bot_token", "TELEGRAM_BOT_TOKEN", "P4 (Telegram bot)"),
    ("gemini_api_key", "GEMINI_API_KEY", "P1 (extraction)"),
    ("google_places_api_key", "GOOGLE_PLACES_API_KEY", "P2 (geocoding)"),
)


def _line(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def _check_db_directory(db_path: Path) -> bool:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".gcreelmap-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        _line("fail", f"DB directory {db_path.parent} is not writable: {exc}")
        return False
    return True


def _reelkit_commit() -> str | None:
    try:
        dist = importlib.metadata.distribution("reelkit")
        raw = dist.read_text("direct_url.json")
    except (importlib.metadata.PackageNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    top_level = cast(dict[str, object], data)
    vcs_info_raw = top_level.get("vcs_info")
    if not isinstance(vcs_info_raw, dict):
        return None
    vcs_info = cast(dict[str, object], vcs_info_raw)
    commit_id = vcs_info.get("commit_id")
    return commit_id if isinstance(commit_id, str) else None


def _check_importable(module_name: str) -> bool:
    try:
        __import__(module_name)
    except ImportError as exc:
        _line("fail", f"{module_name} not importable: {exc}")
        return False
    if module_name == "reelkit":
        try:
            version = importlib.metadata.version("reelkit")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        commit = _reelkit_commit()
        suffix = f", commit {commit}" if commit else ""
        _line("ok", f"reelkit importable (version {version}{suffix})")
    else:
        _line("ok", f"{module_name} importable")
    return True


def _run_doctor(env_file: str | None, no_migrate: bool) -> int:
    ok = True

    try:
        settings = load_settings(env_file=env_file)
    except ConfigError as exc:
        for problem in exc.problems:
            _line("fail", f"config: {problem}")
        db_path = resolve_db_path(env_file=env_file)
        _check_db_directory(db_path)
        return 1
    else:
        _line("ok", "config loaded")

    if not _check_db_directory(settings.db_path):
        ok = False
    else:
        conn = connect(settings.db_path)
        try:
            if no_migrate:
                pending = pending_migrations(conn)
                if pending:
                    versions = ", ".join(str(m.version) for m in pending)
                    _line("ok", f"database pending migrations: {versions}")
                else:
                    _line("ok", "database up to date")
            else:
                try:
                    applied = migrate(conn, db_path=settings.db_path)
                except Exception as exc:  # noqa: BLE001 - surfaced as a doctor failure, not a crash
                    _line("fail", f"migration failed: {exc}")
                    ok = False
                else:
                    if applied:
                        versions = ", ".join(str(v) for v in applied)
                        _line("ok", f"database migrated: applied {versions}")
                    else:
                        _line("ok", "database up to date")
        finally:
            conn.close()

    try:
        with RunLock(settings.lock_path):
            pass
    except RunLockHeld:
        _line("fail", "run lock held by another process")
        ok = False
    else:
        _line("ok", "run lock acquirable")

    for module_name in ("reelkit", "yt_dlp", "gallery_dl"):
        if not _check_importable(module_name):
            ok = False

    for field_name, env_name, needed_by in _SECRET_NOTES:
        if getattr(settings, field_name) is None:
            _line("warn", f"{env_name} is unset (needed for {needed_by})")
        else:
            _line("ok", f"{env_name} set ({redact(getattr(settings, field_name))})")

    _line(
        "ok",
        "places ceilings: "
        f"max_per_run={settings.places_max_lookups_per_run} "
        f"max_per_day={settings.places_max_lookups_per_day} "
        f"cache_ttl_days={settings.geocode_cache_ttl_days} "
        f"negative_ttl_days={settings.geocode_negative_ttl_days} "
        f"bias_radius_m={settings.places_bias_radius_m}",
    )

    return 0 if ok else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    return _run_doctor(args.env_file, args.no_migrate)


def _reel_counts(conn: sqlite3.Connection, collection_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM reels WHERE collection_id = ? GROUP BY status",
        (collection_id,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _resolve_trip(conn: sqlite3.Connection, identifier: str) -> Collection | None:
    if identifier.isdigit():
        collection = get_collection(conn, int(identifier))
        if collection is not None:
            return collection
    return get_by_slug(conn, identifier)


def _connect_and_migrate(settings: Settings) -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    migrate(conn, db_path=settings.db_path)
    return conn


def _cmd_trip_new(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    conn = _connect_and_migrate(settings)
    try:
        collection = create_collection(
            conn,
            owner_type="user",
            owner_id=0,
            name=args.name,
            now=SystemClock().now(),
            tokens=SecretsTokenSource(),
        )
        print(f"{collection.id}\t{collection.slug}")
    finally:
        conn.close()
    return 0


def _cmd_trip_list(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    conn = _connect_and_migrate(settings)
    try:
        collections = list_collections(conn)
        if not collections:
            print("No trips yet.")
            return 0
        for collection in collections:
            counts = _reel_counts(conn, collection.id)
            counts_text = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
            print(f"{collection.id}\t{collection.slug}\t{collection.name}\t{counts_text or 'none'}")
    finally:
        conn.close()
    return 0


def _print_reel_outcome(
    conn: sqlite3.Connection, reel: Reel, status: str, error_code: str | None
) -> None:
    if status == "done":
        rows = conn.execute(
            "SELECT resolution_status, COUNT(*) FROM item_mentions WHERE reel_id = ? "
            "GROUP BY resolution_status",
            (reel.id,),
        ).fetchall()
        counts = {row[0]: row[1] for row in rows}
        places = counts.get("pending", 0)
        skipped = counts.get("skipped", 0)
        print(f"done: {places} places ({skipped} skipped as non-places) {reel.canonical_url}")
        return
    code = error_code or ErrorCode.INTERNAL.value
    try:
        description = describe(ErrorCode(code))
    except ValueError:
        description = "an internal error occurred"
    print(f"{status}: {code} — {description} {reel.canonical_url}")


async def _process_queued_reels(
    conn: sqlite3.Connection, collection: Collection, settings: Settings, client: genai.Client
) -> None:
    clock = SystemClock()
    registry = ResolverRegistry()
    budget = LookupBudget(settings.places_max_lookups_per_run, settings.places_max_lookups_per_day)
    async with httpx.AsyncClient(timeout=15.0) as http:
        if settings.google_places_api_key is not None:
            registry.register("place", GooglePlacesResolver(settings.google_places_api_key, http))

        while True:
            reel = claim_next_reel(conn, now=clock.now(), collection_id=collection.id)
            if reel is None:
                return
            try:
                outcome = await process_reel(
                    conn,
                    reel,
                    settings=settings,
                    client=client,
                    clock=clock,
                    registry=registry,
                    budget=budget,
                )
            except ModelRetiredError:
                print(
                    "gemini model retired: update GEMINI_MODEL "
                    f"(currently {settings.gemini_model}) — stopping"
                )
                return
            _print_reel_outcome(conn, reel, outcome.status, outcome.error_code)
            if outcome.resolver_error:
                print(f"places resolver error: {outcome.resolver_error}")


def _cmd_add_reel(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    conn = _connect_and_migrate(settings)
    try:
        collection = _resolve_trip(conn, args.trip)
        if collection is None:
            print(f"no such trip: {args.trip}")
            return 1

        now = SystemClock().now()
        any_queued = False
        for url in args.urls:
            try:
                canonical_url, platform = asyncio.run(resolve_canonical_url(url))
            except UnsupportedPlatformError:
                print(f"unsupported: {url}")
                continue
            _reel_id, created = enqueue_reel(
                conn,
                collection_id=collection.id,
                canonical_url=canonical_url,
                platform=platform,
                submitted_by=None,
                source_message_id=None,
                now=now,
            )
            if created:
                any_queued = True
            else:
                print(f"already added: {canonical_url}")

        if args.queue_only or not any_queued:
            return 0

        try:
            settings.require("gemini_api_key")
        except ConfigError as exc:
            for problem in exc.problems:
                print(f"error: {problem}")
            return 1

        client = genai.Client(api_key=settings.gemini_api_key)
        try:
            with RunLock(settings.lock_path):
                asyncio.run(_process_queued_reels(conn, collection, settings, client))
        except RunLockHeld:
            print("another run holds the lock; use --queue-only to enqueue without processing")
            return 1
    finally:
        conn.close()
    return 0


def _print_mentions_view(conn: sqlite3.Connection, collection: Collection) -> None:
    for reel in list_reels(conn, collection.id):
        if reel.status == "failed" and reel.error_code:
            header = f"[failed: {reel.error_code}]"
            with contextlib.suppress(ValueError):
                header += f" {describe(ErrorCode(reel.error_code))}"
            print(f"{header} {reel.canonical_url}")
        else:
            author_text = f" (by {reel.author})" if reel.author else ""
            print(f"[{reel.status}] {reel.canonical_url}{author_text}")

        mention_rows = conn.execute(
            "SELECT raw_name, raw_area, raw_category, confidence, raw_blurb "
            "FROM item_mentions WHERE reel_id = ? ORDER BY id",
            (reel.id,),
        ).fetchall()
        for name, area, category, confidence, blurb in mention_rows:
            area_text = area or ""
            category_text = category or ""
            blurb_text = blurb or ""
            print(f"  - {name} | {area_text} | {category_text} | {confidence:.2f} | {blurb_text}")
        print()


def _mapped_review_pending_counts(
    conn: sqlite3.Connection, collection_id: int
) -> tuple[int, int, int]:
    mapped = conn.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id = ? AND needs_review = 0", (collection_id,)
    ).fetchone()[0]
    review = conn.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id = ? AND needs_review = 1", (collection_id,)
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? AND m.kind = 'place' AND m.resolution_status = 'pending'",
        (collection_id,),
    ).fetchone()[0]
    return mapped, review, pending


def _item_primary_source(conn: sqlite3.Connection, item_id: int) -> tuple[str, str | None] | None:
    row = conn.execute(
        "SELECT r.canonical_url, m.raw_blurb FROM item_mentions m "
        "JOIN reels r ON r.id = m.reel_id WHERE m.item_id = ? "
        "ORDER BY m.confidence DESC, m.id ASC LIMIT 1",
        (item_id,),
    ).fetchone()
    return (row[0], row[1]) if row is not None else None


def _print_ranked_view(conn: sqlite3.Connection, collection: Collection) -> None:
    mapped, review, pending = _mapped_review_pending_counts(conn, collection.id)
    reel_counts = _reel_counts(conn, collection.id)
    done = reel_counts.get("done", 0)
    failed = reel_counts.get("failed", 0)
    queued = reel_counts.get("queued", 0) + reel_counts.get("processing", 0)
    print(
        f"Reels: {done} done, {failed} failed, {queued} queued | "
        f"Places: {mapped} mapped, {review} need review, {pending} pending lookup"
    )
    print()

    ranked = conn.execute(
        "SELECT id, name, mention_count, category, lat, lng, address, canonical_id "
        "FROM items WHERE collection_id = ? AND needs_review = 0 "
        "ORDER BY mention_count DESC, last_mentioned_at DESC, name ASC",
        (collection.id,),
    ).fetchall()
    for rank, (item_id, name, count, category, lat, lng, address, canonical_id) in enumerate(
        ranked, start=1
    ):
        print(f" {rank}. {name}   x{count}  [{category or 'other'}]  {lat:.5f},{lng:.5f}")
        place_id_text = f"  (place id {canonical_id})" if canonical_id else ""
        if address or place_id_text:
            print(f"    {address or ''}{place_id_text}")
        source = _item_primary_source(conn, item_id)
        if source is not None:
            url, blurb = source
            extra = count - 1
            suffix = f"  (+{extra} more reel{'s' if extra != 1 else ''})" if extra > 0 else ""
            blurb_text = f'"{blurb}" — ' if blurb else ""
            print(f"    {blurb_text}{url}{suffix}")

    review_rows = conn.execute(
        "SELECT id, name, review_reason FROM items WHERE collection_id = ? AND needs_review = 1 "
        "ORDER BY mention_count DESC, last_mentioned_at DESC, name ASC",
        (collection.id,),
    ).fetchall()
    if review_rows:
        print()
        print(f"Needs review ({len(review_rows)}):")
        for item_id, name, review_reason in review_rows:
            area_row = conn.execute(
                "SELECT raw_area FROM item_mentions WHERE item_id = ? "
                "ORDER BY confidence DESC, id ASC LIMIT 1",
                (item_id,),
            ).fetchone()
            area_text = f" ({area_row[0]})" if area_row and area_row[0] else ""
            source = _item_primary_source(conn, item_id)
            source_text = f'  "{source[1]}" — {source[0]}' if source and source[1] else ""
            print(f"  - {name}{area_text} [{review_reason}]{source_text}")

    failed_reels = [
        r for r in list_reels(conn, collection.id) if r.status == "failed" and r.error_code
    ]
    if failed_reels:
        print()
        print(f"Failed reels ({len(failed_reels)}):")
        for reel in failed_reels:
            description = ""
            with contextlib.suppress(ValueError):
                if reel.error_code:
                    description = f" — {describe(ErrorCode(reel.error_code))}"
            print(f"  - {reel.canonical_url}: {reel.error_code}{description}")


def _print_stats(conn: sqlite3.Connection, settings: Settings) -> None:
    today = SystemClock().now().strftime("%Y-%m-%d")
    gemini_row = conn.execute(
        "SELECT value FROM kv WHERE key = ?", (f"gemini_calls:{today}",)
    ).fetchone()
    places_row = conn.execute(
        "SELECT value FROM kv WHERE key = ?", (f"places_lookups:{today}",)
    ).fetchone()
    gemini_calls = int(gemini_row[0]) if gemini_row else 0
    places_lookups = int(places_row[0]) if places_row else 0
    cache_rows = conn.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0]
    print()
    print(
        f"Stats: Gemini calls today={gemini_calls}; "
        f"Places lookups today={places_lookups}/{settings.places_max_lookups_per_day}; "
        f"geocode cache rows={cache_rows}"
    )


def _cmd_show(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    conn = _connect_and_migrate(settings)
    try:
        collection = _resolve_trip(conn, args.trip)
        if collection is None:
            print(f"no such trip: {args.trip}")
            return 1

        print(f"Trip: {collection.name} ({collection.slug})")
        if args.mentions:
            print()
            _print_mentions_view(conn, collection)
        else:
            _print_ranked_view(conn, collection)

        if args.stats:
            _print_stats(conn, settings)
    finally:
        conn.close()
    return 0


def _cmd_rebuild(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    conn = _connect_and_migrate(settings)
    try:
        collection = _resolve_trip(conn, args.trip)
        if collection is None:
            print(f"no such trip: {args.trip}")
            return 1
        result = rebuild_items(conn, collection.id, now=SystemClock().now())
        print("changed" if result.changed else "unchanged")
    finally:
        conn.close()
    return 0


async def _run_resolve_pending(
    conn: sqlite3.Connection, collection: Collection, settings: Settings
) -> ResolveReport:
    api_key = settings.google_places_api_key
    if api_key is None:
        raise RuntimeError("google_places_api_key must be set before calling _run_resolve_pending")
    registry = ResolverRegistry()
    budget = LookupBudget(settings.places_max_lookups_per_run, settings.places_max_lookups_per_day)
    async with httpx.AsyncClient(timeout=15.0) as http:
        registry.register("place", GooglePlacesResolver(api_key, http))
        return await resolve_pending_mentions(
            conn,
            collection_id=collection.id,
            registry=registry,
            budget=budget,
            settings=settings,
            clock=SystemClock(),
        )


def _cmd_resolve_pending(args: argparse.Namespace) -> int:
    settings = load_settings(env_file=args.env_file)
    try:
        settings.require("google_places_api_key")
    except ConfigError as exc:
        for problem in exc.problems:
            print(f"error: {problem}")
        return 1

    conn = _connect_and_migrate(settings)
    try:
        collection = _resolve_trip(conn, args.trip)
        if collection is None:
            print(f"no such trip: {args.trip}")
            return 1
        report = asyncio.run(_run_resolve_pending(conn, collection, settings))
        print(
            f"resolved={report.resolved} unresolved={report.unresolved} "
            f"still_pending={report.still_pending} lookups={report.lookups} "
            f"budget_exhausted={report.budget_exhausted}"
        )
    finally:
        conn.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcreelmap")
    parser.add_argument("--env-file", default=".env", help="Path to a .env file (default: .env)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check the local setup is healthy")
    doctor.add_argument(
        "--no-migrate",
        action="store_true",
        help="Report pending migrations without applying them",
    )
    doctor.set_defaults(func=_cmd_doctor)

    trip = subparsers.add_parser("trip", help="Manage trips")
    trip_subparsers = trip.add_subparsers(dest="trip_command", required=True)

    trip_new = trip_subparsers.add_parser("new", help="Create a new trip")
    trip_new.add_argument("name", help="Trip name")
    trip_new.set_defaults(func=_cmd_trip_new)

    trip_list = trip_subparsers.add_parser("list", help="List trips")
    trip_list.set_defaults(func=_cmd_trip_list)

    add_reel = subparsers.add_parser("add-reel", help="Queue (and process) reel URLs for a trip")
    add_reel.add_argument("trip", help="Trip id or slug")
    add_reel.add_argument("urls", nargs="+", help="One or more reel URLs")
    add_reel.add_argument(
        "--queue-only", action="store_true", help="Enqueue the URLs without processing them"
    )
    add_reel.set_defaults(func=_cmd_add_reel)

    show = subparsers.add_parser("show", help="Show a trip's ranked places (or raw mentions)")
    show.add_argument("trip", help="Trip id or slug")
    show.add_argument(
        "--mentions",
        action="store_true",
        help="Show the raw per-reel mentions view instead of the ranked view",
    )
    show.add_argument(
        "--stats", action="store_true", help="Also print today's Gemini/Places usage and cache size"
    )
    show.set_defaults(func=_cmd_show)

    rebuild = subparsers.add_parser(
        "rebuild", help="Rebuild a trip's merged items from its mentions"
    )
    rebuild.add_argument("trip", help="Trip id or slug")
    rebuild.set_defaults(func=_cmd_rebuild)

    resolve_pending = subparsers.add_parser(
        "resolve-pending", help="Resolve a trip's still-pending place mentions"
    )
    resolve_pending.add_argument("trip", help="Trip id or slug")
    resolve_pending.set_defaults(func=_cmd_resolve_pending)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
