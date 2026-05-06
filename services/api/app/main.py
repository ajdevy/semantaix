from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.incidents import IncidentRepository
from services.api.app.openrouter_client import OpenRouterClient

app = create_service_app("api")
openrouter_client = OpenRouterClient()
settings = get_settings()
incident_repository = IncidentRepository(
    db_path=settings.incident_db_path,
    dedup_window_seconds=settings.incident_dedup_window_seconds,
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
def ingest_incident_event(request: IncidentEventRequest) -> dict[str, object]:
    incident = incident_repository.ingest(
        fingerprint=request.fingerprint,
        severity=request.severity,
        summary=request.summary,
    )
    return {
        "id": incident.id,
        "fingerprint": incident.fingerprint,
        "status": incident.status,
        "occurrence_count": incident.occurrence_count,
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
                "severity": incident.severity,
                "summary": incident.summary,
                "occurrence_count": incident.occurrence_count,
                "first_seen_at": incident.first_seen_at,
                "last_seen_at": incident.last_seen_at,
            }
            for incident in incidents
        ],
    }
