import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

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
        **secrets,
    )


def redact(secret: str | None) -> str:
    if secret is None:
        return "unset"
    if len(secret) <= 4:
        return "****"
    return f"****...{secret[-4:]}"
