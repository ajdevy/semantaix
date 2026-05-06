import logging
import uuid

from fastapi import HTTPException, Request

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.hitl import HitlTicketRepository
from services.bot_gateway.app.persistence import persist_normalized_message
from services.bot_gateway.app.telegram_update import (
    TelegramUpdateValidationError,
    normalize_update,
)

app = create_service_app("bot_gateway")
logger = logging.getLogger(__name__)
settings = get_settings()
hitl_ticket_repository = HitlTicketRepository(settings.hitl_ticket_db_path)


def _handle_admin_hitl_command(*, username: str | None, text: str) -> dict[str, str] | None:
    if not text.startswith("/hitl_config"):
        return None

    if username != settings.hitl_config_admin_username:
        return {"status": "ignored", "reason": "unauthorized_hitl_config"}

    parts = text.split()
    if len(parts) != 3:
        return {"status": "ignored", "reason": "invalid_hitl_config_format"}

    _, operator_username, chat_id = parts
    if not operator_username.startswith("@"):
        return {"status": "ignored", "reason": "invalid_operator_username"}
    if not chat_id.isdigit():
        return {"status": "ignored", "reason": "invalid_chat_id"}

    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value=operator_username,
        updated_by=username,
    )
    hitl_ticket_repository.set_runtime_config(
        key="telegram_alert_chat_id",
        value=chat_id,
        updated_by=username,
    )
    return {
        "status": "configured",
        "hitl_primary_operator_username": operator_username,
        "telegram_alert_chat_id": chat_id,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, str]:
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_payload_type")

    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    try:
        normalized = normalize_update(payload)
    except TelegramUpdateValidationError as exc:
        logger.warning(
            "telegram_update_rejected",
            extra={"trace_id": trace_id, "reason": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if normalized is None:
        logger.info(
            "telegram_update_ignored",
            extra={"trace_id": trace_id, "update_id": payload.get("update_id")},
        )
        return {"status": "ignored", "trace_id": trace_id}

    admin_command_result = _handle_admin_hitl_command(
        username=normalized.username,
        text=normalized.text,
    )
    if admin_command_result is not None:
        response = {"trace_id": trace_id}
        response.update(admin_command_result)
        return response

    logger.info(
        "telegram_update_normalized",
        extra={
            "trace_id": trace_id,
            "update_id": normalized.update_id,
            "source_message_id": normalized.source_message_id,
            "chat_id": normalized.chat_id,
            "user_id": normalized.user_id,
        },
    )
    persisted = persist_normalized_message(
        telegram_user_id=normalized.user_id,
        source_message_id=normalized.source_message_id,
        text=normalized.text,
        trace_id=trace_id,
    )
    logger.info(
        "telegram_message_persisted",
        extra={
            "trace_id": trace_id,
            "source_message_id": normalized.source_message_id,
            "persisted": persisted,
        },
    )
    return {"status": "accepted", "trace_id": trace_id}
