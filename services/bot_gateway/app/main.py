import logging
import re
import uuid

import httpx
from fastapi import BackgroundTasks, HTTPException, Request

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.hitl import HitlTicketRepository
from services.api.app.russian_text import get_russian_normalizer
from services.api.app.telegram_bot_sender import TelegramBotSender
from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.kb_intent import KbIntent, detect_kb_intent
from services.bot_gateway.app.persistence import persist_normalized_message
from services.bot_gateway.app.telegram_file_download import (
    TelegramFileDownloader,
    TelegramFileDownloadError,
)
from services.bot_gateway.app.telegram_update import (
    NormalizedTelegramMessage,
    TelegramAttachment,
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

_HELP_TRIGGER_RE = re.compile(r"^\s*/help\b", re.IGNORECASE)

_HELP_TEXT = (
    "🤖 Команды оператора:\n"
    "\n"
    "📚 База знаний\n"
    "• /kb_add — добавить вложение или текст в базу знаний "
    "(прикрепите файл или ответьте текстом).\n"
    "• /kb_add confidential — то же, но пометить как конфиденциально "
    "(не цитируется клиентам).\n"
    "• Свободный текст: «добавь в базу …», «сохрани в kb …», "
    "«запомни это для базы знаний …» — то же, что /kb_add.\n"
    "\n"
    "👤 Имя бота (админ)\n"
    "• /persona Имя Фамилия — переименовать бота.\n"
    "• /persona — открыть диалог переименования.\n"
    "\n"
    "⚙️ Маршрутизация HITL (админ)\n"
    "• /hitl_config @username chat_id — назначить оператора "
    "и чат для алертов.\n"
    "\n"
    "💬 Ответ клиенту\n"
    "• Просто ответьте на сообщение бота, в котором указан "
    "«HITL ticket #N» — реплика уйдёт клиенту и закроет тикет. "
    "Если у вас один открытый тикет, ответ можно отправить и без цитирования.\n"
    "\n"
    "ℹ️ /help — показать эту справку."
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


async def _handle_help_command(
    *, normalized: NormalizedTelegramMessage
) -> dict[str, str] | None:
    if not _HELP_TRIGGER_RE.match(normalized.text or ""):
        return None
    operator_username = _effective_operator_username()
    if not normalized.username or normalized.username != operator_username:
        return None
    await _send_dm(normalized.chat_id, _HELP_TEXT)
    return {"status": "help_sent"}


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


_KB_ATTACHMENT_TYPE_MAP: dict[str, str] = {
    "document": "_from_mime_or_name",
    "photo": "image",
    "audio": "audio",
    "voice": "audio",
    "video": "video",
}


def _kb_source_file_type(attachment: TelegramAttachment) -> str | None:
    """Map a Telegram attachment to a `source_file_type` accepted by the API."""
    explicit = _KB_ATTACHMENT_TYPE_MAP.get(attachment.kind)
    if explicit and explicit != "_from_mime_or_name":
        return explicit
    name = (attachment.file_name or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".pptx"):
        return "pptx"
    if name.endswith(".txt"):
        return "txt"
    mime = (attachment.mime_type or "").lower()
    if mime == "application/pdf":
        return "pdf"
    if mime in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }:
        return "docx"
    if mime in {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
    }:
        return "pptx"
    if mime.startswith("text/"):
        return "txt"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return None


def _kb_extension_for(attachment: TelegramAttachment, source_file_type: str) -> str:
    if attachment.file_name and "." in attachment.file_name:
        return attachment.file_name.rsplit(".", 1)[-1]
    fallback = {
        "pdf": "pdf",
        "docx": "docx",
        "pptx": "pptx",
        "txt": "txt",
        "image": "jpg",
        "audio": "ogg",
        "video": "mp4",
    }
    return fallback.get(source_file_type, "bin")


async def _send_dm(chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            await client.post(url, json={"chat_id": chat_id, "text": text})
        except Exception as exc:  # best-effort DM; do not block on Telegram errors
            logger.warning("operator_dm_failed", extra={"chat_id": chat_id, "error": str(exc)})


def _kb_attachment_count_word(count: int) -> str:
    if count == 1:
        return "файл"
    if 2 <= count <= 4:
        return "файла"
    return "файлов"


async def _process_operator_upload(
    *,
    normalized: NormalizedTelegramMessage,
    intent: KbIntent,
) -> None:
    downloader = TelegramFileDownloader(
        bot_token=settings.telegram_bot_token,
        storage_dir=settings.operator_upload_storage_dir,
        max_bytes=settings.operator_upload_max_bytes,
    )
    successes: list[dict] = []
    failures: list[tuple[str, str]] = []

    if not normalized.attachments:
        inline_body = (intent.cleaned_text or "").strip()
        try:
            result = await api_client.submit_operator_upload(
                operator_username=normalized.username or "",
                source_file_type="inline_text",
                source_file_name=None,
                stored_binary_path=None,
                is_confidential=intent.confidential,
                inline_text=inline_body,
                timeout_seconds=settings.operator_upload_api_timeout_seconds,
            )
            successes.append(result)
        except Exception as exc:
            failures.append(("inline_text", str(exc)))
    else:
        for attachment in normalized.attachments:
            label = attachment.file_name or attachment.file_id
            source_file_type = _kb_source_file_type(attachment)
            if source_file_type is None:
                failures.append((label, "unsupported_attachment_type"))
                continue
            extension = _kb_extension_for(attachment, source_file_type)
            try:
                downloaded = await downloader.download(
                    file_id=attachment.file_id,
                    suggested_extension=extension,
                    mime_type=attachment.mime_type,
                )
            except TelegramFileDownloadError as exc:
                failures.append((label, exc.reason))
                continue
            except Exception as exc:
                failures.append((label, f"download_failed:{exc}"))
                continue
            try:
                result = await api_client.submit_operator_upload(
                    operator_username=normalized.username or "",
                    source_file_type=source_file_type,
                    source_file_name=attachment.file_name,
                    stored_binary_path=str(downloaded.path),
                    is_confidential=intent.confidential,
                    timeout_seconds=settings.operator_upload_api_timeout_seconds,
                )
                successes.append(result)
            except Exception as exc:
                failures.append((label, f"api_failed:{exc}"))

    total_chunks = sum(int(item.get("inserted_chunks", 0) or 0) for item in successes)
    confidential_count = sum(
        1 for item in successes if item.get("is_confidential")
    )
    deduped = sum(1 for item in successes if item.get("deduplicated"))
    summary_lines = [
        f"✅ Добавлено в базу: {len(successes)} {_kb_attachment_count_word(len(successes))}, "
        f"{total_chunks} чанков, {confidential_count} помечен(о) confidential."
    ]
    if deduped:
        summary_lines.append(f"♻️ Из них уже было в базе: {deduped}.")
    for name, reason in failures:
        summary_lines.append(f"⚠️ Не удалось обработать {name}: {reason}")
    await _send_dm(normalized.chat_id, "\n".join(summary_lines))


async def _handle_kb_command(
    normalized: NormalizedTelegramMessage,
    background_tasks: BackgroundTasks,
) -> dict[str, str] | None:
    operator_username = _effective_operator_username()
    if not normalized.username or normalized.username != operator_username:
        return None
    intent = detect_kb_intent(
        text=normalized.text,
        caption=normalized.caption,
        normalizer=get_russian_normalizer(),
    )
    if intent is None:
        return None

    n_attachments = len(normalized.attachments)
    inline_body = (intent.cleaned_text or "").strip()
    if n_attachments == 0 and not inline_body:
        return {"status": "ignored", "reason": "no_attachments_no_inline_text"}

    if n_attachments == 0:
        ack = "Принял текст, добавляю в базу…"
    else:
        ack = (
            f"Принял {n_attachments} {_kb_attachment_count_word(n_attachments)}, обрабатываю…"
        )
    await _send_dm(normalized.chat_id, ack)

    background_tasks.add_task(_process_operator_upload, normalized=normalized, intent=intent)
    return {
        "status": "accepted",
        "kb_mode": intent.mode,
        "attachment_count": str(n_attachments),
    }


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
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
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

    kb_result = await _handle_kb_command(normalized, background_tasks)
    if kb_result is not None:
        response = {"trace_id": trace_id}
        response.update(kb_result)
        return response

    if normalized.text == "":
        logger.info(
            "telegram_attachment_only_message_ignored",
            extra={"trace_id": trace_id, "update_id": normalized.update_id},
        )
        return {"status": "ignored", "reason": "attachment_only", "trace_id": trace_id}

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

    help_result = await _handle_help_command(normalized=normalized)
    if help_result is not None:
        response = {"trace_id": trace_id}
        response.update(help_result)
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
