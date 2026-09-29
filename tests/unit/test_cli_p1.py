import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gcreelmap.cli import main
from gcreelmap.pipeline.extract import ExtractedPlace
from gcreelmap.pipeline.process import ProcessOutcome
from gcreelmap.run_lock import RunLock
from gcreelmap.store.mentions import insert_mentions
from gcreelmap.store.reels import mark_done


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "GOOGLE_PLACES_API_KEY", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)


def _write_env(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\n{extra}", encoding="utf-8"
    )
    return env_file


def test_trip_new_prints_slug(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    exit_code = main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "tokyo-test-" in out


def test_trip_list_shows_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()

    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
            "--queue-only",
        ]
    )
    capsys.readouterr()

    main(["--env-file", str(env_file), "trip", "list"])
    out = capsys.readouterr().out
    assert "queued=1" in out


def test_add_reel_duplicate_prints_already_added(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _write_env(tmp_path)
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()

    url = "https://www.instagram.com/reel/abc123/"
    main(["--env-file", str(env_file), "add-reel", "1", url, "--queue-only"])
    capsys.readouterr()
    main(["--env-file", str(env_file), "add-reel", "1", url, "--queue-only"])
    out = capsys.readouterr().out
    assert "already added" in out


def test_add_reel_unsupported_url(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()

    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://example.com/not-a-reel",
            "--queue-only",
        ]
    )
    out = capsys.readouterr().out
    assert "unsupported: https://example.com/not-a-reel" in out


def test_queue_only_does_not_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_process_reel(*args: Any, **kwargs: Any) -> ProcessOutcome:
        nonlocal called
        called = True
        return ProcessOutcome(reel_id=1, status="done", error_code=None, places_found=0)

    monkeypatch.setattr("gcreelmap.cli.process_reel", fake_process_reel)

    env_file = _write_env(tmp_path, "GEMINI_API_KEY=test-key\n")
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()
    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
            "--queue-only",
        ]
    )
    capsys.readouterr()
    assert called is False


def test_add_reel_processes_and_prints_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_process_reel(
        conn: sqlite3.Connection,
        reel: Any,
        *,
        settings: Any,
        client: Any,
        clock: Any,
        **kwargs: Any,
    ) -> ProcessOutcome:
        insert_mentions(
            conn,
            reel.id,
            [
                ExtractedPlace(name="Ichiran", confidence=0.9, kind="place"),
                ExtractedPlace(name="A book", confidence=0.5, kind="other"),
            ],
            clock.now(),
        )
        mark_done(conn, reel.id, author="traveler_jane", now=clock.now())
        return ProcessOutcome(reel_id=reel.id, status="done", error_code=None, places_found=2)

    monkeypatch.setattr("gcreelmap.cli.process_reel", fake_process_reel)

    env_file = _write_env(tmp_path, "GEMINI_API_KEY=test-key\n")
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()
    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
        ]
    )
    out = capsys.readouterr().out
    assert "done: 1 places (1 skipped as non-places)" in out


def test_missing_gemini_api_key_gives_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _write_env(tmp_path)
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()
    exit_code = main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code != 0
    assert "gemini_api_key" in out.lower()


def test_held_lock_prints_suggestion_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _write_env(tmp_path, "GEMINI_API_KEY=test-key\n")
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()
    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
            "--queue-only",
        ]
    )
    capsys.readouterr()

    lock_path = tmp_path / "data" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with RunLock(lock_path):
        exit_code = main(
            [
                "--env-file",
                str(env_file),
                "add-reel",
                "1",
                "https://www.instagram.com/reel/xyz999/",
            ]
        )
    out = capsys.readouterr().out
    assert exit_code != 0
    assert "--queue-only" in out


def test_show_output_matches_expected_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_process_reel(
        conn: sqlite3.Connection,
        reel: Any,
        *,
        settings: Any,
        client: Any,
        clock: Any,
        **kwargs: Any,
    ) -> ProcessOutcome:
        insert_mentions(
            conn,
            reel.id,
            [
                ExtractedPlace(
                    name="Ichiran Shibuya",
                    area="Shibuya",
                    category="food",
                    blurb="Best ramen",
                    confidence=0.9,
                    kind="place",
                )
            ],
            clock.now(),
        )
        mark_done(conn, reel.id, author="traveler_jane", now=clock.now())
        return ProcessOutcome(reel_id=reel.id, status="done", error_code=None, places_found=1)

    monkeypatch.setattr("gcreelmap.cli.process_reel", fake_process_reel)

    env_file = _write_env(tmp_path, "GEMINI_API_KEY=test-key\n")
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()
    main(
        [
            "--env-file",
            str(env_file),
            "add-reel",
            "1",
            "https://www.instagram.com/reel/abc123/",
        ]
    )
    capsys.readouterr()

    main(["--env-file", str(env_file), "show", "1"])
    out = capsys.readouterr().out
    assert "Trip: Tokyo Test (tokyo-test-" in out
    assert "done=1" in out
    assert "[done] https://www.instagram.com/reel/abc123/ (by traveler_jane)" in out
    assert "Ichiran Shibuya | Shibuya | food | 0.90 | Best ramen" in out
