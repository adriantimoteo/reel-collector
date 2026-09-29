import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from check_telemetry import find_blocked, load_blocklist, normalize  # noqa: E402

_BLOCKLIST_TEXT = """
# comment, ignored
sentry-sdk
posthog

# another comment
mixpanel
"""

_CLEAN_LOCKFILE = """
[[package]]
name = "requests"
version = "1.0.0"

[[package]]
name = "reelkit"
version = "0.1.0"
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_normalize() -> None:
    assert normalize("Sentry_SDK") == "sentry-sdk"
    assert normalize("sentry.sdk") == "sentry-sdk"
    assert normalize("sentry-sdk") == "sentry-sdk"


def test_passes_on_lockfile_without_blocked_packages(tmp_path: Path) -> None:
    lockfile = _write(tmp_path / "uv.lock", _CLEAN_LOCKFILE)
    blocklist = _write(tmp_path / "blocklist.txt", _BLOCKLIST_TEXT)
    assert find_blocked(lockfile, blocklist) == []


def test_fails_and_names_sentry_sdk_when_present(tmp_path: Path) -> None:
    lockfile = _write(
        tmp_path / "uv.lock",
        _CLEAN_LOCKFILE + '\n[[package]]\nname = "sentry-sdk"\nversion = "2.0.0"\n',
    )
    blocklist = _write(tmp_path / "blocklist.txt", _BLOCKLIST_TEXT)
    assert find_blocked(lockfile, blocklist) == ["sentry-sdk"]


def test_matches_alternate_spellings(tmp_path: Path) -> None:
    lockfile = _write(
        tmp_path / "uv.lock",
        _CLEAN_LOCKFILE + '\n[[package]]\nname = "Sentry_SDK"\nversion = "2.0.0"\n',
    )
    blocklist = _write(tmp_path / "blocklist.txt", _BLOCKLIST_TEXT)
    assert find_blocked(lockfile, blocklist) == ["sentry-sdk"]


def test_ignores_comments_in_blocklist(tmp_path: Path) -> None:
    blocklist = _write(tmp_path / "blocklist.txt", _BLOCKLIST_TEXT)
    names = load_blocklist(blocklist)
    assert "sentry-sdk" in names
    assert "" not in names
    assert not any(n.startswith("#") for n in names)


def test_real_repo_lockfile_is_clean() -> None:
    lockfile = _REPO_ROOT / "uv.lock"
    blocklist = _REPO_ROOT / "scripts" / "telemetry-blocklist.txt"
    if not lockfile.exists():
        import pytest

        pytest.skip("uv.lock not generated yet (run uv sync first)")
    assert find_blocked(lockfile, blocklist) == []
