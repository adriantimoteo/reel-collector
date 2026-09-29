from pathlib import Path

import pytest

from gcreelmap.config import ConfigError, load_settings, redact


def test_defaults_applied_when_env_is_empty() -> None:
    settings = load_settings(env={}, env_file=None)
    assert settings.db_path == Path("./data/gcreelmap.db")
    assert settings.download_temp_dir == Path("./data/tmp")
    assert settings.lock_path == Path("./data/run.lock")
    assert settings.log_level == "INFO"
    assert settings.telegram_bot_token is None
    assert settings.gemini_api_key is None
    assert settings.google_places_api_key is None


def test_db_path_override_respected() -> None:
    settings = load_settings(env={"DB_PATH": "./custom-data/custom.db"}, env_file=None)
    assert settings.db_path == Path("./custom-data/custom.db")


def test_invalid_log_level_and_second_problem_reported_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env={"LOG_LEVEL": "LOUD", "DB_PATH": "   "}, env_file=None)
    problems = excinfo.value.problems
    assert len(problems) == 2
    assert any("LOG_LEVEL" in p for p in problems)
    assert any("DB_PATH" in p for p in problems)


def test_repr_never_contains_secret_values() -> None:
    settings = load_settings(
        env={
            "TELEGRAM_BOT_TOKEN": "super-secret-token",
            "GEMINI_API_KEY": "another-secret",
            "GOOGLE_PLACES_API_KEY": "yet-another-secret",
        },
        env_file=None,
    )
    text = repr(settings)
    assert "super-secret-token" not in text
    assert "another-secret" not in text
    assert "yet-another-secret" not in text


def test_require_raises_naming_missing_field() -> None:
    settings = load_settings(env={}, env_file=None)
    with pytest.raises(ConfigError) as excinfo:
        settings.require("gemini_api_key")
    assert "gemini_api_key" in excinfo.value.problems[0]


def test_require_passes_when_set() -> None:
    settings = load_settings(env={"GEMINI_API_KEY": "set"}, env_file=None)
    settings.require("gemini_api_key")  # does not raise


def test_env_file_loads_but_real_env_wins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\nDB_PATH=./from-dotenv.db\n", encoding="utf-8")

    import os

    old_log_level = os.environ.get("LOG_LEVEL")
    old_db_path = os.environ.get("DB_PATH")
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ.pop("DB_PATH", None)
    try:
        settings = load_settings(env=None, env_file=env_file)
    finally:
        if old_log_level is None:
            os.environ.pop("LOG_LEVEL", None)
        else:
            os.environ["LOG_LEVEL"] = old_log_level
        if old_db_path is not None:
            os.environ["DB_PATH"] = old_db_path

    assert settings.log_level == "WARNING"  # real env wins over .env
    assert settings.db_path == Path("./from-dotenv.db")  # .env fills in what's missing


def test_redact() -> None:
    assert redact("abcdefgh") == "****...efgh"
    assert redact(None) == "unset"
    assert redact("ab") == "****"
