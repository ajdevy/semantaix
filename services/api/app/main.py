import logging
import re
import time
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.answer_trace import AnswerTraceRepository
from services.api.app.answerers import AnswerContext, AnswerPipeline
from services.api.app.answerers.datetime_answerer import DateTimeAnswerer
from services.api.app.answerers.grounded_rag import GroundedRagAnswerer
from services.api.app.answerers.holiday_answerer import HolidayAnswerer
from services.api.app.answerers.weather_answerer import WeatherAnswerer
from services.api.app.answerers.weather_client import WeatherClient
from services.api.app.backups import BackupError, BackupRepository
from services.api.app.hitl import HitlTicketRepository
from services.api.app.incidents import IncidentRepository
from services.api.app.knowledge import KnowledgeCandidateRepository
from services.api.app.knowledge_moderation import KnowledgeModerationRepository
from services.api.app.nl_knowledge_ops import (
    NlKnowledgeOpsError,
    NlKnowledgeOpsRepository,
)
from services.api.app.openrouter_client import OpenRouterClient
from services.api.app.rag import RagRepository
from services.api.app.telegram_bot_sender import TelegramBotSender
from services.api.app.telegram_notifier import TelegramIncidentNotifier
from services.api.app.trace_corrections import (
    BRANCH_MODERATION,
    BRANCH_PUBLISH,
    TraceCorrectionError,
    TraceCorrectionRepository,
)

app = create_service_app("api")
logger = logging.getLogger(__name__)
openrouter_client = OpenRouterClient()
settings = get_settings()
incident_repository = IncidentRepository(
    db_path=settings.incident_db_path,
    dedup_window_seconds=settings.incident_dedup_window_seconds,
)
telegram_notifier = TelegramIncidentNotifier(
    bot_token=settings.telegram_bot_token,
    alert_chat_id=settings.telegram_alert_chat_id,
    alert_username=settings.telegram_alert_username,
)
hitl_ticket_repository = HitlTicketRepository(settings.hitl_ticket_db_path)
knowledge_candidate_repository = KnowledgeCandidateRepository(
    db_path=settings.knowledge_db_path,
    transcript_db_path=settings.persistence_db_path,
)
rag_repository = RagRepository(settings.rag_db_path)
knowledge_moderation_repository = KnowledgeModerationRepository(settings.knowledge_db_path)
telegram_bot_sender = TelegramBotSender(bot_token=settings.telegram_bot_token)
backup_repository = BackupRepository(
    db_path=settings.backup_db_path,
    archive_dir=settings.backup_archive_dir,
    source_paths=settings.backup_source_path_list(),
)
answer_trace_repository = AnswerTraceRepository(
    db_path=settings.answer_trace_db_path,
    snippet_max_chars=settings.answer_trace_snippet_max_chars,
)
nl_knowledge_ops_repository = NlKnowledgeOpsRepository(db_path=settings.nl_ops_db_path)
trace_correction_repository = TraceCorrectionRepository(db_path=settings.nl_ops_db_path)
weather_client = WeatherClient(base_url=settings.weather_provider_base_url)


def _effective_bot_persona() -> tuple[str, str]:
    return hitl_ticket_repository.get_bot_persona(
        default_first_name=settings.bot_persona_first_name,
        default_last_name=settings.bot_persona_last_name,
    )


answer_pipeline = AnswerPipeline(
    [
        DateTimeAnswerer(),
        HolidayAnswerer(),
        WeatherAnswerer(client=weather_client),
        GroundedRagAnswerer(
            rag_repository=rag_repository,
            openrouter_client=openrouter_client,
            persona_reader=_effective_bot_persona,
        ),
    ]
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "api", "message": "Semantaix API"}


class InboundMessageRequest(BaseModel):
    text: str
    chat_id: int | None = None
    trace_id: str | None = None
    customer_username: str | None = None


class IncidentEventRequest(BaseModel):
    fingerprint: str
    severity: str
    summary: str


class HitlRouteRequest(BaseModel):
    operator_username: str | None = None


class HitlReplyRequest(BaseModel):
    operator_username: str
    reply_text: str


class KnowledgeExtractRequest(BaseModel):
    conversation_id: int | None = None


class RagIngestRequest(BaseModel):
    source_id: str
    text: str


class RagRetrieveRequest(BaseModel):
    query: str
    limit: int = 3


class KnowledgeCandidateCreateRequest(BaseModel):
    text: str


class KnowledgeCandidateApproveRequest(BaseModel):
    edited_text: str | None = None


class BackupRestoreRequest(BaseModel):
    confirm_token: str
    target_root: str


class NlOpProposeRequest(BaseModel):
    user_id: str
    utterance: str
    tenant_id: str | None = None


class NlOpConfirmRequest(BaseModel):
    confirm_token: str


class TraceOpenRequest(BaseModel):
    tenant_id: str
    user_id: str


class TraceCorrectionRequest(BaseModel):
    tenant_id: str
    user_id: str
    edited_text: str
    branch: str


class OperatorUploadRequest(BaseModel):
    operator_username: str
    source_file_type: str
    source_file_name: str | None = None
    stored_binary_path: str | None = None
    is_confidential: bool = False
    inline_text: str | None = None


class BotPersonaRequest(BaseModel):
    first_name: str
    last_name: str
    description: str | None = None
    short_description: str | None = None
    updated_by: str


_PERSONA_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-']{0,31}$")
_PERSONA_DESCRIPTION_MAX = 512
_PERSONA_SHORT_DESCRIPTION_MAX = 120


def _validate_persona_name(value: str) -> str:
    candidate = value.strip()
    if not _PERSONA_NAME_RE.fullmatch(candidate):
        raise HTTPException(status_code=422, detail="invalid_persona_name")
    return candidate


def _effective_hitl_operator_username() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or settings.hitl_primary_operator_username
    )


def _effective_hitl_operator_chat_id() -> str | None:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_chat_id")
        or settings.hitl_primary_operator_chat_id
    )


def _effective_inbound_ack_message() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("inbound_ack_message")
        or settings.inbound_ack_message
    )


def _effective_default_country() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("default_country_code")
        or settings.default_country_code
    )


def _effective_default_timezone() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("default_timezone")
        or settings.default_timezone
    )


def _effective_default_location() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("default_location")
        or settings.default_location
    )


def _effective_default_language() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("default_language")
        or settings.default_language
    )


def _effective_grounding_threshold() -> float:
    raw = hitl_ticket_repository.get_runtime_config("rag_grounding_score_threshold")
    if raw is None:
        return settings.rag_grounding_score_threshold
    try:
        return float(raw)
    except ValueError:
        return settings.rag_grounding_score_threshold


def _build_answer_context(
    *,
    chat_id: int | None,
    customer_username: str | None,
    trace_id: str,
    now: datetime,
) -> AnswerContext:
    return AnswerContext(
        chat_id=chat_id,
        customer_username=customer_username,
        trace_id=trace_id,
        now=now,
        language=_effective_default_language(),
        country_code=_effective_default_country(),
        timezone=_effective_default_timezone(),
        location=_effective_default_location(),
        grounding_threshold=_effective_grounding_threshold(),
    )


async def _safe_send_message(
    *, chat_id: int, text: str, failure_summary: str, failure_kind: str
) -> bool:
    try:
        await telegram_bot_sender.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as exc:  # broad: ack/notify are best-effort
        incident = incident_repository.ingest(
            fingerprint="hitl_delivery_failures",
            severity="critical",
            summary=f"{failure_summary}: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type=failure_kind,
            details=f"chat_id={chat_id};error={exc}",
        )
        return False


async def _notify_hitl_operator_summary(*, ticket_id: int, summary: str) -> bool:
    """Short-form operator DM, used for status changes like route/assign."""
    chat_id_raw = _effective_hitl_operator_chat_id()
    if not chat_id_raw:
        return False
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        return False
    try:
        await telegram_bot_sender.send_message(
            chat_id=chat_id,
            text=f"HITL ticket #{ticket_id}: {summary}",
        )
    except Exception:  # broad: best-effort notification
        return False
    return True


async def _notify_hitl_operator_with_question(
    *,
    ticket_id: int,
    question: str,
    customer_username: str | None,
) -> bool:
    chat_id_raw = _effective_hitl_operator_chat_id()
    if not chat_id_raw:
        return False
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        return False
    customer_label = customer_username or "unknown"
    text = f"HITL ticket #{ticket_id} | from {customer_label} | {question}"
    return await _safe_send_message(
        chat_id=chat_id,
        text=text,
        failure_summary="HITL operator notification failed",
        failure_kind="hitl_operator_notify_failed",
    )


def _persist_answer_trace(
    *,
    trace_id: str,
    request_text: str,
    response_mode: str,
    guardrail_outcome: str,
    guardrail_reasons: list[str],
    guardrail_score: float | None,
    retrieval: list[dict[str, object]],
    latency_ms: int,
    limitations: list[str],
) -> str | None:
    try:
        trace = answer_trace_repository.write(
            trace_id=trace_id,
            request_text=request_text,
            model_id=settings.openrouter_model,
            model_provider="openrouter",
            latency_ms=latency_ms,
            response_mode=response_mode,
            guardrails_applied=True,
            guardrail_outcome=guardrail_outcome,
            guardrail_reasons=guardrail_reasons,
            guardrail_score=guardrail_score,
            retrieval=retrieval,
            confidence=guardrail_score,
            limitations=limitations,
        )
    except Exception as exc:
        incident = incident_repository.ingest(
            fingerprint="answer_trace_persistence_failures",
            severity="critical",
            summary=f"Answer trace persistence failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="answer_trace_failed",
            details=f"trace_id={trace_id};error={exc}",
        )
        return None
    return trace.trace_id


@app.post("/conversations/inbound")
async def conversations_inbound(request: InboundMessageRequest) -> dict[str, object]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="empty_text")

    started_at = time.perf_counter()
    trace_id = request.trace_id or str(uuid.uuid4())
    now = datetime.now(UTC)
    ctx = _build_answer_context(
        chat_id=request.chat_id,
        customer_username=request.customer_username,
        trace_id=trace_id,
        now=now,
    )

    pipeline_result = await answer_pipeline.run(question=request.text, ctx=ctx)
    latency_ms = int((time.perf_counter() - started_at) * 1000)

    if pipeline_result.handled:
        retrieval = pipeline_result.metadata.get("retrieval") or []
        guardrail_score = pipeline_result.metadata.get("guardrail_score")
        delivered = True
        if request.chat_id is not None:
            delivered = await _safe_send_message(
                chat_id=request.chat_id,
                text=pipeline_result.text or "",
                failure_summary="Inbound answer delivery failed",
                failure_kind="inbound_delivery_failed",
            )
        limitations: list[str] = [] if retrieval else ["no_retrieval"]
        persisted_trace_id = _persist_answer_trace(
            trace_id=trace_id,
            request_text=request.text,
            response_mode=pipeline_result.response_mode or "unknown",
            guardrail_outcome="valid",
            guardrail_reasons=[],
            guardrail_score=(
                float(guardrail_score) if guardrail_score is not None else None
            ),
            retrieval=list(retrieval),
            latency_ms=latency_ms,
            limitations=limitations,
        )
        return {
            "delivered": delivered,
            "escalated": False,
            "response_mode": pipeline_result.response_mode,
            "answer_text": pipeline_result.text,
            "answerer": pipeline_result.metadata.get("answerer"),
            "trace_id": persisted_trace_id,
        }

    ack_message = _effective_inbound_ack_message()
    if request.chat_id is not None:
        await _safe_send_message(
            chat_id=request.chat_id,
            text=ack_message,
            failure_summary="Inbound ack delivery failed",
            failure_kind="inbound_ack_failed",
        )

    ticket = hitl_ticket_repository.create(
        conversation_ref=request.text[:120],
        reason="awaiting_human_response",
        target_chat_id=request.chat_id,
    )
    hitl_ticket_repository.assign(
        ticket_id=ticket.id,
        operator_username=_effective_hitl_operator_username(),
    )
    await _notify_hitl_operator_with_question(
        ticket_id=ticket.id,
        question=request.text,
        customer_username=request.customer_username,
    )

    persisted_trace_id = _persist_answer_trace(
        trace_id=trace_id,
        request_text=request.text,
        response_mode="human_only",
        guardrail_outcome="escalated",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        latency_ms=latency_ms,
        limitations=["awaiting_human_response"],
    )
    return {
        "delivered": False,
        "escalated": True,
        "response_mode": "human_only",
        "hitl_ticket_id": ticket.id,
        "hitl_operator_username": _effective_hitl_operator_username(),
        "trace_id": persisted_trace_id,
    }


@app.post("/rag/ingest")
def ingest_rag(request: RagIngestRequest) -> dict[str, object]:
    try:
        inserted = rag_repository.ingest(source_id=request.source_id, text=request.text)
    except Exception as exc:
        incident = incident_repository.ingest(
            fingerprint="rag_ingest_failures",
            severity="critical",
            summary=f"RAG ingest failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="rag_ingest_failed",
            details=f"source_id={request.source_id}",
        )
        raise HTTPException(status_code=500, detail="rag_ingest_failed") from exc

    return {"source_id": request.source_id, "inserted_chunks": inserted}


@app.post("/rag/retrieve")
def retrieve_rag(request: RagRetrieveRequest) -> dict[str, object]:
    chunks = rag_repository.retrieve(query=request.query, limit=request.limit)
    return {
        "items": [
            {
                "id": chunk.id,
                "source_id": chunk.source_id,
                "chunk_text": chunk.chunk_text,
                "score": chunk.score,
            }
            for chunk in chunks
        ]
    }


@app.post("/incidents/events")
async def ingest_incident_event(request: IncidentEventRequest) -> dict[str, object]:
    incident = incident_repository.ingest(
        fingerprint=request.fingerprint,
        severity=request.severity,
        summary=request.summary,
    )
    sent = False
    delivery_status = "not_critical"
    if telegram_notifier.is_critical_event(
        fingerprint=request.fingerprint,
        severity=request.severity,
    ):
        last_sent_at = incident_repository.get_last_telegram_sent_at(incident.id)
        if last_sent_at is not None:
            elapsed = (datetime.now(UTC) - last_sent_at).total_seconds()
            if elapsed < settings.telegram_alert_debounce_seconds:
                delivery_status = "debounced"
            else:
                sent, delivery_status = await telegram_notifier.notify_if_critical(
                    incident_id=incident.id,
                    fingerprint=request.fingerprint,
                    severity=request.severity,
                    summary=request.summary,
                    occurrence_count=incident.occurrence_count,
                )
        else:
            sent, delivery_status = await telegram_notifier.notify_if_critical(
                incident_id=incident.id,
                fingerprint=request.fingerprint,
                severity=request.severity,
                summary=request.summary,
                occurrence_count=incident.occurrence_count,
            )
    incident_repository.append_event(
        incident_id=incident.id,
        event_type="telegram_notify",
        details=f"status={delivery_status}",
    )
    return {
        "id": incident.id,
        "fingerprint": incident.fingerprint,
        "status": incident.status,
        "occurrence_count": incident.occurrence_count,
        "telegram_notification_sent": sent,
        "telegram_delivery_status": delivery_status,
    }


@app.get("/incidents/{fingerprint}")
def get_incidents_by_fingerprint(fingerprint: str) -> dict[str, object]:
    incidents = incident_repository.get_by_fingerprint(fingerprint)
    return {
        "fingerprint": fingerprint,
        "items": [
            {
                "id": incident.id,
                "status": incident.status,
                "is_read": incident.is_read,
                "severity": incident.severity,
                "summary": incident.summary,
                "occurrence_count": incident.occurrence_count,
                "first_seen_at": incident.first_seen_at,
                "last_seen_at": incident.last_seen_at,
                "acknowledged_at": incident.acknowledged_at,
                "resolved_at": incident.resolved_at,
            }
            for incident in incidents
        ],
    }


@app.get("/incidents")
def list_incidents() -> dict[str, object]:
    incidents = incident_repository.list_incidents()
    return {
        "items": [
            {
                "id": incident.id,
                "fingerprint": incident.fingerprint,
                "status": incident.status,
                "is_read": incident.is_read,
                "severity": incident.severity,
                "summary": incident.summary,
                "occurrence_count": incident.occurrence_count,
                "first_seen_at": incident.first_seen_at,
                "last_seen_at": incident.last_seen_at,
                "acknowledged_at": incident.acknowledged_at,
                "resolved_at": incident.resolved_at,
            }
            for incident in incidents
        ]
    }


@app.post("/incidents/{incident_id}/read")
def mark_incident_read(incident_id: int) -> dict[str, object]:
    incident = incident_repository.mark_read(incident_id)
    return {"id": incident.id, "status": incident.status, "is_read": incident.is_read}


@app.post("/incidents/{incident_id}/ack")
def acknowledge_incident(incident_id: int) -> dict[str, object]:
    incident = incident_repository.acknowledge(incident_id)
    return {
        "id": incident.id,
        "status": incident.status,
        "is_read": incident.is_read,
        "acknowledged_at": incident.acknowledged_at,
    }


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int) -> dict[str, object]:
    incident = incident_repository.resolve(incident_id)
    return {
        "id": incident.id,
        "status": incident.status,
        "is_read": incident.is_read,
        "resolved_at": incident.resolved_at,
    }


@app.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(incident_id: int) -> dict[str, object]:
    timeline = incident_repository.get_timeline(incident_id)
    return {
        "incident_id": incident_id,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "details": event.details,
                "created_at": event.created_at,
            }
            for event in timeline
        ],
    }


@app.get("/hitl/tickets")
def list_hitl_tickets() -> dict[str, object]:
    tickets = hitl_ticket_repository.list_all()
    return {
        "items": [
            {
                "id": ticket.id,
                "conversation_ref": ticket.conversation_ref,
                "reason": ticket.reason,
                "status": ticket.status,
                "operator_username": ticket.operator_username,
                "target_chat_id": ticket.target_chat_id,
                "created_at": ticket.created_at,
                "updated_at": ticket.updated_at,
                "resolved_at": ticket.resolved_at,
            }
            for ticket in tickets
        ]
    }


@app.post("/hitl/tickets/{ticket_id}/route")
async def route_hitl_ticket(ticket_id: int, request: HitlRouteRequest) -> dict[str, object]:
    operator = request.operator_username or _effective_hitl_operator_username()
    if not operator:
        incident = incident_repository.ingest(
            fingerprint="hitl_delivery_failures",
            severity="critical",
            summary="HITL ticket routing failed: missing operator username",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="hitl_route_failed",
            details=f"ticket_id={ticket_id}",
        )
        raise HTTPException(status_code=503, detail="hitl_operator_missing")

    ticket = hitl_ticket_repository.assign(ticket_id=ticket_id, operator_username=operator)
    await _notify_hitl_operator_summary(
        ticket_id=ticket.id, summary=f"assigned to {operator}"
    )
    return {
        "id": ticket.id,
        "status": ticket.status,
        "operator_username": ticket.operator_username,
    }


@app.post("/hitl/tickets/{ticket_id}/resolve")
def resolve_hitl_ticket(ticket_id: int) -> dict[str, object]:
    ticket = hitl_ticket_repository.resolve(ticket_id=ticket_id)
    return {
        "id": ticket.id,
        "status": ticket.status,
        "resolved_at": ticket.resolved_at,
    }


@app.post("/hitl/runtime-config/persona")
async def update_bot_persona(request: BotPersonaRequest) -> dict[str, object]:
    if request.updated_by != settings.hitl_config_admin_username:
        raise HTTPException(status_code=403, detail="not_authorized")

    first_name = _validate_persona_name(request.first_name)
    last_name = _validate_persona_name(request.last_name)

    description: str | None = None
    if request.description is not None:
        candidate = request.description.strip()
        if not candidate or len(candidate) > _PERSONA_DESCRIPTION_MAX:
            raise HTTPException(status_code=422, detail="invalid_description")
        description = candidate

    short_description: str | None = None
    if request.short_description is not None:
        candidate = request.short_description.strip()
        if not candidate or len(candidate) > _PERSONA_SHORT_DESCRIPTION_MAX:
            raise HTTPException(status_code=422, detail="invalid_short_description")
        short_description = candidate

    hitl_ticket_repository.set_runtime_config(
        key="bot_persona_first_name",
        value=first_name,
        updated_by=request.updated_by,
    )
    hitl_ticket_repository.set_runtime_config(
        key="bot_persona_last_name",
        value=last_name,
        updated_by=request.updated_by,
    )
    if description is not None:
        hitl_ticket_repository.set_runtime_config(
            key="bot_telegram_description",
            value=description,
            updated_by=request.updated_by,
        )
    if short_description is not None:
        hitl_ticket_repository.set_runtime_config(
            key="bot_telegram_short_description",
            value=short_description,
            updated_by=request.updated_by,
        )

    full_name = f"{first_name} {last_name}"
    telegram_results: dict[str, object] = {}
    telegram_results["set_my_name"] = await _safe_telegram_identity_call(
        method=telegram_bot_sender.set_my_name, name=full_name
    )
    effective_description = description or (
        hitl_ticket_repository.get_runtime_config("bot_telegram_description")
        or settings.bot_telegram_description
    )
    telegram_results["set_my_description"] = await _safe_telegram_identity_call(
        method=telegram_bot_sender.set_my_description,
        description=effective_description,
    )
    effective_short = short_description or (
        hitl_ticket_repository.get_runtime_config("bot_telegram_short_description")
        or settings.bot_telegram_short_description
    )
    telegram_results["set_my_short_description"] = (
        await _safe_telegram_identity_call(
            method=telegram_bot_sender.set_my_short_description,
            short_description=effective_short,
        )
    )

    return {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "telegram": telegram_results,
    }


async def _safe_telegram_identity_call(*, method, **kwargs) -> dict:
    """Wrap a single setMyX call so a Telegram error doesn't fail the endpoint."""
    try:
        return await method(**kwargs)
    except Exception as exc:  # broad: identity calls are best-effort
        logger.warning(
            "telegram_identity_call_failed",
            extra={"method": method.__name__, "error": str(exc)},
        )
        return {"ok": False, "error": str(exc)}


@app.on_event("startup")
async def sync_telegram_identity_on_startup() -> None:
    """Push persona/description from config to Telegram once on boot.

    Idempotent on Telegram's side; safe to run on every restart. Skips
    entirely when the bot token is the unconfigured placeholder so unit
    tests don't reach the network.
    """
    if not telegram_bot_sender._is_token_configured():
        return
    first_name, last_name = _effective_bot_persona()
    description = (
        hitl_ticket_repository.get_runtime_config("bot_telegram_description")
        or settings.bot_telegram_description
    )
    short_description = (
        hitl_ticket_repository.get_runtime_config("bot_telegram_short_description")
        or settings.bot_telegram_short_description
    )
    await _safe_telegram_identity_call(
        method=telegram_bot_sender.set_my_name,
        name=f"{first_name} {last_name}",
    )
    await _safe_telegram_identity_call(
        method=telegram_bot_sender.set_my_description, description=description
    )
    await _safe_telegram_identity_call(
        method=telegram_bot_sender.set_my_short_description,
        short_description=short_description,
    )


@app.post("/hitl/tickets/{ticket_id}/reply")
async def deliver_hitl_ticket_reply(ticket_id: int, request: HitlReplyRequest) -> dict[str, object]:
    ticket = hitl_ticket_repository.get(ticket_id)
    if ticket.operator_username != request.operator_username:
        raise HTTPException(status_code=403, detail="operator_not_assigned")
    if not request.reply_text.strip():
        raise HTTPException(status_code=400, detail="empty_reply")
    if ticket.target_chat_id is None:
        incident = incident_repository.ingest(
            fingerprint="hitl_delivery_failures",
            severity="critical",
            summary="HITL reply delivery failed: missing target chat id",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="hitl_delivery_failed",
            details=f"ticket_id={ticket_id};reason=missing_chat_id",
        )
        raise HTTPException(status_code=503, detail="missing_target_chat_id")

    try:
        # Delivers only the operator-authored body as bot text.
        message_id = await telegram_bot_sender.send_message(
            chat_id=ticket.target_chat_id,
            text=request.reply_text.strip(),
        )
    except RuntimeError as exc:
        incident = incident_repository.ingest(
            fingerprint="hitl_delivery_failures",
            severity="critical",
            summary=f"HITL reply delivery failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="hitl_delivery_failed",
            details=f"ticket_id={ticket_id};reason=missing_bot_token",
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - provider failure path
        incident = incident_repository.ingest(
            fingerprint="hitl_delivery_failures",
            severity="critical",
            summary=f"HITL reply delivery failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="hitl_delivery_failed",
            details=f"ticket_id={ticket_id};reason=provider_error",
        )
        raise HTTPException(status_code=502, detail="hitl_delivery_failed") from exc

    resolved = hitl_ticket_repository.resolve(ticket_id=ticket_id)
    return {
        "ticket_id": ticket_id,
        "delivered": True,
        "chat_id": ticket.target_chat_id,
        "message_id": message_id,
        "resolved": True,
        "status": resolved.status,
        "resolved_at": resolved.resolved_at,
    }


@app.post("/knowledge/extract")
def extract_knowledge_candidates(request: KnowledgeExtractRequest) -> dict[str, object]:
    try:
        extract_result = knowledge_candidate_repository.extract_from_transcripts(
            conversation_id=request.conversation_id
        )
    except Exception as exc:
        incident = incident_repository.ingest(
            fingerprint="knowledge_extraction_failures",
            severity="critical",
            summary=f"Knowledge extraction failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="knowledge_extract_failed",
            details=f"conversation_id={request.conversation_id}",
        )
        raise HTTPException(status_code=500, detail="knowledge_extraction_failed") from exc

    moderation_ids: list[int] = []
    for extracted in extract_result.new_candidates:
        moderation_row = knowledge_moderation_repository.create_pending(
            text=extracted.candidate_text,
            source_extraction_candidate_id=extracted.id,
        )
        moderation_ids.append(moderation_row.id)

    candidates = knowledge_candidate_repository.list_candidates(
        conversation_id=request.conversation_id
    )
    return {
        "inserted_candidates": extract_result.inserted,
        "enqueued_for_moderation": len(moderation_ids),
        "moderation_queue_ids": moderation_ids,
        "items": [
            {
                "id": item.id,
                "conversation_id": item.conversation_id,
                "source_message_id": item.source_message_id,
                "candidate_text": item.candidate_text,
            }
            for item in candidates
        ],
    }


@app.post("/knowledge/candidates")
def create_knowledge_candidate(request: KnowledgeCandidateCreateRequest) -> dict[str, object]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="empty_candidate_text")
    row = knowledge_moderation_repository.create_pending(text=request.text)
    return {
        "id": row.id,
        "status": row.status,
        "candidate_text": row.candidate_text,
        "source_extraction_candidate_id": row.source_extraction_candidate_id,
    }


@app.get("/knowledge/candidates")
def list_knowledge_candidates(status: str | None = None) -> dict[str, object]:
    rows = knowledge_moderation_repository.list_by_status(status)
    return {
        "items": [
            {
                "id": row.id,
                "candidate_text": row.candidate_text,
                "published_text": row.published_text,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "source_extraction_candidate_id": row.source_extraction_candidate_id,
            }
            for row in rows
        ]
    }


@app.post("/knowledge/candidates/{candidate_id}/approve")
def approve_knowledge_candidate(
    candidate_id: int, request: KnowledgeCandidateApproveRequest
) -> dict[str, object]:
    try:
        publish_text = knowledge_moderation_repository.prepare_publish_text(
            candidate_id=candidate_id,
            edited_text=request.edited_text,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="candidate_not_found") from exc
    except ValueError as exc:
        if str(exc) == "invalid_status":
            raise HTTPException(status_code=409, detail="candidate_not_pending") from exc
        if str(exc) == "empty_publish_text":
            raise HTTPException(status_code=400, detail="empty_publish_text") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source_id = f"knowledge_candidate:{candidate_id}"
    try:
        inserted_chunks = rag_repository.ingest(source_id=source_id, text=publish_text)
    except Exception as exc:
        incident = incident_repository.ingest(
            fingerprint="knowledge_reindex_failures",
            severity="critical",
            summary=f"Knowledge moderation reindex failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="knowledge_reindex_failed",
            details=f"candidate_id={candidate_id};source_id={source_id}",
        )
        raise HTTPException(status_code=500, detail="knowledge_reindex_failed") from exc

    try:
        knowledge_moderation_repository.mark_approved(
            candidate_id=candidate_id,
            published_text=publish_text,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="candidate_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="candidate_not_pending") from exc

    return {
        "id": candidate_id,
        "status": "approved",
        "published_text": publish_text,
        "source_id": source_id,
        "inserted_chunks": inserted_chunks,
    }


@app.post("/knowledge/candidates/{candidate_id}/reject")
def reject_knowledge_candidate(candidate_id: int) -> dict[str, object]:
    try:
        knowledge_moderation_repository.reject(candidate_id=candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="candidate_not_found") from exc
    except ValueError as exc:
        if str(exc) == "invalid_status":
            raise HTTPException(status_code=409, detail="candidate_not_pending") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": candidate_id, "status": "rejected"}


_OPERATOR_UPLOAD_TYPES = frozenset(
    {"pdf", "docx", "pptx", "txt", "image", "audio", "video", "inline_text"}
)
_OPERATOR_UPLOAD_MEDIA_TYPES = frozenset({"audio", "video"})
_operator_transcriber: object | None = None


def _get_operator_transcriber() -> object:
    global _operator_transcriber
    if _operator_transcriber is None:
        from services.api.app.operator_uploads.extractors import WhisperTranscriber

        _operator_transcriber = WhisperTranscriber()
    return _operator_transcriber


@app.post("/knowledge/operator_upload")
async def operator_upload(request: OperatorUploadRequest) -> dict[str, object]:
    from pathlib import Path as _Path

    from services.api.app.operator_uploads.extractors import (
        EXTRACTORS,
        ExtractionError,
        binary_sha256,
        extract_media,
        soft_wrap,
    )

    if request.source_file_type not in _OPERATOR_UPLOAD_TYPES:
        raise HTTPException(status_code=422, detail="unsupported_source_file_type")

    sha: str | None = None
    if request.source_file_type == "inline_text":
        if not request.inline_text or not request.inline_text.strip():
            raise HTTPException(status_code=422, detail="empty_inline_text")
    else:
        if not request.stored_binary_path:
            raise HTTPException(status_code=422, detail="missing_stored_binary_path")
        binary_path = _Path(request.stored_binary_path)
        if not binary_path.exists():
            raise HTTPException(status_code=404, detail="binary_not_found")
        sha = binary_sha256(binary_path)
        existing = knowledge_moderation_repository.find_by_binary_sha256(sha)
        if existing is not None:
            return {
                "candidate_id": existing.id,
                "source_id": f"knowledge_candidate:{existing.id}",
                "inserted_chunks": 0,
                "extracted_chars": 0,
                "is_confidential": existing.is_confidential,
                "deduplicated": True,
            }

    try:
        if request.source_file_type == "inline_text":
            raw_text = request.inline_text.strip()  # type: ignore[union-attr]
        elif request.source_file_type in _OPERATOR_UPLOAD_MEDIA_TYPES:
            raw_text = await extract_media(
                request.source_file_type,
                _Path(request.stored_binary_path),  # type: ignore[arg-type]
                transcriber=_get_operator_transcriber(),  # type: ignore[arg-type]
            )
        else:
            extractor = EXTRACTORS[request.source_file_type]
            raw_text = extractor(_Path(request.stored_binary_path))  # type: ignore[arg-type]
            if not raw_text or not raw_text.strip():
                raise ExtractionError("empty_text")
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc
    except HTTPException:
        raise
    except Exception as exc:
        incident = incident_repository.ingest(
            fingerprint="operator_upload_failures",
            severity="critical",
            summary=f"Operator upload extraction failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="operator_upload_failed",
            details=(
                f"operator={request.operator_username};"
                f"type={request.source_file_type};"
                f"path={request.stored_binary_path}"
            ),
        )
        raise HTTPException(status_code=500, detail="operator_upload_failed") from exc

    wrapped = soft_wrap(raw_text)
    if not wrapped.strip():
        raise HTTPException(status_code=422, detail="empty_text")

    candidate = knowledge_moderation_repository.create_approved_operator_upload(
        candidate_text=raw_text,
        published_text=wrapped,
        operator_username=request.operator_username,
        is_confidential=request.is_confidential,
        source_file_name=request.source_file_name,
        source_file_type=request.source_file_type,
        stored_binary_path=request.stored_binary_path,
        binary_sha256=sha,
    )
    source_id = f"knowledge_candidate:{candidate.id}"
    inserted_chunks = rag_repository.ingest(
        source_id=source_id,
        text=wrapped,
        is_confidential=request.is_confidential,
    )
    return {
        "candidate_id": candidate.id,
        "source_id": source_id,
        "inserted_chunks": inserted_chunks,
        "extracted_chars": len(wrapped),
        "is_confidential": request.is_confidential,
        "deduplicated": False,
    }


def _serialize_nl_session(session: object) -> dict[str, object]:
    return {
        "id": session.id,
        "tenant_id": session.tenant_id,
        "user_id": session.user_id,
        "utterance": session.utterance,
        "intent": session.intent,
        "draft_text": session.draft_text,
        "status": session.status,
        "confirm_token": session.confirm_token,
        "knowledge_version_id": session.knowledge_version_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _serialize_nl_version(version: object) -> dict[str, object]:
    return {
        "id": version.id,
        "tenant_id": version.tenant_id,
        "version_number": version.version_number,
        "source_text": version.source_text,
        "status": version.status,
        "nl_session_id": version.nl_session_id,
        "source_id": version.source_id,
        "created_at": version.created_at,
    }


def _check_nl_ops_enabled() -> None:
    if not settings.nl_ops_enabled:
        raise HTTPException(status_code=503, detail="nl_ops_disabled")


def _check_nl_ops_admin(user_id: str) -> None:
    allow = settings.nl_ops_admin_user_id_list()
    if allow and user_id not in allow:
        raise HTTPException(status_code=403, detail="nl_ops_user_not_authorized")


@app.post("/knowledge/nl-ops")
def propose_nl_op(request: NlOpProposeRequest) -> dict[str, object]:
    _check_nl_ops_enabled()
    _check_nl_ops_admin(request.user_id)
    tenant_id = request.tenant_id or settings.nl_ops_default_tenant_id
    try:
        session = nl_knowledge_ops_repository.propose(
            tenant_id=tenant_id,
            user_id=request.user_id,
            utterance=request.utterance,
        )
    except NlKnowledgeOpsError as exc:
        message = str(exc)
        if message in {"tenant_id_required", "user_id_required", "utterance_required"}:
            raise HTTPException(status_code=400, detail=message) from exc
        incident = incident_repository.ingest(  # pragma: no cover - defensive guard
            fingerprint="nl_ops_propose_failures",
            severity="critical",
            summary=f"NL ops propose failed: {message}",
        )
        incident_repository.append_event(  # pragma: no cover
            incident_id=incident.id,
            event_type="nl_ops_propose_failed",
            details=message,
        )
        raise HTTPException(  # pragma: no cover
            status_code=500, detail="nl_ops_propose_failed"
        ) from exc
    return _serialize_nl_session(session)


@app.post("/knowledge/nl-ops/{session_id}/confirm")
def confirm_nl_op(session_id: int, request: NlOpConfirmRequest) -> dict[str, object]:
    _check_nl_ops_enabled()
    try:
        session, version = nl_knowledge_ops_repository.confirm(
            session_id=session_id,
            confirm_token=request.confirm_token,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="nl_op_session_not_found") from exc
    except NlKnowledgeOpsError as exc:
        message = str(exc)
        if message == "invalid_confirm_token":
            raise HTTPException(status_code=400, detail=message) from exc
        if message.startswith("invalid_status") or message == "already_confirmed":
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(  # pragma: no cover - defensive guard
            status_code=400, detail=message
        ) from exc

    if session.intent != "deprecate":
        try:
            rag_repository.ingest(source_id=version.source_id, text=version.source_text)
        except Exception as exc:
            incident = incident_repository.ingest(
                fingerprint="nl_knowledge_reindex_failures",
                severity="critical",
                summary=f"NL ops reindex failed: {exc}",
            )
            incident_repository.append_event(
                incident_id=incident.id,
                event_type="nl_knowledge_reindex_failed",
                details=f"version_id={version.id};source_id={version.source_id}",
            )
            raise HTTPException(status_code=500, detail="nl_knowledge_reindex_failed") from exc
    return {
        "session": _serialize_nl_session(session),
        "version": _serialize_nl_version(version),
    }


@app.post("/knowledge/nl-ops/{session_id}/cancel")
def cancel_nl_op(session_id: int) -> dict[str, object]:
    _check_nl_ops_enabled()
    try:
        session = nl_knowledge_ops_repository.cancel(session_id=session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="nl_op_session_not_found") from exc
    except NlKnowledgeOpsError as exc:
        message = str(exc)
        if message.startswith("invalid_status"):
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(  # pragma: no cover - defensive guard
            status_code=400, detail=message
        ) from exc
    return _serialize_nl_session(session)


@app.get("/knowledge/nl-ops/{session_id}")
def get_nl_op(session_id: int) -> dict[str, object]:
    try:
        session = nl_knowledge_ops_repository.get_session(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="nl_op_session_not_found") from exc
    return _serialize_nl_session(session)


@app.get("/knowledge/nl-ops")
def list_nl_ops(tenant_id: str | None = None) -> dict[str, object]:
    sessions = nl_knowledge_ops_repository.list_sessions(tenant_id=tenant_id)
    return {"items": [_serialize_nl_session(item) for item in sessions]}


@app.get("/knowledge/versions")
def list_knowledge_versions(tenant_id: str | None = None) -> dict[str, object]:
    versions = nl_knowledge_ops_repository.list_versions(tenant_id=tenant_id)
    return {"items": [_serialize_nl_version(version) for version in versions]}


@app.get("/knowledge/nl-ops-audit")
def list_nl_ops_audit(tenant_id: str | None = None) -> dict[str, object]:
    logs = nl_knowledge_ops_repository.list_audit_logs(tenant_id=tenant_id)
    return {
        "items": [
            {
                "id": log.id,
                "tenant_id": log.tenant_id,
                "user_id": log.user_id,
                "session_id": log.session_id,
                "op_type": log.op_type,
                "details": log.details,
                "created_at": log.created_at,
            }
            for log in logs
        ]
    }


def _serialize_correction(correction: object) -> dict[str, object]:
    return {
        "id": correction.id,
        "trace_id": correction.trace_id,
        "tenant_id": correction.tenant_id,
        "user_id": correction.user_id,
        "branch": correction.branch,
        "status": correction.status,
        "draft_text": correction.draft_text,
        "source_id": correction.source_id,
        "candidate_id": correction.candidate_id,
        "created_at": correction.created_at,
        "updated_at": correction.updated_at,
    }


def _ensure_trace_exists(trace_id: str) -> None:
    try:
        answer_trace_repository.get_by_trace_id(trace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="answer_trace_not_found") from exc


@app.post("/answer-traces/{trace_id}/open")
def record_trace_open(trace_id: str, request: TraceOpenRequest) -> dict[str, object]:
    _ensure_trace_exists(trace_id)
    try:
        trace_correction_repository.record_open(
            trace_id=trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
        )
    except TraceCorrectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trace_id": trace_id, "status": "logged"}


@app.post("/answer-traces/{trace_id}/corrections")
def submit_trace_correction(
    trace_id: str, request: TraceCorrectionRequest
) -> dict[str, object]:
    _ensure_trace_exists(trace_id)
    if request.branch not in {BRANCH_PUBLISH, BRANCH_MODERATION}:
        raise HTTPException(status_code=400, detail="invalid_branch")
    if request.branch == BRANCH_PUBLISH:
        try:
            correction = trace_correction_repository.submit_publish(
                trace_id=trace_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                edited_text=request.edited_text,
            )
        except TraceCorrectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            rag_repository.ingest(
                source_id=correction.source_id,
                text=correction.draft_text,
            )
        except Exception as exc:
            incident = incident_repository.ingest(
                fingerprint="trace_correction_reindex_failures",
                severity="critical",
                summary=f"Trace correction reindex failed: {exc}",
            )
            incident_repository.append_event(
                incident_id=incident.id,
                event_type="trace_correction_reindex_failed",
                details=f"correction_id={correction.id};trace_id={trace_id}",
            )
            raise HTTPException(
                status_code=500, detail="trace_correction_reindex_failed"
            ) from exc
        return _serialize_correction(correction)

    candidate = knowledge_moderation_repository.create_pending(text=request.edited_text)
    try:
        correction = trace_correction_repository.submit_moderation(
            trace_id=trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            edited_text=request.edited_text,
            candidate_id=candidate.id,
        )
    except TraceCorrectionError as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_correction(correction)


@app.get("/answer-traces/{trace_id}/corrections")
def list_trace_corrections(trace_id: str) -> dict[str, object]:
    _ensure_trace_exists(trace_id)
    return {
        "items": [
            _serialize_correction(correction)
            for correction in trace_correction_repository.list_for_trace(trace_id)
        ],
    }


@app.get("/answer-traces/{trace_id}/audit")
def list_trace_audit(trace_id: str) -> dict[str, object]:
    _ensure_trace_exists(trace_id)
    return {"items": trace_correction_repository.list_audit(trace_id=trace_id)}


def _serialize_trace(trace: object) -> dict[str, object]:
    return {
        "trace_id": trace.trace_id,
        "created_at": trace.created_at,
        "request_text": trace.request_text,
        "model_id": trace.model_id,
        "model_provider": trace.model_provider,
        "latency_ms": trace.latency_ms,
        "response_mode": trace.response_mode,
        "guardrails_applied": trace.guardrails_applied,
        "guardrail_outcome": trace.guardrail_outcome,
        "guardrail_reasons": trace.guardrail_reasons,
        "guardrail_score": trace.guardrail_score,
        "grounded": trace.grounded,
        "no_retrieval_hit": trace.no_retrieval_hit,
        "confidence": trace.confidence,
        "retrieval": trace.retrieval,
        "limitations": trace.limitations,
    }


@app.get("/answer-traces")
def list_answer_traces(limit: int = 50) -> dict[str, object]:
    return {
        "items": [_serialize_trace(trace) for trace in answer_trace_repository.list_traces(
            limit=limit
        )]
    }


@app.get("/answer-traces/{trace_id}")
def get_answer_trace(trace_id: str) -> dict[str, object]:
    try:
        trace = answer_trace_repository.get_by_trace_id(trace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="answer_trace_not_found") from exc
    return _serialize_trace(trace)


def _serialize_backup(backup: object) -> dict[str, object]:
    return {
        "id": backup.id,
        "started_at": backup.started_at,
        "completed_at": backup.completed_at,
        "status": backup.status,
        "archive_path": backup.archive_path,
        "size_bytes": backup.size_bytes,
        "source_paths": backup.source_paths,
        "included_paths": backup.included_paths,
        "error_message": backup.error_message,
    }


@app.post("/backups/run")
def run_backup() -> dict[str, object]:
    try:
        backup = backup_repository.run_backup()
    except BackupError as exc:
        incident = incident_repository.ingest(
            fingerprint="backup_failures",
            severity="critical",
            summary=f"Backup run failed: {exc}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="backup_failed",
            details=str(exc),
        )
        raise HTTPException(status_code=500, detail="backup_failed") from exc
    return _serialize_backup(backup)


@app.get("/backups")
def list_backups() -> dict[str, object]:
    return {"items": [_serialize_backup(backup) for backup in backup_repository.list_backups()]}


@app.get("/backups/last-successful")
def get_last_successful_backup() -> dict[str, object]:
    backup = backup_repository.latest_successful()
    if backup is None:
        return {"backup": None}
    return {"backup": _serialize_backup(backup)}


@app.get("/backups/{backup_id}")
def get_backup(backup_id: int) -> dict[str, object]:
    try:
        backup = backup_repository.get(backup_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    return _serialize_backup(backup)


@app.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: int, request: BackupRestoreRequest) -> dict[str, object]:
    try:
        result = backup_repository.restore(
            backup_id=backup_id,
            confirm_token=request.confirm_token,
            target_root=request.target_root,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except BackupError as exc:
        message = str(exc)
        if message == "invalid_confirm_token":
            raise HTTPException(status_code=400, detail="invalid_confirm_token") from exc
        incident = incident_repository.ingest(
            fingerprint="backup_restore_failures",
            severity="critical",
            summary=f"Backup restore failed: {message}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="backup_restore_failed",
            details=f"backup_id={backup_id};error={message}",
        )
        raise HTTPException(status_code=500, detail="backup_restore_failed") from exc
    return {
        "backup_id": result.backup_id,
        "restored_paths": result.restored_paths,
    }
