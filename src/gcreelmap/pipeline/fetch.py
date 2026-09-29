from reelkit.exceptions import DownloadError, ReelCaptureError
from reelkit.fetch import FetchSettings
from reelkit.fetch import fetch as reelkit_fetch
from reelkit.models import ReelMetadata

from gcreelmap.config import Settings


async def fetch_reel(url: str, settings: Settings) -> ReelMetadata:
    settings.download_temp_dir.mkdir(parents=True, exist_ok=True)
    fetch_settings = FetchSettings(
        temp_dir=settings.download_temp_dir,
        max_duration=settings.max_video_duration_seconds,
        cookies_file=settings.ytdlp_cookies_file,
        cookies_from_browser=settings.ytdlp_cookies_from_browser,
    )
    try:
        return await reelkit_fetch(url, fetch_settings)
    except ReelCaptureError:
        raise
    except Exception as exc:
        # reelkit's fetch() does not currently wrap yt-dlp/gallery-dl failures in its
        # own DownloadError (see technical-decisions.md -> Open Items); normalize here
        # so process_reel's exception mapping can rely on the documented contract.
        raise DownloadError(url=url, cause=exc) from exc
