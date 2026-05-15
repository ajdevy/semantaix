"""Multi-operator resolver for bot_gateway (Epic 10 story 10.07).

Replaces the implicit "only the primary operator counts" gate with a
lookup against the api's `operators` registry. Falls back to the
configured primary operator on api failure so an api outage does not
disable the operator commands for the primary admin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from services.bot_gateway.app.api_client import ApiClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedOperator:
    username: str
    chat_id: int | None
    project_id: int | None
    is_active: bool
    source: str  # "registry" or "primary_fallback"


async def resolve_operator_for_sender(
    *,
    username: str | None,
    api_client: ApiClient,
    primary_operator_username: str,
    primary_operator_chat_id: int | None = None,
) -> ResolvedOperator | None:
    """Resolve a Telegram username to a registered operator, if any.

    Returns:
        - ResolvedOperator(source="registry") for a registered active operator.
        - ResolvedOperator(source="primary_fallback") when api is unreachable
          but the username matches the configured primary operator.
        - None otherwise (non-operator sender, inactive operator, or
          unknown operator with no primary fallback).
    """
    if not username:
        return None
    try:
        record = await api_client.find_operator_by_username(username=username)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "operator_lookup_http_error",
            extra={"username": username, "status": exc.response.status_code},
        )
        record = None
        api_unreachable = True
    except (httpx.RequestError, httpx.TransportError, OSError) as exc:
        logger.warning(
            "operator_lookup_network_error",
            extra={"username": username, "error": str(exc)},
        )
        record = None
        api_unreachable = True
    else:
        api_unreachable = False

    if record is not None and bool(record.get("is_active", True)):
        return ResolvedOperator(
            username=str(record["username"]),
            chat_id=(
                int(record["chat_id"])
                if record.get("chat_id") is not None
                else None
            ),
            project_id=int(record["project_id"]),
            is_active=True,
            source="registry",
        )
    if record is not None and not bool(record.get("is_active", True)):
        # Explicitly deactivated — refuse even if the api responded.
        return None
    if api_unreachable and username == primary_operator_username:
        return ResolvedOperator(
            username=username,
            chat_id=primary_operator_chat_id,
            project_id=None,
            is_active=True,
            source="primary_fallback",
        )
    return None
