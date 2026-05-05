import logging
import uuid

from fastapi import HTTPException, Request

from platform_common.app_factory import create_service_app
from services.bot_gateway.app.telegram_update import (
    TelegramUpdateValidationError,
    normalize_update,
)


app = create_service_app("bot_gateway")
logger = logging.getLogger(__name__)


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
    return {"status": "accepted", "trace_id": trace_id}
