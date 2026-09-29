"""Live extraction test. Excluded from CI (see pytest addopts `-m 'not live'`).

Run explicitly with real credentials, e.g.:
    $env:RUN_LIVE = "1"
    $env:GEMINI_API_KEY = "<real key>"
    $env:LIVE_REEL_URLS = "https://www.instagram.com/reel/..., https://youtube.com/shorts/..."
    uv run pytest -m live tests/live/test_extract_live.py

GEMINI_API_KEY (and cookie settings, if needed) must be in the real environment --
this test points --env-file at an isolated temp file, so the repo's own .env is
not read.
"""

import os
from pathlib import Path

import pytest

from gcreelmap.cli import main

pytestmark = pytest.mark.live

_KNOWN_ERROR_CODES = {
    "download_failed",
    "unsupported",
    "duration_cap",
    "extraction_failed",
    "no_places",
    "timeout",
    "internal",
}


def test_extract_live(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("set RUN_LIVE=1 to run live tests")

    urls = [u.strip() for u in os.environ.get("LIVE_REEL_URLS", "").split(",") if u.strip()]
    if not urls:
        pytest.skip("set LIVE_REEL_URLS (comma-separated) to run this test")

    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_PATH={(tmp_path / 'gcreelmap.db').as_posix()}\n", encoding="utf-8")

    exit_code = main(["--env-file", str(env_file), "trip", "new", "Live test"])
    assert exit_code == 0
    capsys.readouterr()

    exit_code = main(["--env-file", str(env_file), "add-reel", "1", *urls])
    out = capsys.readouterr().out
    assert exit_code == 0

    result_lines = [
        line for line in out.splitlines() if line.startswith(("done:", "failed:", "requeued:"))
    ]
    assert len(result_lines) == len(urls)
    for line in result_lines:
        if line.startswith("done:"):
            continue
        code = line.split(":", 1)[1].strip().split()[0]
        assert code in _KNOWN_ERROR_CODES, f"unexpected error code: {code}"
