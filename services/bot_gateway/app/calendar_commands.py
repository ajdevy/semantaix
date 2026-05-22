"""Operator `/connect_calendar` + `/disconnect_calendar` commands (Epic 11, story 11.03).

Give the project's designated calendar operator a Telegram entry point:

- `/connect_calendar` asks the api to mint a Google consent URL and DMs it with a
  short Russian instruction.
- `/disconnect_calendar` asks the api to revoke + delete the stored token and DMs
  a Russian confirmation.

Gating mirrors the existing operator-command dispatch (`kb_intent._SLASH_RE`,
`/hitl_config`): the sender is resolved against the Epic-10 operator registry, and
only a registered operator bound to a project may run these. The api enforces the
*designated* calendar-operator rule (returns ``not_calendar_operator`` for a
non-designated operator), which surfaces here as the Russian fallback DM.

Never log the consent URL (it carries a single-use ``state``) or any token.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.operator_resolver import resolve_operator_for_sender
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

logger = logging.getLogger(__name__)

SendDmFn = Callable[[int, str], Awaitable[Any]]

_CONNECT_RE = re.compile(r"^\s*/connect_calendar\b", re.IGNORECASE)
_DISCONNECT_RE = re.compile(r"^\s*/disconnect_calendar\b", re.IGNORECASE)

# Russian-first operator-facing copy, kept with the command (consistent with the
# other bot_gateway slash-command copy).
_CONNECT_INSTRUCTION = (
    "🔗 Чтобы подключить календарь, откройте ссылку и разрешите доступ "
    "(только чтение занятости):\n{consent_url}\n\n"
    "После подтверждения вернитесь в Telegram — доступ заработает автоматически."
)
_CONNECT_FALLBACK = (
    "Не получилось начать подключение календаря — попробуйте чуть позже."
)
_DISCONNECT_CONFIRMATION = (
    "✅ Календарь отключён. Доступ к занятости отозван, сохранённый токен удалён."
)
_DISCONNECT_FALLBACK = (
    "Не получилось отключить календарь — попробуйте чуть позже."
)


async def handle_calendar_command(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    primary_operator_username: str,
    internal_token: str,
) -> dict[str, str] | None:
    """Dispatch `/connect_calendar` and `/disconnect_calendar`.

    Returns None for non-matching messages so the normal routing continues.
    A non-authorized sender (not a registered operator bound to a project) is
    ignored with the logged reason ``unauthorized_calendar``.
    """
    text = normalized.text or ""
    is_connect = bool(_CONNECT_RE.match(text))
    is_disconnect = bool(_DISCONNECT_RE.match(text))
    if not (is_connect or is_disconnect):
        return None

    resolved = await resolve_operator_for_sender(
        username=normalized.username,
        api_client=api_client,
        primary_operator_username=primary_operator_username,
    )
    if resolved is None or resolved.project_id is None:
        logger.warning(
            "calendar_command_unauthorized",
            extra={
                "username": normalized.username,
                "reason": "unauthorized_calendar",
                "command": "connect" if is_connect else "disconnect",
            },
        )
        return {"status": "ignored", "reason": "unauthorized_calendar"}

    if is_connect:
        return await _do_connect(
            normalized=normalized,
            api_client=api_client,
            send_dm=send_dm,
            project_id=resolved.project_id,
            operator=resolved.username,
            internal_token=internal_token,
        )
    return await _do_disconnect(
        normalized=normalized,
        api_client=api_client,
        send_dm=send_dm,
        project_id=resolved.project_id,
        operator=resolved.username,
        internal_token=internal_token,
    )


async def _do_connect(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
) -> dict[str, str]:
    try:
        result = await api_client.initiate_calendar_connect(
            project_id=project_id,
            operator=operator,
            internal_token=internal_token,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.warning(
            "calendar_connect_initiate_failed",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _CONNECT_FALLBACK)
        return {"status": "accepted", "route": "calendar_connect", "decision": "api_error"}

    consent_url = str(result.get("consent_url") or "")
    if not consent_url:
        logger.warning(
            "calendar_connect_missing_url",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _CONNECT_FALLBACK)
        return {"status": "accepted", "route": "calendar_connect", "decision": "no_url"}

    # NB: log success WITHOUT the consent URL — it carries a single-use state.
    logger.info(
        "calendar_connect_url_sent",
        extra={"project_id": project_id, "operator": operator},
    )
    await send_dm(normalized.chat_id, _CONNECT_INSTRUCTION.format(consent_url=consent_url))
    return {"status": "accepted", "route": "calendar_connect", "decision": "url_sent"}


async def _do_disconnect(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
) -> dict[str, str]:
    try:
        await api_client.disconnect_calendar(
            project_id=project_id,
            operator=operator,
            internal_token=internal_token,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.warning(
            "calendar_disconnect_failed",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _DISCONNECT_FALLBACK)
        return {
            "status": "accepted",
            "route": "calendar_disconnect",
            "decision": "api_error",
        }

    logger.info(
        "calendar_disconnected",
        extra={"project_id": project_id, "operator": operator},
    )
    await send_dm(normalized.chat_id, _DISCONNECT_CONFIRMATION)
    return {
        "status": "accepted",
        "route": "calendar_disconnect",
        "decision": "disconnected",
    }
