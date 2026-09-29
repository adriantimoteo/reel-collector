from datetime import UTC, datetime

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_iso(dt: datetime) -> str:
    """Format an aware datetime as UTC text, second precision.

    Storing every timestamp with this exact format means lexicographic
    string comparison in SQLite equals chronological comparison.
    """
    if dt.tzinfo is None:
        raise ValueError("utc_iso requires a timezone-aware datetime")
    return dt.astimezone(UTC).strftime(_FORMAT)


def parse_iso(s: str) -> datetime:
    """Parse text produced by utc_iso back into an aware UTC datetime."""
    return datetime.strptime(s, _FORMAT).replace(tzinfo=UTC)
