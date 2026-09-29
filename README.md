# GC Reel Map

A Telegram bot that watches a group chat for travel reels (Instagram, TikTok,
YouTube Shorts), extracts the places they mention, geocodes and merges them,
and renders one shared, per-trip map page for the group.

## Setup

```powershell
uv sync
Copy-Item .env.example .env
uv run gcreelmap doctor
```

`doctor` reports `[ok]`/`[warn]`/`[fail]` for config, the database, the run
lock, and the `reelkit`/`yt-dlp`/`gallery-dl` dependencies. `[warn]` lines for
the three API keys are expected until the phases that need them (P1, P2, P4).

## Developing against reelkit locally

This project depends on [`reelkit`](https://github.com/adriantimoteo/reel-notes)
(`packages/reelkit` in that repo) as a git dependency pinned to a commit SHA.
To iterate on reelkit changes alongside this project without waiting for a
merge/re-pin round trip, point `uv` at your local reel-notes checkout:

```powershell
uv add --editable ../reel-notes/packages/reelkit
```

**Never commit that editable override.** Once your reelkit change has landed
on `reel-notes`'s `main` branch, drop the editable override and re-pin:

```powershell
uv lock --upgrade-package reelkit
```

then commit the updated `pyproject.toml`/`uv.lock`.

## Tests, lint, and type-checking

```powershell
uv run pytest        # unit tests (live/perf tests are excluded by default)
uv run ruff check
uv run ruff format --check
uv run pyright
```

## Repository

https://github.com/adriantimoteo/reel-collector
