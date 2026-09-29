from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

@dataclass
class ReelMetadata:
    source_url: str
    platform: Literal["instagram", "tiktok", "youtube"]
    author: str | None
    posted_at: datetime | None
    title: str | None
    caption: str | None
    video_path: Path | None = ...
    image_paths: list[Path] = ...
    audio_path: Path | None = ...
    hashtags: list[str] = ...
    def temp_paths(self) -> list[Path]: ...
