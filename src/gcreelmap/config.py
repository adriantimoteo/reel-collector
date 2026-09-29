import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
_DEFAULT_MAX_VIDEO_DURATION_SECONDS = 120

_PATH_ENV_FIELDS = {
    "DB_PATH": ("db_path", Path("./data/gcreelmap.db")),
    "DOWNLOAD_TEMP_DIR": ("download_temp_dir", Path("./data/tmp")),
    "LOCK_PATH": ("lock_path", Path("./data/run.lock")),
}

_SECRET_ENV_FIELDS = {
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "GEMINI_API_KEY": "gemini_api_key",
    "GOOGLE_PLACES_API_KEY": "google_places_api_key",
}


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    download_temp_dir: Path
    lock_path: Path
    log_level: str
    gemini_model: str
    max_video_duration_seconds: int
    ytdlp_cookies_file: Path | None
    ytdlp_cookies_from_browser: str | None
    telegram_bot_token: str | None = field(default=None, repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)
    google_places_api_key: str | None = field(default=None, repr=False)

    def require(self, *field_names: str) -> None:
        missing = [name for name in field_names if getattr(self, name) is None]
        if missing:
            raise ConfigError([f"{name} is required but not set" for name in missing])


def _merged_values(env: Mapping[str, str] | None, env_file: Path | str | None) -> Mapping[str, str]:
    if env is not None:
        return env
    file_values: dict[str, str] = {}
    if env_file is not None:
        path = Path(env_file)
        if path.exists():
            file_values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    return {**file_values, **os.environ}


def resolve_db_path(
    env: Mapping[str, str] | None = None, env_file: Path | str | None = ".env"
) -> Path:
    """Best-effort DB_PATH resolution, independent of full settings validity.

    Used by `doctor` to check the DB directory even when other settings are
    invalid, so unrelated problems are reported together rather than masking
    each other.
    """
    values = _merged_values(env, env_file)
    raw = values.get("DB_PATH")
    if raw and raw.strip():
        return Path(raw)
    return _PATH_ENV_FIELDS["DB_PATH"][1]


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: Path | str | None = ".env",
) -> Settings:
    values = _merged_values(env, env_file)

    problems: list[str] = []
    parsed_paths: dict[str, Path] = {}
    for env_name, (field_name, default) in _PATH_ENV_FIELDS.items():
        raw = values.get(env_name)
        if raw is None:
            parsed_paths[field_name] = default
        elif not raw.strip():
            problems.append(f"{env_name} must not be empty")
            parsed_paths[field_name] = default
        else:
            parsed_paths[field_name] = Path(raw)

    log_level = values.get("LOG_LEVEL", "INFO")
    if log_level not in _VALID_LOG_LEVELS:
        problems.append(
            f"LOG_LEVEL must be one of {', '.join(_VALID_LOG_LEVELS)}, got {log_level!r}"
        )

    gemini_model = values.get("GEMINI_MODEL") or _DEFAULT_GEMINI_MODEL

    raw_duration = values.get("MAX_VIDEO_DURATION_SECONDS")
    max_video_duration_seconds = _DEFAULT_MAX_VIDEO_DURATION_SECONDS
    if raw_duration is not None:
        try:
            max_video_duration_seconds = int(raw_duration)
        except ValueError:
            problems.append(f"MAX_VIDEO_DURATION_SECONDS must be an integer, got {raw_duration!r}")
        else:
            if max_video_duration_seconds < 10:
                problems.append("MAX_VIDEO_DURATION_SECONDS must be >= 10")

    # If both cookie sources are configured, the browser wins (matches reel-notes and
    # reelkit's own _apply_cookies precedence) -- the file is then irrelevant, so its
    # existence isn't even checked.
    raw_cookies_from_browser = values.get("YTDLP_COOKIES_FROM_BROWSER")
    ytdlp_cookies_from_browser = raw_cookies_from_browser or None

    raw_cookies_file = values.get("YTDLP_COOKIES_FILE")
    ytdlp_cookies_file: Path | None = None
    if raw_cookies_file and not ytdlp_cookies_from_browser:
        candidate = Path(raw_cookies_file)
        if not candidate.exists():
            problems.append(f"YTDLP_COOKIES_FILE does not exist: {raw_cookies_file}")
        else:
            ytdlp_cookies_file = candidate

    secrets: dict[str, str | None] = {}
    for env_name, field_name in _SECRET_ENV_FIELDS.items():
        raw = values.get(env_name)
        secrets[field_name] = raw if raw else None

    if problems:
        raise ConfigError(problems)

    return Settings(
        db_path=parsed_paths["db_path"],
        download_temp_dir=parsed_paths["download_temp_dir"],
        lock_path=parsed_paths["lock_path"],
        log_level=log_level,
        gemini_model=gemini_model,
        max_video_duration_seconds=max_video_duration_seconds,
        ytdlp_cookies_file=ytdlp_cookies_file,
        ytdlp_cookies_from_browser=ytdlp_cookies_from_browser,
        **secrets,
    )


def redact(secret: str | None) -> str:
    if secret is None:
        return "unset"
    if len(secret) <= 4:
        return "****"
    return f"****...{secret[-4:]}"
