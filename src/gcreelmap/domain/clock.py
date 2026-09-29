from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...  # aware UTC


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
