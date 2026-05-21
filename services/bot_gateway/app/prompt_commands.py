"""Telegram slash commands for managing per-project LLM prompts.

Handlers route to the api over an Authorization: Bearer + ``as_user``
session so the api enforces admin-or-operator-of-this-project access.
Editing uses a two-step flow: ``/prompt_set <slug> <name>`` arms a
pending edit and the **next non-command message** from the same user is
captured as the new value.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from services.bot_gateway.app.api_client import ApiClient, ApiError
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

logger = logging.getLogger(__name__)

SendDmFn = Callable[[int, str], Awaitable[Any]]

_TELEGRAM_TEXT_MAX = 3500
_HISTORY_LIMIT = 20

_LIST_RE = re.compile(r"^\s*/prompts(?:\s+(?P<slug>\S+))?\s*$", re.IGNORECASE)
_SHOW_RE = re.compile(
    r"^\s*/prompt_show\s+(?P<slug>\S+)\s+(?P<name>\S+)\s*$", re.IGNORECASE
)
_SET_RE = re.compile(
    r"^\s*/prompt_set\s+(?P<slug>\S+)\s+(?P<name>\S+)\s*$", re.IGNORECASE
)
_CANCEL_RE = re.compile(r"^\s*/prompt_cancel\s*$", re.IGNORECASE)
_HISTORY_RE = re.compile(
    r"^\s*/prompt_history\s+(?P<slug>\S+)\s+(?P<name>\S+)\s*$", re.IGNORECASE
)
_RESTORE_RE = re.compile(
    r"^\s*/prompt_restore\s+(?P<slug>\S+)\s+(?P<name>\S+)\s+"
    r"(?P<version>\d+)\s*$",
    re.IGNORECASE,
)

PROMPT_COMMAND_PREFIXES = (
    "/prompts",
    "/prompt_show",
    "/prompt_set",
    "/prompt_cancel",
    "/prompt_history",
    "/prompt_restore",
)


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_TEXT_MAX:
        return text
    return text[: _TELEGRAM_TEXT_MAX - 32] + "\n…[truncated; open admin UI]"


def _format_detail(exc: ApiError) -> str:
    if exc.detail:
        return f" — {exc.detail}"
    return ""


async def handle_prompt_command(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str] | None:
    text = normalized.text or ""
    username = normalized.username
    chat_id = normalized.chat_id
    if not username:
        return None
    if not any(
        text.lstrip().lower().startswith(p) for p in PROMPT_COMMAND_PREFIXES
    ):
        return None

    if _CANCEL_RE.match(text):
        return await _handle_cancel(
            chat_id=chat_id,
            requester=username,
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    match = _LIST_RE.match(text)
    if match:
        return await _handle_list(
            chat_id=chat_id,
            requester=username,
            slug=match.group("slug"),
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    match = _SHOW_RE.match(text)
    if match:
        return await _handle_show(
            chat_id=chat_id,
            requester=username,
            slug=match.group("slug"),
            name=match.group("name"),
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    match = _SET_RE.match(text)
    if match:
        return await _handle_set(
            chat_id=chat_id,
            requester=username,
            slug=match.group("slug"),
            name=match.group("name"),
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    match = _HISTORY_RE.match(text)
    if match:
        return await _handle_history(
            chat_id=chat_id,
            requester=username,
            slug=match.group("slug"),
            name=match.group("name"),
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    match = _RESTORE_RE.match(text)
    if match:
        return await _handle_restore(
            chat_id=chat_id,
            requester=username,
            slug=match.group("slug"),
            name=match.group("name"),
            version=int(match.group("version")),
            api_client=api_client,
            send_dm=send_dm,
            internal_token=internal_token,
        )

    # Looks like a prompt command but didn't match any pattern → usage hint.
    await send_dm(
        chat_id,
        "Не понял команду. Используйте:\n"
        "/prompts <slug>\n"
        "/prompt_show <slug> <name>\n"
        "/prompt_set <slug> <name>\n"
        "/prompt_cancel\n"
        "/prompt_history <slug> <name>\n"
        "/prompt_restore <slug> <name> <version>",
    )
    return {"status": "ok", "route": "prompt_usage_hint"}


async def _handle_list(
    *,
    chat_id: int,
    requester: str,
    slug: str | None,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    if not slug:
        await send_dm(chat_id, "Использование: /prompts <project_slug>")
        return {"status": "ok", "route": "prompt_list_usage"}
    try:
        body = await api_client.list_project_prompts(
            project_slug=slug,
            requester_username=requester,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id, f"Не удалось получить промты{_format_detail(exc)}."
        )
        return {"status": "error", "route": "prompt_list", "error": str(exc.detail)}
    items = body.get("items", [])
    lines = [f"📋 Промты проекта {slug}:"]
    for item in items:
        source = "default" if item.get("is_default") else "override"
        lines.append(
            f"• {item['prompt_name']} · v{item['version']} · {source}"
        )
    await send_dm(chat_id, "\n".join(lines))
    return {"status": "ok", "route": "prompt_list"}


async def _handle_show(
    *,
    chat_id: int,
    requester: str,
    slug: str,
    name: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    try:
        body = await api_client.get_project_prompt(
            project_slug=slug,
            prompt_name=name,
            requester_username=requester,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id, f"Не удалось получить промт{_format_detail(exc)}."
        )
        return {"status": "error", "route": "prompt_show", "error": str(exc.detail)}
    header = (
        f"📝 {name} (проект {slug}) · v{body.get('version', 0)} · "
        f"{'default' if body.get('is_default') else 'override'}"
    )
    await send_dm(
        chat_id, header + "\n\n" + _truncate_for_telegram(str(body.get("value", "")))
    )
    return {"status": "ok", "route": "prompt_show"}


async def _handle_set(
    *,
    chat_id: int,
    requester: str,
    slug: str,
    name: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    try:
        await api_client.arm_prompt_pending_edit(
            project_slug=slug,
            prompt_name=name,
            requester_username=requester,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id, f"Не удалось подготовить редактирование{_format_detail(exc)}."
        )
        return {"status": "error", "route": "prompt_set", "error": str(exc.detail)}
    await send_dm(
        chat_id,
        f"✏️ Готов записать новое значение для {name} (проект {slug}). "
        "Отправьте следующим сообщением только текст промта — он "
        "заменит текущее значение. /prompt_cancel отменяет.",
    )
    return {"status": "ok", "route": "prompt_set_armed"}


async def _handle_cancel(
    *,
    chat_id: int,
    requester: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    body = await api_client.cancel_pending_prompt_edit(
        requester_username=requester, internal_token=internal_token
    )
    deleted = bool(body.get("deleted"))
    await send_dm(
        chat_id,
        "Отменено." if deleted else "Активного редактирования нет.",
    )
    return {"status": "ok", "route": "prompt_cancel"}


async def _handle_history(
    *,
    chat_id: int,
    requester: str,
    slug: str,
    name: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    try:
        body = await api_client.get_project_prompt(
            project_slug=slug,
            prompt_name=name,
            requester_username=requester,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id, f"Не удалось получить историю{_format_detail(exc)}."
        )
        return {
            "status": "error",
            "route": "prompt_history",
            "error": str(exc.detail),
        }
    items = body.get("history", [])[:_HISTORY_LIMIT]
    if not items:
        await send_dm(chat_id, f"История пуста для {name} (проект {slug}).")
        return {"status": "ok", "route": "prompt_history_empty"}
    lines = [f"🕓 История {name} (проект {slug}):"]
    for item in items:
        lines.append(
            f"v{item['version']} — {item.get('edited_by', '?')} — "
            f"{item.get('created_at', '?')}"
        )
    await send_dm(chat_id, "\n".join(lines))
    return {"status": "ok", "route": "prompt_history"}


async def _handle_restore(
    *,
    chat_id: int,
    requester: str,
    slug: str,
    name: str,
    version: int,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str]:
    try:
        body = await api_client.restore_project_prompt(
            project_slug=slug,
            prompt_name=name,
            version=version,
            requester_username=requester,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id, f"Не удалось восстановить версию{_format_detail(exc)}."
        )
        return {"status": "error", "route": "prompt_restore", "error": str(exc.detail)}
    await send_dm(
        chat_id,
        f"♻️ {name} (проект {slug}) восстановлен до v{version}. "
        f"Новая активная версия: v{body.get('version', '?')}.",
    )
    return {"status": "ok", "route": "prompt_restore"}


async def dispatch_pending_prompt_edit(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    internal_token: str,
) -> dict[str, str] | None:
    """If the sender has an armed pending edit, treat this message's text as
    the new prompt value and apply it. Slash commands are left alone so the
    user can /prompt_cancel or run another /prompt_* command without losing
    their pending state to an accidental capture.
    """
    text = normalized.text or ""
    if not text or text.lstrip().startswith("/"):
        return None
    username = normalized.username
    chat_id = normalized.chat_id
    if not username:
        return None
    try:
        pending = await api_client.peek_pending_prompt_edit(
            requester_username=username, internal_token=internal_token
        )
    except (httpx.HTTPError, ApiError) as exc:
        logger.warning(
            "prompt_pending_peek_failed",
            extra={"username": username, "error": repr(exc)},
        )
        return None
    if pending is None:
        return None
    try:
        body = await api_client.consume_pending_prompt_edit(
            value=text,
            requester_username=username,
            internal_token=internal_token,
        )
    except ApiError as exc:
        await send_dm(
            chat_id,
            f"Не сохранено{_format_detail(exc)}. Попробуйте /prompt_set ещё раз.",
        )
        return {
            "status": "error",
            "route": "prompt_pending_consume",
            "error": str(exc.detail),
        }
    preview = text.strip().replace("\n", " ")
    if len(preview) > 200:
        preview = preview[:200] + "…"
    await send_dm(
        chat_id,
        f"💾 Сохранено как v{body.get('version', '?')} "
        f"({body.get('prompt_name')}, проект {body.get('project_slug')}). "
        f"Превью: {preview}",
    )
    return {"status": "ok", "route": "prompt_pending_consumed"}
