import argparse
import importlib.metadata
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from gcreelmap.config import ConfigError, load_settings, redact, resolve_db_path
from gcreelmap.run_lock import RunLock, RunLockHeld
from gcreelmap.store.db import connect
from gcreelmap.store.migrate import migrate, pending_migrations

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

    return 0 if ok else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    return _run_doctor(args.env_file, args.no_migrate)


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
