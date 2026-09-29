"""Factories for building valid aiogram Update objects in tests.

The `Messenger` Protocol itself is created in P4; the FakeMessenger fixture
(recording send_text/send_document calls, with fail_next(exc) scripting) is
defined there against it. TODO(P4): add FakeMessenger here.
"""

from typing import Any

from aiogram.types import Update

_DATE = 1_700_000_000


def _chat_member_payload(
    user_id: int, status: str, *, is_bot: bool = False, first_name: str = "User"
) -> dict[str, Any]:
    user = {"id": user_id, "is_bot": is_bot, "first_name": first_name}
    base: dict[str, Any] = {"user": user, "status": status}

    if status == "creator":
        return {**base, "is_anonymous": False}
    if status == "administrator":
        return {
            **base,
            "can_be_edited": False,
            "is_anonymous": False,
            "can_manage_chat": True,
            "can_delete_messages": True,
            "can_manage_video_chats": True,
            "can_restrict_members": True,
            "can_promote_members": False,
            "can_change_info": True,
            "can_invite_users": True,
            "can_post_messages": False,
            "can_edit_messages": False,
            "can_pin_messages": True,
            "can_manage_topics": False,
        }
    if status == "restricted":
        return {
            **base,
            "is_member": True,
            "can_send_messages": True,
            "can_send_audios": True,
            "can_send_documents": True,
            "can_send_photos": True,
            "can_send_videos": True,
            "can_send_video_notes": True,
            "can_send_voice_notes": True,
            "can_send_polls": True,
            "can_send_other_messages": True,
            "can_add_web_page_previews": True,
            "can_change_info": False,
            "can_invite_users": False,
            "can_pin_messages": False,
            "can_manage_topics": False,
            "until_date": 0,
        }
    if status == "kicked":
        return {**base, "until_date": 0}
    # "left", "member"
    return base


def make_message_update(
    chat_id: int,
    text: str,
    *,
    user_id: int = 1,
    message_id: int = 1,
    update_id: int = 1,
    chat_type: str = "supergroup",
    entities: list[dict[str, Any]] | None = None,
) -> Update:
    message: dict[str, Any] = {
        "message_id": message_id,
        "date": _DATE,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
        "text": text,
    }
    if entities is not None:
        message["entities"] = entities
    return Update.model_validate({"update_id": update_id, "message": message})


def make_my_chat_member_update(
    chat_id: int,
    old_status: str,
    new_status: str,
    *,
    update_id: int = 1,
    bot_user_id: int = 999,
    actor_user_id: int = 1,
    chat_type: str = "supergroup",
) -> Update:
    payload = {
        "update_id": update_id,
        "my_chat_member": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": actor_user_id, "is_bot": False, "first_name": "Actor"},
            "date": _DATE,
            "old_chat_member": _chat_member_payload(bot_user_id, old_status, is_bot=True),
            "new_chat_member": _chat_member_payload(bot_user_id, new_status, is_bot=True),
        },
    }
    return Update.model_validate(payload)
