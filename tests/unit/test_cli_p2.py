from pathlib import Path

import pytest

from gcreelmap.cli import main
from gcreelmap.config import ConfigError, load_settings
from gcreelmap.domain.failure import ErrorCode
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.store.db import connect
from gcreelmap.store.migrate import migrate
from gcreelmap.store.reels import enqueue_reel, mark_done, mark_failed
from tests.support.clock import FakeClock


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_PLACES_API_KEY",
        "LOG_LEVEL",
        "MAX_VIDEO_DURATION_SECONDS",
        "PLACES_MAX_LOOKUPS_PER_RUN",
        "PLACES_MAX_LOOKUPS_PER_DAY",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_env(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\n{extra}", encoding="utf-8"
    )
    return env_file


def _seed_mention(
    conn,
    reel_id,
    *,
    raw_name,
    match_score,
    confidence,
    canonical_id,
    lat,
    lng,
    now,
    category="food",
    blurb="great",
):
    conn.execute(
        "INSERT INTO item_mentions (reel_id, kind, raw_name, raw_category, raw_blurb, confidence, "
        "resolution_status, match_score, resolved_provider, resolved_canonical_id, resolved_name, "
        "resolved_address, resolved_lat, resolved_lng, created_at) "
        "VALUES (?, 'place', ?, ?, ?, ?, 'resolved', ?, 'google', ?, ?, '1 Main St', ?, ?, ?)",
        (
            reel_id,
            raw_name,
            category,
            blurb,
            confidence,
            match_score,
            canonical_id,
            raw_name,
            lat,
            lng,
            utc_iso(now),
        ),
    )


def _seed_trip_with_items(tmp_path: Path, env_file: Path) -> None:
    settings = load_settings(env_file=env_file)
    conn = connect(settings.db_path)
    migrate(conn, db_path=settings.db_path)
    clock = FakeClock()
    from gcreelmap.store.collections import create_collection
    from tests.support.tokens import FakeTokenSource

    collection = create_collection(
        conn,
        owner_type="user",
        owner_id=0,
        name="Tokyo Test",
        now=clock.now(),
        tokens=FakeTokenSource(),
    )

    # Item A: mentioned twice (higher rank).
    reel1, _ = enqueue_reel(
        conn,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/1",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_done(conn, reel1, author=None, now=clock.now())
    _seed_mention(
        conn,
        reel1,
        raw_name="Ichiran Shibuya",
        match_score=0.9,
        confidence=0.9,
        canonical_id="A",
        lat=35.0,
        lng=139.0,
        now=clock.now(),
    )
    reel2, _ = enqueue_reel(
        conn,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/2",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_done(conn, reel2, author=None, now=clock.now())
    _seed_mention(
        conn,
        reel2,
        raw_name="Ichiran Shibuya",
        match_score=0.9,
        confidence=0.9,
        canonical_id="A",
        lat=35.0,
        lng=139.0,
        now=clock.now(),
    )

    # Item B: mentioned once (lower rank).
    reel3, _ = enqueue_reel(
        conn,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/3",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_done(conn, reel3, author=None, now=clock.now())
    _seed_mention(
        conn,
        reel3,
        raw_name="Meiji Shrine",
        match_score=0.9,
        confidence=0.9,
        canonical_id="B",
        lat=10.0,
        lng=10.0,
        now=clock.now(),
    )

    # Needs-review item (low confidence).
    reel4, _ = enqueue_reel(
        conn,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/4",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_done(conn, reel4, author=None, now=clock.now())
    _seed_mention(
        conn,
        reel4,
        raw_name="Sketchy Place",
        match_score=0.9,
        confidence=0.2,
        canonical_id="C",
        lat=1.0,
        lng=1.0,
        now=clock.now(),
    )

    # Failed reel.
    reel5, _ = enqueue_reel(
        conn,
        collection_id=collection.id,
        canonical_url="https://instagram.com/reel/5",
        platform="instagram",
        submitted_by=None,
        source_message_id=None,
        now=clock.now(),
    )
    mark_failed(conn, reel5, code=ErrorCode.DOWNLOAD_FAILED, detail="net error", now=clock.now())

    conn.close()


def test_show_ranked_output_order_and_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _write_env(tmp_path)
    _seed_trip_with_items(tmp_path, env_file)
    main(["--env-file", str(env_file), "rebuild", "1"])
    capsys.readouterr()

    main(["--env-file", str(env_file), "show", "1"])
    out = capsys.readouterr().out

    assert "Trip: Tokyo Test (tokyo-test-" in out
    assert "Places: 2 mapped, 1 need review, 0 pending lookup" in out
    ichiran_pos = out.index("Ichiran Shibuya")
    meiji_pos = out.index("Meiji Shrine")
    assert ichiran_pos < meiji_pos  # higher mention_count ranks first
    assert " 1. Ichiran Shibuya   x2" in out
    assert " 2. Meiji Shrine   x1" in out
    assert "Needs review (1):" in out
    assert "Sketchy Place" in out
    assert "Failed reels (1):" in out
    assert "download_failed" in out


def test_show_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    _seed_trip_with_items(tmp_path, env_file)
    main(["--env-file", str(env_file), "rebuild", "1"])
    capsys.readouterr()

    main(["--env-file", str(env_file), "show", "1", "--stats"])
    out = capsys.readouterr().out
    assert "Stats: Gemini calls today=" in out
    assert "Places lookups today=" in out
    assert "geocode cache rows=" in out


def test_rebuild_changed_then_unchanged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    _seed_trip_with_items(tmp_path, env_file)

    main(["--env-file", str(env_file), "rebuild", "1"])
    out = capsys.readouterr().out
    assert out.strip() == "changed"

    main(["--env-file", str(env_file), "rebuild", "1"])
    out2 = capsys.readouterr().out
    assert out2.strip() == "unchanged"


def test_resolve_pending_requires_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = _write_env(tmp_path)
    main(["--env-file", str(env_file), "trip", "new", "Tokyo Test"])
    capsys.readouterr()

    exit_code = main(["--env-file", str(env_file), "resolve-pending", "1"])
    out = capsys.readouterr().out
    assert exit_code != 0
    assert "google_places_api_key" in out.lower()


def test_settings_validation_reports_several_bad_values_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            env={
                "PLACES_MAX_LOOKUPS_PER_RUN": "not-a-number",
                "PLACES_BIAS_RADIUS_M": "-5",
            },
            env_file=None,
        )
    problems = excinfo.value.problems
    assert len(problems) == 2
    assert any("PLACES_MAX_LOOKUPS_PER_RUN" in p for p in problems)
    assert any("PLACES_BIAS_RADIUS_M" in p for p in problems)
