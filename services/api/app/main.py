from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.incidents import IncidentRepository
from services.api.app.openrouter_client import OpenRouterClient
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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "api", "message": "Semantaix API"}


class SuggestRequest(BaseModel):
    text: str


class IncidentEventRequest(BaseModel):
    fingerprint: str
    severity: str
    summary: str


@app.post("/suggest")
async def suggest(request: SuggestRequest) -> dict[str, object]:
    try:
        suggestion = await openrouter_client.suggest(request.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - external provider failure path
        raise HTTPException(status_code=502, detail=f"OpenRouter call failed: {exc}") from exc

    return {
        "suggestion": f"[Suggestion mode] {suggestion}",
        "is_suggestion_only": True,
        "response_mode": "suggestion_only",
        "guardrails_applied": False,
    }


@app.post("/incidents/events")
async def ingest_incident_event(request: IncidentEventRequest) -> dict[str, object]:
    incident = incident_repository.ingest(
        fingerprint=request.fingerprint,
        severity=request.severity,
        summary=request.summary,
    )
    sent, delivery_status = await telegram_notifier.notify_if_critical(
        incident_id=incident.id,
        fingerprint=incident.fingerprint,
        severity=incident.severity,
        summary=incident.summary,
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
