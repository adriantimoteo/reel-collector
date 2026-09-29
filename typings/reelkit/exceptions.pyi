class ReelCaptureError(Exception): ...

class UnsupportedPlatformError(ReelCaptureError):
    url: str
    def __init__(self, url: str) -> None: ...

class UnsupportedCarouselError(UnsupportedPlatformError): ...

class DurationCapExceeded(ReelCaptureError):
    duration: int
    cap: int
    def __init__(self, duration: int, cap: int) -> None: ...

class DownloadError(ReelCaptureError):
    url: str
    cause: Exception
    def __init__(self, url: str, cause: Exception) -> None: ...

class ExtractionError(ReelCaptureError):
    cause: Exception
    def __init__(self, cause: Exception) -> None: ...
