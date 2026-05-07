from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.backups import BackupError, BackupRepository
from services.api.app.guardrails import evaluate_suggestion
from services.api.app.hitl import HitlTicketRepository
from services.api.app.incidents import IncidentRepository
from services.api.app.knowledge import KnowledgeCandidateRepository
from services.api.app.knowledge_moderation import KnowledgeModerationRepository
from services.api.app.openrouter_client import OpenRouterClient
from services.api.app.rag import RagRepository
from services.api.app.telegram_bot_sender import TelegramBotSender
from services.api.app.telegram_notifier import TelegramIncidentNotifier

app = create_service_app("api")
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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "api", "message": "Semantaix API"}


class SuggestRequest(BaseModel):
    text: str
    chat_id: int | None = None


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


def _effective_hitl_operator_username() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or settings.hitl_primary_operator_username
    )


@app.post("/suggest")
async def suggest(request: SuggestRequest) -> dict[str, object]:
    retrieved_chunks = rag_repository.retrieve(query=request.text, limit=3)
    retrieval_context: list[dict[str, str]] = []
    if retrieved_chunks:
        retrieval_context.append(
            {
                "role": "system",
                "content": "Relevant knowledge:\n"
                + "\n".join(
                    f"- [{chunk.source_id}] {chunk.chunk_text}" for chunk in retrieved_chunks
                ),
            }
        )
    try:
        suggestion = await openrouter_client.suggest(request.text, context=retrieval_context)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - external provider failure path
        raise HTTPException(status_code=502, detail=f"OpenRouter call failed: {exc}") from exc

    decision = evaluate_suggestion(suggestion)
    if not decision.valid:
        ticket = hitl_ticket_repository.create(
            conversation_ref=request.text[:120],
            reason=",".join(decision.reasons),
            target_chat_id=request.chat_id,
        )
        hitl_ticket_repository.assign(
            ticket_id=ticket.id,
            operator_username=_effective_hitl_operator_username(),
        )
        incident = incident_repository.ingest(
            fingerprint="guardrail_invalid_suggestion",
            severity="warning",
            summary=f"Suggestion blocked by guardrails: {','.join(decision.reasons)}",
        )
        incident_repository.append_event(
            incident_id=incident.id,
            event_type="guardrail_blocked",
            details=f"reasons={','.join(decision.reasons)}",
        )
        return {
            "suggestion": None,
            "is_suggestion_only": True,
            "response_mode": "blocked_invalid",
            "guardrails_applied": True,
            "guardrail_decision": {
                "valid": decision.valid,
                "reasons": decision.reasons,
                "score": decision.score,
            },
            "delivery_blocked": True,
            "hitl_ticket_id": ticket.id,
            "hitl_operator_username": _effective_hitl_operator_username(),
        }

    return {
        "suggestion": f"[Suggestion mode] {suggestion}",
        "is_suggestion_only": True,
        "response_mode": "suggestion_only",
        "guardrails_applied": True,
        "guardrail_decision": {
            "valid": decision.valid,
            "reasons": decision.reasons,
            "score": decision.score,
        },
        "delivery_blocked": False,
        "retrieval": [
            {
                "source_id": chunk.source_id,
                "chunk_text": chunk.chunk_text,
                "score": chunk.score,
            }
            for chunk in retrieved_chunks
        ],
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
def route_hitl_ticket(ticket_id: int, request: HitlRouteRequest) -> dict[str, object]:
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

    return {
        "ticket_id": ticket_id,
        "delivered": True,
        "chat_id": ticket.target_chat_id,
        "message_id": message_id,
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
