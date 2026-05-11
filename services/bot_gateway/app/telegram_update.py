from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TelegramUpdateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedTelegramMessage:
    update_id: int
    source_message_id: int
    chat_id: int
    user_id: int
    username: str | None
    text: str
    reply_to_text: str | None = None


def normalize_update(payload: dict[str, Any]) -> NormalizedTelegramMessage | None:
    """Normalize supported Telegram updates for Epic 01.

    Returns:
      - NormalizedTelegramMessage when update contains a usable text message.
      - None when update is valid but intentionally ignored in Epic 01
        (non-text/unsupported update types).
    Raises:
      - TelegramUpdateValidationError for malformed payloads.
    """
    update_id = payload.get("update_id")
    if not isinstance(update_id, int):
        raise TelegramUpdateValidationError("missing_or_invalid_update_id")

    message = payload.get("message")
    if message is None:
        # Valid Telegram updates can be callback_query/edited_message/etc.
        return None
    if not isinstance(message, dict):
        raise TelegramUpdateValidationError("invalid_message_object")

    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        raise TelegramUpdateValidationError("missing_or_invalid_message_id")

    chat = message.get("chat")
    if not isinstance(chat, dict):
        raise TelegramUpdateValidationError("missing_or_invalid_chat")
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        raise TelegramUpdateValidationError("missing_or_invalid_chat_id")

    from_user = message.get("from")
    if not isinstance(from_user, dict):
        raise TelegramUpdateValidationError("missing_or_invalid_from")
    user_id = from_user.get("id")
    if not isinstance(user_id, int):
        raise TelegramUpdateValidationError("missing_or_invalid_user_id")
    username = from_user.get("username")
    normalized_username = f"@{username}" if isinstance(username, str) and username else None

    text = message.get("text")
    if not isinstance(text, str):
        # Non-text message types are intentionally ignored in Epic 01.
        return None

    normalized_text = text.strip()
    if normalized_text == "":
        return None

    reply_to_text: str | None = None
    reply_to = message.get("reply_to_message")
    if isinstance(reply_to, dict):
        candidate = reply_to.get("text")
        if isinstance(candidate, str) and candidate.strip():
            reply_to_text = candidate

    return NormalizedTelegramMessage(
        update_id=update_id,
        source_message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        username=normalized_username,
        text=normalized_text,
        reply_to_text=reply_to_text,
    )
