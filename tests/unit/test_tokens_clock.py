import re
from datetime import UTC, datetime, timedelta

from gcreelmap.domain.tokens import SecretsTokenSource
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


def test_secrets_token_source_shape() -> None:
    source = SecretsTokenSource()
    token = source.token(10)
    assert len(token) == 10
    assert re.fullmatch(r"[a-z2-7]{10}", token)


def test_secrets_token_source_two_calls_differ() -> None:
    source = SecretsTokenSource()
    assert source.token(10) != source.token(10)


def test_fake_token_source_is_deterministic() -> None:
    a = FakeTokenSource()
    b = FakeTokenSource()
    assert a.token(10) == b.token(10) == "aaaaaaaaaa"
    assert a.token(10) == "aaaaaaaaab"


def test_fake_clock_advance_moves_now() -> None:
    clock = FakeClock(datetime(2026, 3, 1, tzinfo=UTC))
    before = clock.now()
    clock.advance(timedelta(hours=1))
    assert clock.now() == before + timedelta(hours=1)
