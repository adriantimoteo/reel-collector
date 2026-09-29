import sqlite3

from gcreelmap.store.collections import (
    bump_version,
    create_collection,
    get_by_slug,
    get_collection,
    list_collections,
)
from tests.support.clock import FakeClock
from tests.support.tokens import FakeTokenSource


class _RepeatOnceTokenSource:
    """Returns the same token for the first two calls (forcing a clash on the
    second create_collection's first attempt), then a distinct one."""

    def __init__(self) -> None:
        self._calls = 0

    def token(self, n: int) -> str:
        self._calls += 1
        return ("a" if self._calls <= 2 else "b") * n


def test_create_then_fetch_by_id_and_slug(db: sqlite3.Connection, clock: FakeClock) -> None:
    tokens = FakeTokenSource()
    created = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=tokens
    )
    by_id = get_collection(db, created.id)
    by_slug = get_by_slug(db, created.slug)
    assert by_id == created
    assert by_slug == created
    assert created.slug == "tokyo-aaaaaaaaaa"
    assert created.owner_type == "user"
    assert created.state == "active"
    assert created.version == 0


def test_slug_clash_retries_then_succeeds(db: sqlite3.Connection, clock: FakeClock) -> None:
    tokens = _RepeatOnceTokenSource()
    first = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=tokens
    )
    second = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=tokens
    )
    assert first.slug != second.slug
    assert get_by_slug(db, second.slug) == second


def test_bump_version_increments(db: sqlite3.Connection, clock: FakeClock) -> None:
    tokens = FakeTokenSource()
    created = create_collection(
        db, owner_type="user", owner_id=0, name="Tokyo", now=clock.now(), tokens=tokens
    )
    bump_version(db, created.id)
    bump_version(db, created.id)
    updated = get_collection(db, created.id)
    assert updated is not None
    assert updated.version == created.version + 2


def test_list_collections_orders_by_id(db: sqlite3.Connection, clock: FakeClock) -> None:
    tokens = FakeTokenSource()
    a = create_collection(
        db, owner_type="user", owner_id=0, name="Alpha", now=clock.now(), tokens=tokens
    )
    b = create_collection(
        db, owner_type="user", owner_id=0, name="Beta", now=clock.now(), tokens=tokens
    )
    listed = list_collections(db)
    assert [c.id for c in listed] == sorted([a.id, b.id])
