from datetime import UTC, datetime, timedelta

_DEFAULT_NOW = datetime(2026, 3, 1, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now if now is not None else _DEFAULT_NOW

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
