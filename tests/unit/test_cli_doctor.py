import sqlite3
from pathlib import Path

import pytest

from gcreelmap.cli import main
from gcreelmap.run_lock import RunLock


@pytest.fixture(autouse=True)
def _isolated_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "GOOGLE_PLACES_API_KEY", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)


def test_valid_env_exits_zero_with_ok_and_warn_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\n", encoding="utf-8")

    exit_code = main(["--env-file", str(env_file), "doctor"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] config loaded" in out
    assert "[warn]" in out  # unset secrets
    assert out.count("[warn]") == 3
    assert "[fail]" not in out


def test_invalid_log_level_exits_nonzero_with_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=LOUD\n", encoding="utf-8")

    exit_code = main(["--env-file", str(env_file), "doctor"])

    out = capsys.readouterr().out
    assert exit_code != 0
    assert "[fail]" in out
    assert "LOG_LEVEL" in out


def test_held_lock_reports_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    lock_path = tmp_path / "run.lock"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\nLOCK_PATH={lock_path.as_posix()}\n",
        encoding="utf-8",
    )

    with RunLock(lock_path):
        exit_code = main(["--env-file", str(env_file), "doctor"])

    out = capsys.readouterr().out
    assert exit_code != 0
    assert "[fail]" in out
    assert "lock" in out.lower()


def test_no_migrate_on_fresh_db_reports_pending_and_leaves_no_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "fresh.db"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_PATH={db_path.as_posix()}\n", encoding="utf-8")

    exit_code = main(["--env-file", str(env_file), "doctor", "--no-migrate"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "pending migrations: 1" in out

    conn = sqlite3.connect(db_path)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    assert tables == []


def test_secrets_never_appear_unredacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    secret = "s3cr3t-token-value-should-not-leak"  # noqa: S105 - test fixture value, not real
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\nTELEGRAM_BOT_TOKEN={secret}\n",
        encoding="utf-8",
    )

    main(["--env-file", str(env_file), "doctor"])

    out = capsys.readouterr().out
    assert secret not in out
    assert "TELEGRAM_BOT_TOKEN set" in out
