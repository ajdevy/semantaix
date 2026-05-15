"""Admin natural-language dialog for Epic 10 story 10.05.

Detects whether an admin DM is a (a) natural-language admin intent
("создай проект …", "удали оператора …", "привяжи файл …", etc.) or
(b) a confirm/cancel reply to a previously-proposed pending session.
On (a) it calls api `POST /admin/nl-ops` to propose; on (b) it looks up
the admin's latest pending session via api and calls confirm or cancel.
Replies are DM'd back to the admin via the supplied `send_dm` coroutine.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

SendDmFn = Callable[[int, str], Awaitable[Any]]

# Trigger phrases for NL intents — matched as a substring (case-insensitive
# lower of the casefolded text). The api side does the structured parsing.
_INTENT_KEYWORDS = (
    "создай проект",
    "создайте проект",
    "переименуй проект",
    "переименуйте проект",
    "добавь оператора",
    "добавьте оператора",
    "удали оператора",
    "удалите оператора",
    "привяжи файл",
    "привяжите файл",
)

_CONFIRM_KEYWORDS = ("да", "yes", "подтверждаю", "ok", "подтвердить")
_CANCEL_KEYWORDS = ("нет", "no", "отмена", "отменить", "cancel")
_CONFIRM_SLASH_RE = re.compile(r"^\s*/confirm(?:\s+(?P<token>\S+))?\s*$", re.IGNORECASE)
_CANCEL_SLASH_RE = re.compile(r"^\s*/cancel\s*$", re.IGNORECASE)


def _looks_like_intent(text: str) -> bool:
    casefolded = text.casefold()
    return any(keyword in casefolded for keyword in _INTENT_KEYWORDS)


def _is_confirm_reply(text: str) -> bool:
    stripped = text.strip().casefold()
    return stripped in _CONFIRM_KEYWORDS


def _is_cancel_reply(text: str) -> bool:
    stripped = text.strip().casefold()
    return stripped in _CANCEL_KEYWORDS


def _format_preview_reply(session: dict[str, Any]) -> str:
    preview = str(session.get("preview", "")).strip()
    token = str(session.get("confirm_token", ""))
    return (
        f"{preview}\nПодтвердите ответом «да» или /confirm {token}. "
        "Отмена: «нет» или /cancel."
    )


async def _confirm_session(
    *,
    session: dict[str, Any],
    api_client: ApiClient,
    send_dm: SendDmFn,
    chat_id: int,
) -> dict[str, str]:
    try:
        body = await api_client.admin_nl_ops_confirm(
            session_id=int(session["id"]),
            confirm_token=str(session.get("confirm_token") or ""),
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Не удалось подтвердить ({status}).")
        return {
            "status": "error",
            "route": "admin_nl_confirm",
            "http_status": str(status),
        }
    op_type = str(body.get("op_type", ""))
    await send_dm(chat_id, f"Операция применена: {op_type}.")
    return {"status": "ok", "route": "admin_nl_confirm", "op_type": op_type}


async def handle_admin_nl_dialog(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    admin_username: str,
) -> dict[str, str] | None:
    """Top-level dispatcher for admin natural-language dialog.

    Returns None when the sender is not the admin or the text matches
    nothing. The slash-command admin dispatcher in `admin_commands.py`
    runs FIRST so explicit `/projects` etc. don't fall into the NL
    branch by accident.
    """
    if not normalized.username or normalized.username != admin_username:
        return None
    text = normalized.text or ""

    # /confirm <token> takes precedence over keyword detection.
    m = _CONFIRM_SLASH_RE.match(text)
    if m:
        return await _handle_confirm(
            chat_id=normalized.chat_id,
            admin_username=admin_username,
            explicit_token=m.group("token"),
            api_client=api_client,
            send_dm=send_dm,
        )

    if _CANCEL_SLASH_RE.match(text) or _is_cancel_reply(text):
        return await _handle_cancel(
            chat_id=normalized.chat_id,
            admin_username=admin_username,
            api_client=api_client,
            send_dm=send_dm,
        )

    if _is_confirm_reply(text):
        return await _handle_confirm(
            chat_id=normalized.chat_id,
            admin_username=admin_username,
            explicit_token=None,
            api_client=api_client,
            send_dm=send_dm,
        )

    if _looks_like_intent(text):
        return await _handle_propose(
            chat_id=normalized.chat_id,
            admin_username=admin_username,
            utterance=text,
            api_client=api_client,
            send_dm=send_dm,
        )

    return None


async def _handle_propose(
    *,
    chat_id: int,
    admin_username: str,
    utterance: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    try:
        body = await api_client.admin_nl_ops_propose(
            admin_username=admin_username, utterance=utterance
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Ошибка ({status}). Попробуйте позже.")
        return {
            "status": "error",
            "route": "admin_nl_propose",
            "http_status": str(status),
        }
    status = str(body.get("status"))
    if status == "clarify":
        await send_dm(chat_id, str(body.get("preview", "")))
        return {"status": "ok", "route": "admin_nl_propose", "decision": "clarify"}
    await send_dm(chat_id, _format_preview_reply(body))
    return {
        "status": "ok",
        "route": "admin_nl_propose",
        "session_id": str(body.get("id")),
    }


async def _handle_confirm(
    *,
    chat_id: int,
    admin_username: str,
    explicit_token: str | None,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    try:
        latest = await api_client.admin_nl_ops_latest_pending(
            admin_username=admin_username
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Ошибка поиска сессии ({status}).")
        return {
            "status": "error",
            "route": "admin_nl_confirm",
            "http_status": str(status),
        }
    if not latest.get("found"):
        await send_dm(chat_id, "Нет ожидающих подтверждения операций.")
        return {
            "status": "ok",
            "route": "admin_nl_confirm",
            "decision": "no_pending",
        }
    # If admin supplied an explicit token, ensure it matches.
    if (
        explicit_token is not None
        and str(latest.get("confirm_token")) != explicit_token
    ):
        await send_dm(chat_id, "Неверный токен подтверждения.")
        return {
            "status": "ok",
            "route": "admin_nl_confirm",
            "decision": "wrong_token",
        }
    return await _confirm_session(
        session=latest,
        api_client=api_client,
        send_dm=send_dm,
        chat_id=chat_id,
    )


async def _handle_cancel(
    *,
    chat_id: int,
    admin_username: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    try:
        latest = await api_client.admin_nl_ops_latest_pending(
            admin_username=admin_username
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Ошибка поиска сессии ({status}).")
        return {
            "status": "error",
            "route": "admin_nl_cancel",
            "http_status": str(status),
        }
    if not latest.get("found"):
        await send_dm(chat_id, "Нет ожидающих подтверждения операций.")
        return {
            "status": "ok",
            "route": "admin_nl_cancel",
            "decision": "no_pending",
        }
    try:
        await api_client.admin_nl_ops_cancel(session_id=int(latest["id"]))
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Не удалось отменить ({status}).")
        return {
            "status": "error",
            "route": "admin_nl_cancel",
            "http_status": str(status),
        }
    await send_dm(chat_id, "Операция отменена.")
    return {"status": "ok", "route": "admin_nl_cancel"}
