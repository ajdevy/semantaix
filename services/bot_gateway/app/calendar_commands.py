"""Operator `/connect_calendar` + `/disconnect_calendar` commands (Epic 11, story 11.03).

Give the project's designated calendar operator a Telegram entry point:

- `/connect_calendar` asks the api to mint a Google consent URL and DMs it with a
  short Russian instruction. **Connect IS enable**: a successful OAuth callback
  also flips the project to enabled and records the connecting operator as the
  designated calendar operator atomically with the token upsert. There is no
  separate `/calendar_on` command — re-enable after `/calendar_off` means the
  operator re-runs `/connect_calendar`.
- `/disconnect_calendar` asks the api to revoke + delete the stored token and DMs
  a Russian confirmation.
- `/calendar_off` pauses the feature for the operator's project without losing
  the stored token; `/calendar_service add|remove …` manages per-service rules.

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
_CALENDAR_OFF_RE = re.compile(r"^\s*/calendar_off\b\s*$", re.IGNORECASE)
_CALENDAR_SERVICE_RE = re.compile(r"^\s*/calendar_service\b\s*(?P<rest>.*)$", re.IGNORECASE)

_DAY_TOKENS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RE = re.compile(r"^(?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2})$")

# Russian-first operator-facing copy, kept with the command (consistent with the
# other bot_gateway slash-command copy).
_CONNECT_INSTRUCTION = (
    "🔗 Чтобы подключить календарь, откройте ссылку и разрешите доступ "
    "(только чтение занятости):\n{consent_url}\n\n"
    "После подтверждения вернитесь в Telegram — доступ заработает автоматически, "
    "и календарь включится для вашего проекта."
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
_CALENDAR_OFF_CONFIRMATION = (
    "✅ Календарь выключен. Сохранённый токен не удалён — чтобы снова включить, "
    "запустите /connect_calendar."
)
_CALENDAR_FALLBACK = "Не получилось изменить настройки календаря — попробуйте чуть позже."
_SERVICE_USAGE = (
    "Использование:\n"
    "/calendar_service add <название> <минуты> <дни> <часы>\n"
    "например: /calendar_service add маникюр 60 mon-sat 10:00-19:00\n"
    "/calendar_service remove <id>"
)
_SERVICE_ADDED = "✅ Услуга #{rule_id} «{name}» сохранена."
_SERVICE_REMOVED = "✅ Услуга #{rule_id} удалена."
_SERVICE_FALLBACK = "Не получилось сохранить услугу — попробуйте чуть позже."


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
    is_off = bool(_CALENDAR_OFF_RE.match(text))
    service_match = _CALENDAR_SERVICE_RE.match(text)
    is_service = service_match is not None
    if not (is_connect or is_disconnect or is_off or is_service):
        return None

    if is_connect:
        command = "connect"
    elif is_disconnect:
        command = "disconnect"
    elif is_off:
        command = "calendar_off"
    else:
        command = "calendar_service"

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
                "command": command,
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
    if is_disconnect:
        return await _do_disconnect(
            normalized=normalized,
            api_client=api_client,
            send_dm=send_dm,
            project_id=resolved.project_id,
            operator=resolved.username,
            internal_token=internal_token,
        )
    if is_off:
        return await _do_disable(
            normalized=normalized,
            api_client=api_client,
            send_dm=send_dm,
            project_id=resolved.project_id,
            operator=resolved.username,
            internal_token=internal_token,
        )
    return await _do_service(
        normalized=normalized,
        api_client=api_client,
        send_dm=send_dm,
        project_id=resolved.project_id,
        operator=resolved.username,
        internal_token=internal_token,
        rest=service_match.group("rest").strip(),
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


async def _do_disable(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
) -> dict[str, str]:
    try:
        await api_client.calendar_disable(
            project_id=project_id,
            actor=operator,
            actor_role="operator",
            internal_token=internal_token,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.warning(
            "calendar_disable_failed",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _CALENDAR_FALLBACK)
        return {"status": "accepted", "route": "calendar_off", "decision": "api_error"}
    logger.info(
        "calendar_disabled",
        extra={"project_id": project_id, "operator": operator},
    )
    await send_dm(normalized.chat_id, _CALENDAR_OFF_CONFIRMATION)
    return {"status": "accepted", "route": "calendar_off", "decision": "disabled"}


def _parse_day_range(token: str) -> list[str] | None:
    """Parse ``mon-sat`` or a single ``mon`` into a list of weekday tokens."""
    token = token.lower()
    if "-" in token:
        start, _, end = token.partition("-")
        if start not in _DAY_TOKENS or end not in _DAY_TOKENS:
            return None
        start_i = _DAY_TOKENS.index(start)
        end_i = _DAY_TOKENS.index(end)
        if start_i > end_i:
            return None
        return list(_DAY_TOKENS[start_i : end_i + 1])
    if token not in _DAY_TOKENS:
        return None
    return [token]


def parse_service_add(rest: str) -> dict[str, object] | None:
    """Parse ``add <name> <minutes> <days> <hours>`` into repository kwargs.

    Returns None on any malformed input so the caller can show usage help.
    """
    parts = rest.split()
    if len(parts) != 5 or parts[0].lower() != "add":
        return None
    name = parts[1]
    if not parts[2].isdigit():
        return None
    duration = int(parts[2])
    if duration <= 0:
        return None
    days = _parse_day_range(parts[3])
    if days is None:
        return None
    time_match = _TIME_RE.match(parts[4])
    if time_match is None:
        return None
    start = time_match.group("start")
    end = time_match.group("end")
    working_hours = {day: [start, end] for day in days}
    return {
        "name": name,
        "duration_minutes": duration,
        "service_days": days,
        "working_hours": working_hours,
    }


async def _do_service(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
    rest: str,
) -> dict[str, str]:
    parts = rest.split()
    action = parts[0].lower() if parts else ""
    if action == "remove" and len(parts) == 2 and parts[1].isdigit():
        return await _do_service_remove(
            normalized=normalized,
            api_client=api_client,
            send_dm=send_dm,
            project_id=project_id,
            operator=operator,
            internal_token=internal_token,
            rule_id=int(parts[1]),
        )
    if action == "add":
        parsed = parse_service_add(rest)
        if parsed is not None:
            return await _do_service_add(
                normalized=normalized,
                api_client=api_client,
                send_dm=send_dm,
                project_id=project_id,
                operator=operator,
                internal_token=internal_token,
                parsed=parsed,
            )
    await send_dm(normalized.chat_id, _SERVICE_USAGE)
    return {"status": "ignored", "route": "calendar_service", "reason": "usage"}


async def _do_service_add(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
    parsed: dict[str, object],
) -> dict[str, str]:
    try:
        result = await api_client.calendar_upsert_service(
            project_id=project_id,
            actor=operator,
            actor_role="operator",
            internal_token=internal_token,
            name=parsed["name"],
            duration_minutes=parsed["duration_minutes"],
            working_hours=parsed["working_hours"],
            service_days=parsed["service_days"],
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.warning(
            "calendar_service_add_failed",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _SERVICE_FALLBACK)
        return {"status": "accepted", "route": "calendar_service", "decision": "api_error"}
    rule_id = result.get("id")
    await send_dm(
        normalized.chat_id,
        _SERVICE_ADDED.format(rule_id=rule_id, name=parsed["name"]),
    )
    return {"status": "accepted", "route": "calendar_service", "decision": "added"}


async def _do_service_remove(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    project_id: int,
    operator: str,
    internal_token: str,
    rule_id: int,
) -> dict[str, str]:
    try:
        await api_client.calendar_delete_service(
            project_id=project_id,
            rule_id=rule_id,
            actor=operator,
            actor_role="operator",
            internal_token=internal_token,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.warning(
            "calendar_service_remove_failed",
            extra={"project_id": project_id, "operator": operator},
        )
        await send_dm(normalized.chat_id, _SERVICE_FALLBACK)
        return {"status": "accepted", "route": "calendar_service", "decision": "api_error"}
    await send_dm(normalized.chat_id, _SERVICE_REMOVED.format(rule_id=rule_id))
    return {"status": "accepted", "route": "calendar_service", "decision": "removed"}
