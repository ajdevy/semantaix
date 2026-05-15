"""Admin-only Telegram slash commands for Epic 10 story 10.04.

The handlers are pure functions over the bot's `ApiClient`. The
dispatcher (`handle_admin_project_command`) gates on
`settings.admin_telegram_username` so a non-admin sender falls through
to the rest of the routing chain. Responses are sent back to Telegram
via the injected `send_dm` coroutine, which the wiring code in
`services/bot_gateway/app/main.py` supplies.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.operator_files import OperatorFileRepository
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

_PROJECTS_LIST_RE = re.compile(r"^\s*/projects(?:\s|$)", re.IGNORECASE)
_PROJECT_NEW_RE = re.compile(
    r"^\s*/project_new\s+(?P<slug>\S+)\s+(?P<name>.+)$", re.IGNORECASE
)
_OPERATOR_ADD_RE = re.compile(
    r"^\s*/operator_add\s+(?P<username>@\S+)\s+(?P<project_slug>\S+)"
    r"(?:\s+(?P<chat_id>\d+))?\s*$",
    re.IGNORECASE,
)
_OPERATOR_REMOVE_RE = re.compile(
    r"^\s*/operator_remove\s+(?P<username>@\S+)\s*$", re.IGNORECASE
)
_OPERATOR_LIST_RE = re.compile(r"^\s*/operator_list(?:\s|$)", re.IGNORECASE)
_FILE_ASSIGN_RE = re.compile(
    r"^\s*/file_assign\s+#(?P<short_id>\S+)\s+(?P<project_slug>\S+)\s*$",
    re.IGNORECASE,
)

SendDmFn = Callable[[int, str], Awaitable[Any]]


def _format_project(project: dict[str, Any]) -> str:
    description = project.get("description") or ""
    suffix = f" — {description}" if description else ""
    return (
        f"📁 #{project['id']} · {project['slug']} · "
        f"{project.get('name', '')}{suffix}"
    )


def _format_operator(operator: dict[str, Any]) -> str:
    chat = operator.get("chat_id") or "—"
    active = "active" if operator.get("is_active") else "inactive"
    return (
        f"👤 {operator['username']} → project #{operator['project_id']} · "
        f"chat={chat} · {active}"
    )


async def _resolve_project_id(api_client: ApiClient, slug: str) -> int | None:
    projects = await api_client.list_projects()
    for project in projects.get("items", []):
        if str(project.get("slug")) == slug:
            return int(project["id"])
    return None


async def handle_list_projects(
    *, chat_id: int, api_client: ApiClient, send_dm: SendDmFn
) -> dict[str, str]:
    body = await api_client.list_projects()
    items = body.get("items", [])
    if not items:
        await send_dm(chat_id, "Проектов пока нет.")
        return {"status": "ok", "route": "projects_list", "count": "0"}
    lines = ["📂 Проекты:"] + [_format_project(p) for p in items]
    await send_dm(chat_id, "\n".join(lines))
    return {"status": "ok", "route": "projects_list", "count": str(len(items))}


async def handle_create_project(
    *,
    chat_id: int,
    slug: str,
    name: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    try:
        body = await api_client.create_project(slug=slug, name=name)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 409:
            await send_dm(chat_id, f"Проект {slug} уже существует.")
            return {
                "status": "ok",
                "route": "project_new",
                "decision": "conflict",
            }
        await send_dm(chat_id, f"Не удалось создать проект ({status}).")
        return {
            "status": "error",
            "route": "project_new",
            "http_status": str(status),
        }
    await send_dm(chat_id, f"Проект #{body['id']} «{body['slug']}» создан.")
    return {
        "status": "ok",
        "route": "project_new",
        "project_id": str(body["id"]),
    }


async def handle_add_operator(
    *,
    chat_id: int,
    username: str,
    project_slug: str,
    operator_chat_id: int | None,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    project_id = await _resolve_project_id(api_client, project_slug)
    if project_id is None:
        await send_dm(chat_id, f"Проект {project_slug} не найден.")
        return {
            "status": "ok",
            "route": "operator_add",
            "decision": "project_missing",
        }
    try:
        await api_client.attach_operator(
            username=username,
            project_id=project_id,
            chat_id=operator_chat_id,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 409:
            await send_dm(chat_id, f"Оператор {username} уже существует.")
            return {
                "status": "ok",
                "route": "operator_add",
                "decision": "conflict",
            }
        await send_dm(chat_id, f"Не удалось добавить оператора ({status}).")
        return {
            "status": "error",
            "route": "operator_add",
            "http_status": str(status),
        }
    await send_dm(
        chat_id,
        f"Оператор {username} привязан к проекту {project_slug}.",
    )
    return {"status": "ok", "route": "operator_add"}


async def handle_remove_operator(
    *,
    chat_id: int,
    username: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    try:
        await api_client.detach_operator(username=username)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            await send_dm(chat_id, f"Оператор {username} не найден.")
            return {
                "status": "ok",
                "route": "operator_remove",
                "decision": "missing",
            }
        await send_dm(chat_id, f"Ошибка удаления ({status}).")
        return {
            "status": "error",
            "route": "operator_remove",
            "http_status": str(status),
        }
    await send_dm(chat_id, f"Оператор {username} деактивирован.")
    return {"status": "ok", "route": "operator_remove"}


async def handle_list_operators(
    *, chat_id: int, api_client: ApiClient, send_dm: SendDmFn
) -> dict[str, str]:
    body = await api_client.list_operators()
    items = body.get("items", [])
    if not items:
        await send_dm(chat_id, "Операторов пока нет.")
        return {"status": "ok", "route": "operator_list", "count": "0"}
    lines = ["👥 Операторы:"] + [_format_operator(o) for o in items]
    await send_dm(chat_id, "\n".join(lines))
    return {"status": "ok", "route": "operator_list", "count": str(len(items))}


async def handle_file_assign(
    *,
    chat_id: int,
    short_id: str,
    project_slug: str,
    api_client: ApiClient,
    send_dm: SendDmFn,
) -> dict[str, str]:
    project_id = await _resolve_project_id(api_client, project_slug)
    if project_id is None:
        await send_dm(chat_id, f"Проект {project_slug} не найден.")
        return {
            "status": "ok",
            "route": "file_assign",
            "decision": "project_missing",
        }
    try:
        body = await api_client.find_candidate_by_short_id(short_id=short_id)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            await send_dm(
                chat_id,
                f"Файл #{short_id} не найден (или загружен до Epic 10).",
            )
            return {
                "status": "ok",
                "route": "file_assign",
                "decision": "candidate_missing",
            }
        await send_dm(chat_id, f"Ошибка поиска файла ({status}).")
        return {
            "status": "error",
            "route": "file_assign",
            "http_status": str(status),
        }
    candidate_id = int(body["candidate_id"])
    try:
        await api_client.reassign_candidate(
            candidate_id=candidate_id, project_id=project_id
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        await send_dm(chat_id, f"Не удалось переназначить ({status}).")
        return {
            "status": "error",
            "route": "file_assign",
            "http_status": str(status),
        }
    await send_dm(
        chat_id,
        f"Файл #{short_id} → проект {project_slug}.",
    )
    return {"status": "ok", "route": "file_assign"}


async def handle_admin_project_command(
    *,
    normalized: NormalizedTelegramMessage,
    api_client: ApiClient,
    send_dm: SendDmFn,
    admin_username: str,
    operator_file_repository: OperatorFileRepository | None = None,
) -> dict[str, str] | None:
    """Top-level dispatcher for admin-only project/operator/file commands.

    Returns None when the sender is not the configured admin so the
    rest of the bot routing chain proceeds untouched. Otherwise tries
    each trigger regex; the first match wins. An admin sending a
    `/<unknown>` admin command receives a short hint.

    `operator_file_repository` is unused by the current commands but
    will be needed in 10.05 for short_id ↔ telegram_file_id matching;
    accepting it now keeps the wiring stable.
    """
    _ = operator_file_repository
    if not normalized.username or normalized.username != admin_username:
        return None
    text = normalized.text or ""

    if _PROJECTS_LIST_RE.match(text):
        return await handle_list_projects(
            chat_id=normalized.chat_id,
            api_client=api_client,
            send_dm=send_dm,
        )

    m = _PROJECT_NEW_RE.match(text)
    if m:
        return await handle_create_project(
            chat_id=normalized.chat_id,
            slug=m.group("slug"),
            name=m.group("name").strip(),
            api_client=api_client,
            send_dm=send_dm,
        )

    m = _OPERATOR_ADD_RE.match(text)
    if m:
        chat_raw = m.group("chat_id")
        return await handle_add_operator(
            chat_id=normalized.chat_id,
            username=m.group("username"),
            project_slug=m.group("project_slug"),
            operator_chat_id=int(chat_raw) if chat_raw else None,
            api_client=api_client,
            send_dm=send_dm,
        )

    m = _OPERATOR_REMOVE_RE.match(text)
    if m:
        return await handle_remove_operator(
            chat_id=normalized.chat_id,
            username=m.group("username"),
            api_client=api_client,
            send_dm=send_dm,
        )

    if _OPERATOR_LIST_RE.match(text):
        return await handle_list_operators(
            chat_id=normalized.chat_id,
            api_client=api_client,
            send_dm=send_dm,
        )

    m = _FILE_ASSIGN_RE.match(text)
    if m:
        return await handle_file_assign(
            chat_id=normalized.chat_id,
            short_id=m.group("short_id"),
            project_slug=m.group("project_slug"),
            api_client=api_client,
            send_dm=send_dm,
        )

    return None
