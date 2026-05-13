import logging
import re
import uuid

from fastapi import HTTPException, Request

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.hitl import HitlTicketRepository
from services.api.app.telegram_bot_sender import TelegramBotSender
from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.persistence import persist_normalized_message
from services.bot_gateway.app.telegram_update import (
    NormalizedTelegramMessage,
    TelegramUpdateValidationError,
    normalize_update,
)

app = create_service_app("bot_gateway")
logger = logging.getLogger(__name__)
settings = get_settings()
hitl_ticket_repository = HitlTicketRepository(settings.hitl_ticket_db_path)
api_client = ApiClient(base_url=settings.api_internal_base_url)
telegram_bot_sender = TelegramBotSender(bot_token=settings.telegram_bot_token)

_TICKET_REF = re.compile(r"HITL\s+ticket\s+#(\d+)", re.IGNORECASE)

# Persona dialog: marker prefix on the prompt + same prefix on the parse-fail
# nudge so the operator's reply to either lands in the dialog branch.
_PERSONA_MARKER = "📝 Как нас будут звать?"
_PERSONA_PROMPT = (
    "📝 Как нас будут звать? Ответьте на это сообщение в формате: "
    "«Имя Фамилия»"
)
_PERSONA_REPROMPT = (
    "📝 Как нас будут звать? Не разобрал — пришлите Имя и Фамилию "
    "через пробел, например: Анна Иванова"
)
_PERSONA_TRIGGER_RE = re.compile(
    r"^/persona(\b|$)"
    r"|^переименуй\b"
    r"|^переименовать\b"
    r"|^смени(те)?\s+имя\b"
    r"|^поменяй(те)?\s+имя\b"
    r"|^новое\s+имя\b",
    re.IGNORECASE,
)


def _effective_operator_username() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or settings.hitl_primary_operator_username
    )


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
        key="hitl_primary_operator_chat_id",
        value=chat_id,
        updated_by=username,
    )
    hitl_ticket_repository.set_runtime_config(
        key="telegram_alert_chat_id",
        value=chat_id,
        updated_by=username,
    )
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value=chat_id,
        updated_by=username,
    )
    return {
        "status": "configured",
        "hitl_primary_operator_username": operator_username,
        "telegram_alert_chat_id": chat_id,
        "hitl_primary_operator_chat_id": chat_id,
    }


async def _safe_send_text(*, chat_id: int, text: str) -> None:
    """Send a Telegram reply to the operator; swallow missing-token errors.

    The bot_gateway needs to talk back to operators for the persona dialog,
    but unit tests run without a real bot token. We log + drop instead of
    failing the webhook (Telegram would retry it).
    """
    try:
        await telegram_bot_sender.send_message(chat_id=chat_id, text=text)
    except Exception as exc:  # broad: best-effort outbound message
        logger.warning(
            "bot_gateway_outbound_failed",
            extra={"chat_id": chat_id, "error": str(exc)},
        )


async def _apply_persona(
    *, chat_id: int, username: str, first_name: str, last_name: str
) -> dict[str, str]:
    try:
        result = await api_client.set_persona(
            first_name=first_name,
            last_name=last_name,
            updated_by=username,
        )
    except Exception as exc:
        logger.warning(
            "persona_update_failed",
            extra={"username": username, "error": str(exc)},
        )
        await _safe_send_text(
            chat_id=chat_id,
            text="Не получилось обновить имя — попробуйте чуть позже.",
        )
        return {"status": "persona_update_failed"}
    await _safe_send_text(
        chat_id=chat_id,
        text=(
            f"Готово, теперь меня зовут "
            f"{result.get('first_name', first_name)} "
            f"{result.get('last_name', last_name)}."
        ),
    )
    return {
        "status": "persona_updated",
        "first_name": str(result.get("first_name", first_name)),
        "last_name": str(result.get("last_name", last_name)),
    }


async def _handle_persona_command(
    *, normalized: NormalizedTelegramMessage
) -> dict[str, str] | None:
    username = normalized.username
    text = normalized.text
    is_persona_reply = (
        normalized.reply_to_text is not None
        and normalized.reply_to_text.startswith(_PERSONA_MARKER)
    )
    trigger_match = _PERSONA_TRIGGER_RE.match(text)

    if not is_persona_reply and trigger_match is None:
        return None

    if username != settings.hitl_config_admin_username:
        return {"status": "ignored", "reason": "unauthorized_persona"}

    if is_persona_reply:
        parts = text.split()
        if len(parts) != 2:
            await _safe_send_text(chat_id=normalized.chat_id, text=_PERSONA_REPROMPT)
            return {"status": "persona_invalid_reply"}
        return await _apply_persona(
            chat_id=normalized.chat_id,
            username=username,
            first_name=parts[0],
            last_name=parts[1],
        )

    # Slash command or natural trigger. /persona Имя Фамилия → one-shot.
    if text.lower().startswith("/persona"):
        parts = text.split()
        if len(parts) == 3:
            return await _apply_persona(
                chat_id=normalized.chat_id,
                username=username,
                first_name=parts[1],
                last_name=parts[2],
            )

    await _safe_send_text(chat_id=normalized.chat_id, text=_PERSONA_PROMPT)
    return {"status": "persona_prompt_sent"}


def _extract_ticket_id(reply_to_text: str | None) -> int | None:
    if not reply_to_text:
        return None
    match = _TICKET_REF.search(reply_to_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex already constrains to digits
        return None


def _fallback_open_ticket_for_operator(operator_username: str) -> int | None:
    """Return the single open-assigned ticket id for this operator, if any."""
    open_tickets = [
        t
        for t in hitl_ticket_repository.list_all()
        if t.operator_username == operator_username and t.status == "assigned"
    ]
    if len(open_tickets) == 1:
        return open_tickets[0].id
    return None


async def _handle_operator_reply(normalized: NormalizedTelegramMessage) -> dict[str, str]:
    ticket_id = _extract_ticket_id(normalized.reply_to_text)
    if ticket_id is None:
        ticket_id = _fallback_open_ticket_for_operator(normalized.username or "")
    if ticket_id is None:
        return {
            "status": "ignored",
            "reason": "operator_reply_unmatched",
        }
    try:
        await api_client.deliver_operator_reply(
            ticket_id=ticket_id,
            operator_username=normalized.username or "",
            reply_text=normalized.text,
        )
    except Exception as exc:  # broad: best-effort; api emits incidents internally
        logger.warning(
            "operator_reply_delivery_failed",
            extra={"ticket_id": ticket_id, "error": str(exc)},
        )
        return {"status": "failed", "reason": "operator_reply_delivery_failed"}
    return {"status": "operator_reply_delivered", "ticket_id": str(ticket_id)}


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

    persona_result = await _handle_persona_command(normalized=normalized)
    if persona_result is not None:
        response = {"trace_id": trace_id}
        response.update(persona_result)
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

    operator_username = _effective_operator_username()
    if normalized.username and normalized.username == operator_username:
        operator_result = await _handle_operator_reply(normalized)
        response = {"trace_id": trace_id}
        response.update(operator_result)
        return response

    try:
        await api_client.forward_inbound(
            text=normalized.text,
            chat_id=normalized.chat_id,
            customer_username=normalized.username,
            trace_id=trace_id,
        )
    except Exception as exc:
        # Best-effort forward; api emits incidents on its side. Webhook
        # still acks Telegram so it doesn't retry the same update.
        logger.warning(
            "inbound_forward_failed",
            extra={"trace_id": trace_id, "error": str(exc)},
        )
        return {"status": "accepted", "forward": "failed", "trace_id": trace_id}

    return {"status": "accepted", "trace_id": trace_id}
