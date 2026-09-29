import random
from datetime import UTC, datetime, timedelta

import pytest

from gcreelmap.domain.timeutil import parse_iso, utc_iso


def test_utc_iso_requires_aware_datetime() -> None:
    naive = datetime(2026, 3, 1, 12, 0, 0)
    with pytest.raises(ValueError, match="aware"):
        utc_iso(naive)


def test_utc_iso_converts_non_utc_aware_input() -> None:
    from datetime import timezone

    plus8 = timezone(timedelta(hours=8))
    dt = datetime(2026, 3, 1, 20, 0, 0, tzinfo=plus8)
    assert utc_iso(dt) == "2026-03-01T12:00:00Z"


def test_round_trip() -> None:
    dt = datetime(2026, 3, 1, 12, 34, 56, tzinfo=UTC)
    assert parse_iso(utc_iso(dt)) == dt


def test_lexicographic_order_equals_chronological_order() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(seconds=i * 3701) for i in range(5)]
    texts = [utc_iso(t) for t in timestamps]
    shuffled = list(texts)
    random.shuffle(shuffled)
    assert sorted(shuffled) == texts
