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

## Trying it out (P1: extraction, no bot yet)

Requires a real `GEMINI_API_KEY` in `.env` (and, for Instagram, `YTDLP_COOKIES_FILE`
or `YTDLP_COOKIES_FROM_BROWSER` pointing at a burner account -- see `.env.example`).

```powershell
uv run gcreelmap trip new "Tokyo test"
uv run gcreelmap add-reel <trip-slug> <reel-url> [<reel-url> ...]
uv run gcreelmap show <trip-slug>
```

`add-reel` queues each URL, then (unless `--queue-only` is passed) processes
everything queued for that trip one at a time: fetch -> one Gemini call ->
stored place mentions. `show` prints the trip's reels and their extracted
mentions. TikTok photo-post URLs (`tiktok.com/@user/photo/...`) and Instagram
carousels are currently not reliably downloadable -- see
`technical-decisions.md` -> Open Items in the planning vault.

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
