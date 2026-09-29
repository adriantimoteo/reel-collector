import sqlite3
from typing import Any

import httpx
import pytest
import respx
from aiogram import Bot, Dispatcher

from tests.support.fixtures import load_fixture
from tests.support.telegram import make_message_update, make_my_chat_member_update

_FAKE_TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105 - fake bot token


def test_db_fixture_is_migrated(db: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "collections" in tables
    assert "schema_migrations" in tables


async def _feed(update: Any) -> list[Any]:
    calls: list[Any] = []
    dp = Dispatcher()

    @dp.message()
    async def _on_message(message: Any, **kwargs: Any) -> None:
        calls.append(message)

    @dp.my_chat_member()
    async def _on_member(event: Any, **kwargs: Any) -> None:
        calls.append(event)

    bot = Bot(token=_FAKE_TOKEN)
    try:
        await dp.feed_update(bot, update)
    finally:
        await bot.session.close()
    return calls


@pytest.mark.asyncio
async def test_make_message_update_accepted_by_dispatcher() -> None:
    update = make_message_update(chat_id=-100123, text="hello https://example.com")
    calls = await _feed(update)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_make_my_chat_member_update_accepted_by_dispatcher() -> None:
    update = make_my_chat_member_update(chat_id=-100123, old_status="left", new_status="member")
    calls = await _feed(update)
    assert len(calls) == 1


def test_load_fixture_reads_json() -> None:
    data = load_fixture("telegram/sample.json")
    assert data == {"ok": True, "note": "sample fixture for load_fixture tests"}


def test_load_fixture_reads_text() -> None:
    text = load_fixture("telegram/sample.txt")
    assert text.strip() == "sample text fixture"


@respx.mock
def test_respx_mocks_httpx_call() -> None:
    route = respx.get("https://example.invalid/api").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    response = httpx.get("https://example.invalid/api")
    assert route.called
    assert response.json() == {"ok": True}
