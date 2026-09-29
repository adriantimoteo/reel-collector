from enum import StrEnum


class ErrorCode(StrEnum):
    DOWNLOAD_FAILED = "download_failed"
    UNSUPPORTED = "unsupported"
    DURATION_CAP = "duration_cap"
    EXTRACTION_FAILED = "extraction_failed"
    NO_PLACES = "no_places"
    TIMEOUT = "timeout"
    INTERNAL = "internal"
    DUPLICATE = "duplicate"


MAX_ATTEMPTS = 3

_TRANSIENT = frozenset(
    {
        ErrorCode.DOWNLOAD_FAILED,
        ErrorCode.EXTRACTION_FAILED,
        ErrorCode.TIMEOUT,
        ErrorCode.INTERNAL,
    }
)

_DESCRIPTIONS = {
    ErrorCode.DOWNLOAD_FAILED: "couldn't download it (it may be private, deleted, or blocked)",
    ErrorCode.UNSUPPORTED: "this kind of link isn't supported",
    ErrorCode.DURATION_CAP: "the video is longer than the limit",
    ErrorCode.EXTRACTION_FAILED: "the AI step failed",
    ErrorCode.NO_PLACES: "no places were found in it",
    ErrorCode.TIMEOUT: "it took too long to process",
    ErrorCode.INTERNAL: "an internal error occurred",
    ErrorCode.DUPLICATE: "already added",
}


def is_transient(code: ErrorCode) -> bool:
    return code in _TRANSIENT


def describe(code: ErrorCode) -> str:
    return _DESCRIPTIONS[code]
