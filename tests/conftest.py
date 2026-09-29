import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gcreelmap.store.db import connect
from gcreelmap.store.migrate import migrate
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    migrate(conn, db_path=None)
    yield conn
    conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "gcreelmap.db"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def tokens() -> FakeTokenSource:
    return FakeTokenSource()
