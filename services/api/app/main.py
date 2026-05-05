from fastapi import HTTPException
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from services.api.app.openrouter_client import OpenRouterClient


app = create_service_app("api")
openrouter_client = OpenRouterClient()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "api", "message": "Semantaix API"}


class SuggestRequest(BaseModel):
    text: str


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
