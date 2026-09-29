from pathlib import Path
from typing import Any

from reelkit.models import ReelMetadata

def is_model_retired(e: Exception) -> bool: ...
def is_carousel(metadata: ReelMetadata) -> bool: ...
def media_paths(metadata: ReelMetadata) -> list[Path]: ...
async def generate_structured(
    client: Any,
    model: str,
    paths: list[Path],
    prompt: str,
    schema: dict[str, Any],
    *,
    description: str,
) -> dict[str, Any]: ...
