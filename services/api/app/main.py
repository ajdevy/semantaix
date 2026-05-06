from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.guardrails import evaluate_suggestion
from services.api.app.hitl import HitlTicketRepository
from services.api.app.incidents import IncidentRepository
from services.api.app.openrouter_client import OpenRouterClient
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
telegram_bot_sender = TelegramBotSender(bot_token=settings.telegram_bot_token)


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


def _effective_hitl_operator_username() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or settings.hitl_primary_operator_username
    )


@app.post("/suggest")
async def suggest(request: SuggestRequest) -> dict[str, object]:
    try:
        suggestion = await openrouter_client.suggest(request.text)
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
