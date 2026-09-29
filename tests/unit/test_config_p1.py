from pathlib import Path

import pytest

from gcreelmap.config import ConfigError, load_settings


def test_defaults_for_new_settings() -> None:
    settings = load_settings(env={}, env_file=None)
    assert settings.gemini_model == "gemini-3.5-flash"
    assert settings.max_video_duration_seconds == 120
    assert settings.ytdlp_cookies_file is None
    assert settings.ytdlp_cookies_from_browser is None


def test_bad_duration_and_missing_cookies_file_reported_together(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.txt"
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            env={
                "MAX_VIDEO_DURATION_SECONDS": "abc",
                "YTDLP_COOKIES_FILE": str(missing),
            },
            env_file=None,
        )
    problems = excinfo.value.problems
    assert len(problems) == 2
    assert any("MAX_VIDEO_DURATION_SECONDS" in p for p in problems)
    assert any("YTDLP_COOKIES_FILE" in p for p in problems)


def test_duration_below_minimum_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env={"MAX_VIDEO_DURATION_SECONDS": "5"}, env_file=None)
    assert any("MAX_VIDEO_DURATION_SECONDS" in p for p in excinfo.value.problems)


def test_browser_cookie_option_wins_when_both_set(tmp_path: Path) -> None:
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("# netscape cookies\n", encoding="utf-8")
    settings = load_settings(
        env={
            "YTDLP_COOKIES_FILE": str(cookies_file),
            "YTDLP_COOKIES_FROM_BROWSER": "chrome",
        },
        env_file=None,
    )
    assert settings.ytdlp_cookies_from_browser == "chrome"
    assert settings.ytdlp_cookies_file is None


def test_cookies_file_alone_is_used_when_it_exists(tmp_path: Path) -> None:
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("# netscape cookies\n", encoding="utf-8")
    settings = load_settings(env={"YTDLP_COOKIES_FILE": str(cookies_file)}, env_file=None)
    assert settings.ytdlp_cookies_file == cookies_file
    assert settings.ytdlp_cookies_from_browser is None
