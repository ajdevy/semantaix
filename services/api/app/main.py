import hmac
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.admin_auth import (
    AdminAuthRepository,
    AdminAuthService,
    AdminSession,
    InvalidLoginCode,
    SessionPrincipal,
    wire_admin_auth_routes,
)
from services.api.app.admin_files import wire_admin_files_routes
from services.api.app.admin_nl_ops import (
    OP_FILE_ATTACH,
    OP_OPERATOR_ATTACH,
    OP_OPERATOR_DETACH,
    OP_PROJECT_CREATE,
    OP_PROJECT_RENAME,
    AdminNlOpSession,
    AdminNlOpsRepository,
    InvalidConfirmToken,
    SessionNotPending,
)
from services.api.app.admin_rag_inspect import wire_admin_rag_inspect_routes
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
from services.api.app.operator_files_admin import OperatorFilesAdminWriter
from services.api.app.operator_files_view import OperatorFilesView
from services.api.app.operators import (
    Operator,
    OperatorRepository,
    OperatorUsernameConflict,
)
from services.api.app.project_prompts import (
    PROMPT_NAME_LIST,
    PROMPT_NAMES,
    ProjectPromptRepository,
    PromptCurrent,
    PromptValueInvalid,
    PromptValueTooLarge,
    PromptVersion,
    PromptVersionNotFound,
    default_prompt,
)
from services.api.app.projects import (
    Project,
    ProjectReferenced,
    ProjectRepository,
    ProjectSlugConflict,
)
from services.api.app.rag import RagRepository
from services.api.app.telegram_bot_sender import TelegramBotSender
from services.api.app.telegram_notifier import TelegramIncidentNotifier
from services.api.app.trace_corrections import (
    BRANCH_MODERATION,
    BRANCH_PUBLISH,
    TraceCorrectionError,
    TraceCorrectionRepository,
)
from services.api.app.web_auth import WebAuthRepository

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
    base_url=settings.telegram_bot_api_base_url,
)
hitl_ticket_repository = HitlTicketRepository(settings.hitl_ticket_db_path)
knowledge_candidate_repository = KnowledgeCandidateRepository(
    db_path=settings.knowledge_db_path,
    transcript_db_path=settings.persistence_db_path,
)
rag_repository = RagRepository(settings.rag_db_path)
knowledge_moderation_repository = KnowledgeModerationRepository(settings.knowledge_db_path)
telegram_bot_sender = TelegramBotSender(
    bot_token=settings.telegram_bot_token,
    base_url=settings.telegram_bot_api_base_url,
)
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
project_repository = ProjectRepository(settings.projects_db_path)
operator_repository = OperatorRepository(settings.operators_db_path)
project_prompt_repository = ProjectPromptRepository(settings.hitl_ticket_db_path)
admin_auth_repository = AdminAuthRepository(settings.admin_session_db_path)
admin_nl_ops_repository = AdminNlOpsRepository(settings.nl_ops_db_path)
web_auth_repository = WebAuthRepository(db_path=settings.web_auth_db_path)
admin_auth_service = AdminAuthService(
    web_auth_repository=web_auth_repository,
    hitl_repository=hitl_ticket_repository,
    telegram_bot_sender=telegram_bot_sender,
    settings=settings,
)
operator_files_view = OperatorFilesView(
    operator_files_db_path=settings.operator_files_db_path,
    knowledge_db_path=settings.knowledge_db_path,
)
operator_files_admin_writer = OperatorFilesAdminWriter(
    operator_files_db_path=settings.operator_files_db_path,
    knowledge_db_path=settings.knowledge_db_path,
    rag_db_path=settings.rag_db_path,
)
wire_admin_auth_routes(app, service=admin_auth_service)
wire_admin_files_routes(
    app,
    auth_service=admin_auth_service,
    files_view=operator_files_view,
    files_admin_writer=operator_files_admin_writer,
)
wire_admin_rag_inspect_routes(
    app,
    auth_service=admin_auth_service,
    rag_repository=rag_repository,
    operator_files_db_path=lambda: settings.operator_files_db_path,
    resolve_inbound_project_id=lambda chat_id: _resolve_inbound_project_id(chat_id),
    default_project_id=lambda: _default_project_id(),
    grounding_threshold=lambda: _effective_grounding_threshold(),
)


def _bootstrap_default_entities() -> None:
    """Idempotently ensure a `default` project and a primary operator row exist.

    Runs at module import so a fresh `docker compose up` always lands with the
    schema rows the rest of Epic 10 depends on. Tests can re-run it after
    rebinding repository db paths.
    """
    default_project = project_repository.ensure_default_project()
    primary_chat_id_raw = settings.hitl_primary_operator_chat_id
    primary_chat_id = None
    if primary_chat_id_raw is not None:
        try:
            primary_chat_id = int(primary_chat_id_raw)
        except (TypeError, ValueError):
            primary_chat_id = None
    operator_repository.ensure_default_operator(
        username=settings.hitl_primary_operator_username,
        project_id=default_project.id,
        chat_id=primary_chat_id,
    )


_bootstrap_default_entities()


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
            project_prompt_repository=project_prompt_repository,
        ),
    ]
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "api", "message": "Semantaix API"}


class AdminLoginRequestModel(BaseModel):
    admin_username: str


class AdminLoginVerifyRequest(BaseModel):
    admin_username: str
    code: str


def _ensure_admin_username(admin_username: str) -> None:
    if admin_username != settings.admin_telegram_username:
        raise HTTPException(status_code=403, detail="not_admin")


def require_admin_session(
    x_admin_session: Annotated[str | None, Header()] = None,
) -> AdminSession:
    if not x_admin_session:
        raise HTTPException(status_code=401, detail="missing_admin_session")
    session = admin_auth_repository.validate_session(x_admin_session)
    if session is None:
        raise HTTPException(status_code=401, detail="invalid_admin_session")
    return session


def require_admin_or_internal(
    x_admin_session: Annotated[str | None, Header()] = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> str:
    """Accept either an admin session token or the configured internal token.

    Returns the principal identifier (admin username, or "internal" for
    bot-to-api server-to-server calls). Raises 401 if neither credential
    is presented.
    """
    expected = settings.admin_internal_token
    if x_internal_token and expected and hmac.compare_digest(
        x_internal_token, expected
    ):
        return "internal"
    if x_admin_session:
        session = admin_auth_repository.validate_session(x_admin_session)
        if session is not None:
            return session.admin_username
    raise HTTPException(status_code=401, detail="admin_auth_required")


def _project_to_dict(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _operator_to_dict(operator: Operator) -> dict[str, object]:
    return {
        "id": operator.id,
        "username": operator.username,
        "chat_id": operator.chat_id,
        "project_id": operator.project_id,
        "display_name": operator.display_name,
        "is_active": operator.is_active,
        "created_at": operator.created_at,
        "updated_at": operator.updated_at,
    }


@app.post("/admin/login/request")
async def admin_login_request(request: AdminLoginRequestModel) -> dict[str, object]:
    _ensure_admin_username(request.admin_username)
    admin_operator = operator_repository.find_by_username(request.admin_username)
    if admin_operator is None or admin_operator.chat_id is None:
        raise HTTPException(status_code=400, detail="admin_operator_chat_id_missing")
    code = admin_auth_repository.request_code(
        admin_username=request.admin_username,
        ttl_seconds=settings.admin_login_code_ttl_seconds,
    )
    minutes = max(1, settings.admin_login_code_ttl_seconds // 60)
    message = f"Ваш код входа: {code} (действителен {minutes} мин)"
    try:
        await telegram_bot_sender.send_message(
            chat_id=admin_operator.chat_id, text=message
        )
    except Exception as exc:  # broad: any DM failure surfaces as 502
        logger.warning("admin_login_code_dm_failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="telegram_dm_failed"
        ) from exc
    return {"requested": True}


@app.post("/admin/login/verify")
def admin_login_verify(request: AdminLoginVerifyRequest) -> dict[str, object]:
    _ensure_admin_username(request.admin_username)
    try:
        session = admin_auth_repository.consume_code(
            admin_username=request.admin_username,
            code=request.code,
            ttl_seconds=settings.admin_session_ttl_seconds,
        )
    except InvalidLoginCode as exc:
        raise HTTPException(status_code=401, detail="invalid_login_code") from exc
    return {
        "session_token": session.token,
        "expires_at": session.expires_at,
        "admin_username": session.admin_username,
    }


@app.post("/admin/logout")
def admin_logout(
    session: Annotated[AdminSession, Depends(require_admin_session)],
) -> dict[str, bool]:
    admin_auth_repository.revoke_session(session.token)
    return {"ok": True}


@app.get("/admin/session/check")
def admin_session_check(
    session: Annotated[AdminSession, Depends(require_admin_session)],
) -> dict[str, str]:
    return {
        "admin_username": session.admin_username,
        "expires_at": session.expires_at,
    }


class ProjectCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class OperatorCreateRequest(BaseModel):
    username: str
    project_id: int
    chat_id: int | None = None
    display_name: str | None = None


class OperatorUpdateRequest(BaseModel):
    project_id: int | None = None
    chat_id: int | None = None
    display_name: str | None = None
    is_active: bool | None = None


class FileReassignRequest(BaseModel):
    project_id: int


@app.get("/projects")
def list_projects(
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    items = [_project_to_dict(p) for p in project_repository.list_all()]
    return {"items": items}


@app.post("/projects")
def create_project(
    request: ProjectCreateRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    try:
        project = project_repository.create(
            slug=request.slug,
            name=request.name,
            description=request.description,
        )
    except ProjectSlugConflict as exc:
        raise HTTPException(status_code=409, detail="project_slug_conflict") from exc
    return _project_to_dict(project)


@app.get("/projects/{slug}")
def get_project(
    slug: str,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    project = project_repository.get_by_slug(slug)
    if project is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    operators = operator_repository.list_by_project_id(project.id)
    payload = _project_to_dict(project)
    payload["operator_count"] = len(operators)
    payload["operators"] = [_operator_to_dict(op) for op in operators]
    return payload


@app.patch("/projects/{slug}")
def patch_project(
    slug: str,
    request: ProjectUpdateRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    try:
        project = project_repository.update(
            slug=slug, name=request.name, description=request.description
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="project_not_found") from exc
    return _project_to_dict(project)


@app.delete("/projects/{slug}")
def delete_project(
    slug: str,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, bool]:
    try:
        project_repository.delete(
            slug, is_referenced=operator_repository.any_referencing_project
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="project_not_found") from exc
    except ProjectReferenced as exc:
        raise HTTPException(status_code=409, detail="project_referenced") from exc
    return {"ok": True}


class PromptValueRequest(BaseModel):
    value: str


class PromptRestoreRequest(BaseModel):
    version: int


def _prompt_current_to_dict(current: PromptCurrent) -> dict[str, object]:
    return {
        "project_id": current.project_id,
        "prompt_name": current.prompt_name,
        "value": current.value,
        "version": current.version,
        "updated_at": current.updated_at,
        "updated_by": current.updated_by,
        "is_default": False,
    }


def _prompt_default_to_dict(project_id: int, name: str) -> dict[str, object]:
    return {
        "project_id": project_id,
        "prompt_name": name,
        "value": default_prompt(name),
        "version": 0,
        "updated_at": None,
        "updated_by": None,
        "is_default": True,
    }


def _prompt_version_to_dict(pv: PromptVersion) -> dict[str, object]:
    return {
        "version": pv.version,
        "value": pv.value,
        "edited_by": pv.edited_by,
        "created_at": pv.created_at,
    }


def _project_or_404(slug: str) -> Project:
    project = project_repository.get_by_slug(slug)
    if project is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    return project


def _ensure_known_prompt_name(name: str) -> None:
    if name not in PROMPT_NAMES:
        raise HTTPException(status_code=404, detail="unknown_prompt_name")


def _require_project_access(
    request: Request, slug: str, as_user: str | None
) -> tuple[Project, SessionPrincipal]:
    """Resolve principal and enforce admin-or-operator-of-this-project."""
    principal = admin_auth_service.require_session_or_internal(request, as_user)
    project = _project_or_404(slug)
    if principal.role == "admin":
        return project, principal
    operator = operator_repository.find_by_username(principal.username)
    if operator is None or operator.project_id != project.id:
        raise HTTPException(status_code=403, detail="not_in_project")
    return project, principal


@app.get("/projects/{slug}/prompts")
def list_project_prompts(
    slug: str,
    request: Request,
    as_user: str | None = None,
) -> dict[str, object]:
    project, _ = _require_project_access(request, slug, as_user)
    by_name = {
        pc.prompt_name: pc
        for pc in project_prompt_repository.list_current(project.id)
    }
    items = [
        _prompt_current_to_dict(by_name[name])
        if name in by_name
        else _prompt_default_to_dict(project.id, name)
        for name in PROMPT_NAME_LIST
    ]
    return {
        "project_id": project.id,
        "project_slug": project.slug,
        "items": items,
    }


@app.get("/projects/{slug}/prompts/{name}")
def get_project_prompt(
    slug: str,
    name: str,
    request: Request,
    as_user: str | None = None,
) -> dict[str, object]:
    _ensure_known_prompt_name(name)
    project, _ = _require_project_access(request, slug, as_user)
    current = project_prompt_repository.get_current(
        project_id=project.id, prompt_name=name
    )
    if current is None:
        body = _prompt_default_to_dict(project.id, name)
    else:
        body = _prompt_current_to_dict(current)
    body["history"] = [
        _prompt_version_to_dict(pv)
        for pv in project_prompt_repository.list_versions(
            project_id=project.id, prompt_name=name
        )
    ]
    return body


@app.put("/projects/{slug}/prompts/{name}")
def put_project_prompt(
    slug: str,
    name: str,
    payload: PromptValueRequest,
    request: Request,
    as_user: str | None = None,
) -> dict[str, object]:
    _ensure_known_prompt_name(name)
    project, principal = _require_project_access(request, slug, as_user)
    try:
        version = project_prompt_repository.set(
            project_id=project.id,
            prompt_name=name,
            value=payload.value,
            edited_by=principal.username,
        )
    except PromptValueTooLarge as exc:
        raise HTTPException(
            status_code=413, detail="prompt_value_too_large"
        ) from exc
    except PromptValueInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "project_id": project.id,
        "prompt_name": name,
        "version": version,
    }


@app.post("/projects/{slug}/prompts/{name}/restore")
def restore_project_prompt(
    slug: str,
    name: str,
    payload: PromptRestoreRequest,
    request: Request,
    as_user: str | None = None,
) -> dict[str, object]:
    _ensure_known_prompt_name(name)
    project, principal = _require_project_access(request, slug, as_user)
    try:
        version = project_prompt_repository.restore(
            project_id=project.id,
            prompt_name=name,
            version=payload.version,
            edited_by=principal.username,
        )
    except PromptVersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version_not_found") from exc
    return {
        "project_id": project.id,
        "prompt_name": name,
        "version": version,
    }


@app.get("/projects/{slug}/prompts/{name}/versions")
def list_project_prompt_versions(
    slug: str,
    name: str,
    request: Request,
    as_user: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    _ensure_known_prompt_name(name)
    project, _ = _require_project_access(request, slug, as_user)
    versions = project_prompt_repository.list_versions(
        project_id=project.id, prompt_name=name, limit=limit
    )
    return {
        "project_id": project.id,
        "prompt_name": name,
        "items": [_prompt_version_to_dict(pv) for pv in versions],
    }


@app.get("/operators")
def list_operators(
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    items = [_operator_to_dict(op) for op in operator_repository.list_all()]
    return {"items": items}


@app.post("/operators")
def create_operator(
    request: OperatorCreateRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    if project_repository.get(request.project_id) is None:
        raise HTTPException(status_code=400, detail="project_not_found")
    try:
        operator = operator_repository.create(
            username=request.username,
            project_id=request.project_id,
            chat_id=request.chat_id,
            display_name=request.display_name,
        )
    except OperatorUsernameConflict as exc:
        raise HTTPException(
            status_code=409, detail="operator_username_conflict"
        ) from exc
    return _operator_to_dict(operator)


@app.get("/operators/by-username/{username:path}")
def get_operator_by_username(username: str) -> dict[str, object]:
    """Internal endpoint used by bot_gateway; intentionally unauthenticated.

    Returns 404 when the operator does not exist. The bot_gateway treats
    404 as "non-operator sender" and 5xx as "fall back to primary".
    """
    operator = operator_repository.find_by_username(username)
    if operator is None:
        raise HTTPException(status_code=404, detail="operator_not_found")
    return _operator_to_dict(operator)


@app.patch("/operators/{username:path}")
def patch_operator(
    username: str,
    request: OperatorUpdateRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    if (
        request.project_id is not None
        and project_repository.get(request.project_id) is None
    ):
        raise HTTPException(status_code=400, detail="project_not_found")
    try:
        operator = operator_repository.update(
            username=username,
            project_id=request.project_id,
            chat_id=request.chat_id,
            display_name=request.display_name,
            is_active=request.is_active,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="operator_not_found") from exc
    return _operator_to_dict(operator)


@app.get("/knowledge/candidates/by-operator-file/{short_id}")
def get_candidate_by_operator_short_id(short_id: str) -> dict[str, object]:
    """Internal endpoint used by bot_gateway admin commands.

    Finds the knowledge candidate that was uploaded via the operator file
    with the given short_id. Returns 404 when the upload predates the
    operator_short_id plumbing or did not originate from an operator
    upload.
    """
    candidate = knowledge_moderation_repository.find_by_operator_short_id(
        short_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate_not_found")
    return {
        "candidate_id": candidate.id,
        "operator_short_id": candidate.operator_short_id,
        "project_id": candidate.project_id,
        "source_file_name": candidate.source_file_name,
        "uploaded_by_operator_username": candidate.uploaded_by_operator_username,
    }


@app.post("/knowledge/candidates/{candidate_id}/reassign")
def reassign_candidate(
    candidate_id: int,
    request: FileReassignRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    if project_repository.get(request.project_id) is None:
        raise HTTPException(status_code=400, detail="project_not_found")
    try:
        knowledge_moderation_repository.get(candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="candidate_not_found") from exc
    knowledge_moderation_repository.set_project_id(
        candidate_id=candidate_id, project_id=request.project_id
    )
    rag_repository.update_project_id_for_source(
        source_id=f"knowledge_candidate:{candidate_id}",
        project_id=request.project_id,
    )
    return {"candidate_id": candidate_id, "project_id": request.project_id}


class AdminNlOpProposeRequest(BaseModel):
    admin_username: str
    utterance: str


class AdminNlOpConfirmRequest(BaseModel):
    confirm_token: str


def _session_to_dict(session: AdminNlOpSession) -> dict[str, object]:
    return {
        "id": session.id,
        "admin_username": session.admin_username,
        "utterance": session.utterance,
        "op_type": session.op_type,
        "payload": session.payload,
        "status": session.status,
        "confirm_token": session.confirm_token,
        "preview": session.preview,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _apply_admin_nl_op(session: AdminNlOpSession) -> None:
    """Execute the side-effect for a confirmed admin NL op."""
    payload = session.payload
    if session.op_type == OP_PROJECT_CREATE:
        try:
            project_repository.create(
                slug=str(payload["slug"]),
                name=str(payload["name"]),
            )
        except ProjectSlugConflict as exc:
            raise HTTPException(
                status_code=409, detail="project_slug_conflict"
            ) from exc
        return
    if session.op_type == OP_PROJECT_RENAME:
        try:
            project_repository.update(
                slug=str(payload["slug"]), name=str(payload["name"])
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail="project_not_found"
            ) from exc
        return
    if session.op_type == OP_OPERATOR_ATTACH:
        project = project_repository.get_by_slug(str(payload["project_slug"]))
        if project is None:
            raise HTTPException(
                status_code=400, detail="project_not_found"
            )
        chat_id = payload.get("chat_id")
        try:
            operator_repository.create(
                username=str(payload["username"]),
                project_id=project.id,
                chat_id=int(chat_id) if chat_id is not None else None,
            )
        except OperatorUsernameConflict as exc:
            raise HTTPException(
                status_code=409, detail="operator_username_conflict"
            ) from exc
        return
    if session.op_type == OP_OPERATOR_DETACH:
        try:
            operator_repository.update(
                username=str(payload["username"]), is_active=False
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail="operator_not_found"
            ) from exc
        return
    if session.op_type == OP_FILE_ATTACH:
        project = project_repository.get_by_slug(str(payload["project_slug"]))
        if project is None:
            raise HTTPException(
                status_code=400, detail="project_not_found"
            )
        candidate = knowledge_moderation_repository.find_by_operator_short_id(
            str(payload["short_id"])
        )
        if candidate is None:
            raise HTTPException(
                status_code=404, detail="candidate_not_found"
            )
        knowledge_moderation_repository.set_project_id(
            candidate_id=candidate.id, project_id=project.id
        )
        rag_repository.update_project_id_for_source(
            source_id=f"knowledge_candidate:{candidate.id}",
            project_id=project.id,
        )
        return
    # OP_CLARIFY or unknown — nothing to apply (caller validates state).
    raise HTTPException(status_code=400, detail="unconfirmable_op_type")


@app.post("/admin/nl-ops")
def admin_nl_ops_propose(
    request: AdminNlOpProposeRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    _ensure_admin_username(request.admin_username)
    session = admin_nl_ops_repository.propose(
        admin_username=request.admin_username, utterance=request.utterance
    )
    return _session_to_dict(session)


@app.post("/admin/nl-ops/{session_id}/confirm")
def admin_nl_ops_confirm(
    session_id: int,
    request: AdminNlOpConfirmRequest,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    try:
        admin_nl_ops_repository.get(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    try:
        confirmed = admin_nl_ops_repository.confirm(
            session_id=session_id, confirm_token=request.confirm_token
        )
    except InvalidConfirmToken as exc:
        raise HTTPException(status_code=401, detail="invalid_confirm_token") from exc
    except SessionNotPending as exc:
        raise HTTPException(status_code=409, detail=f"session_status:{exc}") from exc
    _apply_admin_nl_op(confirmed)
    return _session_to_dict(confirmed)


@app.post("/admin/nl-ops/{session_id}/cancel")
def admin_nl_ops_cancel(
    session_id: int,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    try:
        cancelled = admin_nl_ops_repository.cancel(session_id=session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionNotPending as exc:
        raise HTTPException(status_code=409, detail=f"session_status:{exc}") from exc
    return _session_to_dict(cancelled)


@app.get("/admin/nl-ops/latest-pending")
def admin_nl_ops_latest_pending(
    admin_username: str,
    _principal: Annotated[str, Depends(require_admin_or_internal)],
) -> dict[str, object]:
    """Returns the most recent pending session for an admin, or {found: false}."""
    session = admin_nl_ops_repository.latest_pending_for(admin_username)
    if session is None:
        return {"found": False}
    payload = _session_to_dict(session)
    payload["found"] = True
    return payload


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
    project_id: int | None = None


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
    operator_short_id: str | None = None
    project_id: int | None = None
    project_slug: str | None = None


class BotPersonaRequest(BaseModel):
    first_name: str
    last_name: str = ""
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


def _effective_inbound_ack_message(project_id: int | None = None) -> str:
    """Resolve the inbound ack with per-project precedence.

    Order: project-scoped override → global runtime_config → settings default.
    """
    if project_id is not None:
        override = project_prompt_repository.get(
            project_id=project_id, prompt_name="inbound_ack"
        )
        if override:
            return override
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


def _pick_assignee_for_chat(chat_id: int | None) -> str:
    """Prefer the operator who already handles this chat; fall back to primary.

    Sticky routing: if the customer's most recent ticket has an assigned
    `operator_username` that maps to an active operator in the registry,
    re-assign the new ticket to them. Otherwise fall back to the
    primary operator configured in settings/runtime config. Pure
    backwards-compat for single-operator deployments.
    """
    primary = _effective_hitl_operator_username()
    if chat_id is None:
        return primary
    try:
        latest = hitl_ticket_repository.latest_for_chat(chat_id)
    except AttributeError:
        latest = None
    if latest is None or not latest.operator_username:
        return primary
    if latest.operator_username == primary:
        return primary
    operator = operator_repository.find_by_username(latest.operator_username)
    if operator is not None and operator.is_active:
        return operator.username
    return primary


def _resolve_inbound_project_id(chat_id: int | None) -> int | None:
    """Resolve the project_id for an incoming customer message.

    Looks at the most recent HITL ticket assigned to the chat; if it
    has an `operator_username` that maps to a registered operator with
    a project binding, that project scopes RAG retrieval. Falls back
    to the default project (id from `ensure_default_project`) so
    pre-Epic-10 deployments behave identically.
    """
    default_project_id = _default_project_id()
    if chat_id is None:
        logger.info(
            "inbound_project_resolved",
            extra={
                "chat_id": None,
                "resolution_path": "no_chat_id",
                "resolved_project_id": default_project_id,
                "default_project_id": default_project_id,
                "latest_ticket_id": None,
                "ticket_operator_username": None,
                "operator_project_id": None,
            },
        )
        return default_project_id
    try:
        ticket = hitl_ticket_repository.latest_for_chat(chat_id)
    except AttributeError:
        ticket = None
    ticket_id = ticket.id if ticket is not None else None
    ticket_op = ticket.operator_username if ticket is not None else None
    if ticket is not None and ticket.operator_username:
        operator = operator_repository.find_by_username(
            ticket.operator_username
        )
        if operator is not None:
            logger.info(
                "inbound_project_resolved",
                extra={
                    "chat_id": chat_id,
                    "resolution_path": "from_ticket_operator",
                    "resolved_project_id": operator.project_id,
                    "default_project_id": default_project_id,
                    "latest_ticket_id": ticket_id,
                    "ticket_operator_username": ticket_op,
                    "operator_project_id": operator.project_id,
                },
            )
            return operator.project_id
    logger.info(
        "inbound_project_resolved",
        extra={
            "chat_id": chat_id,
            "resolution_path": "default_fallback",
            "resolved_project_id": default_project_id,
            "default_project_id": default_project_id,
            "latest_ticket_id": ticket_id,
            "ticket_operator_username": ticket_op,
            "operator_project_id": None,
        },
    )
    return default_project_id


def _default_project_id() -> int | None:
    default = project_repository.get_by_slug("default")
    return default.id if default is not None else None


def _resolve_upload_project_id(
    *,
    operator_username: str,
    project_id: int | None,
    project_slug: str | None,
    short_id: str | None = None,
) -> int | None:
    """Resolve project_id for an operator upload.

    Precedence: explicit project_id > project_slug > operator's
    project_id (from the operators registry) > default project.
    """
    if project_id is not None:
        logger.info(
            "operator_upload_project_resolved",
            extra={
                "short_id": short_id,
                "operator_username": operator_username,
                "precedence_path": "explicit_id",
                "resolved_project_id": project_id,
            },
        )
        return project_id
    if project_slug:
        project = project_repository.get_by_slug(project_slug)
        if project is not None:
            logger.info(
                "operator_upload_project_resolved",
                extra={
                    "short_id": short_id,
                    "operator_username": operator_username,
                    "precedence_path": "explicit_slug",
                    "resolved_project_id": project.id,
                    "project_slug": project_slug,
                },
            )
            return project.id
    operator = operator_repository.find_by_username(operator_username)
    if operator is not None:
        logger.info(
            "operator_upload_project_resolved",
            extra={
                "short_id": short_id,
                "operator_username": operator_username,
                "precedence_path": "operator_default",
                "resolved_project_id": operator.project_id,
            },
        )
        return operator.project_id
    fallback = _default_project_id()
    logger.info(
        "operator_upload_project_resolved",
        extra={
            "short_id": short_id,
            "operator_username": operator_username,
            "precedence_path": "system_default",
            "resolved_project_id": fallback,
        },
    )
    return fallback


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
        project_id=_resolve_inbound_project_id(chat_id),
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
        logger.info(
            "hitl_operator_notified",
            extra={
                "ticket_id": ticket_id,
                "operator_chat_id": None,
                "dm_sent": False,
                "skip_reason": "no_operator_chat_id_configured",
            },
        )
        return False
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        logger.info(
            "hitl_operator_notified",
            extra={
                "ticket_id": ticket_id,
                "operator_chat_id": chat_id_raw,
                "dm_sent": False,
                "skip_reason": "operator_chat_id_invalid",
            },
        )
        return False
    customer_label = customer_username or "unknown"
    text = f"HITL ticket #{ticket_id} | from {customer_label} | {question}"
    sent = await _safe_send_message(
        chat_id=chat_id,
        text=text,
        failure_summary="HITL operator notification failed",
        failure_kind="hitl_operator_notify_failed",
    )
    logger.info(
        "hitl_operator_notified",
        extra={
            "ticket_id": ticket_id,
            "operator_chat_id": chat_id,
            "dm_sent": sent,
        },
    )
    return sent


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
    hitl_ticket_id: int | None = None,
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
            hitl_ticket_id=hitl_ticket_id,
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

    logger.info(
        "inbound_received",
        extra={
            "trace_id": trace_id,
            "chat_id": request.chat_id,
            "customer_username": request.customer_username,
            "text_length": len(request.text),
            "text": request.text,
        },
    )

    # Idempotency: if we've already processed this trace_id, return the cached
    # outcome and skip the side effects (ack, ticket, operator notify). This
    # defends against duplicate /conversations/inbound calls — for example a
    # Telegram webhook retry that slips past the bot_gateway dedup, or a
    # script that posts the same trace_id twice.
    existing_trace = answer_trace_repository.find_by_trace_id(trace_id)
    if existing_trace is not None:
        logger.info(
            "inbound_idempotent_replay",
            extra={
                "trace_id": trace_id,
                "response_mode": existing_trace.response_mode,
                "hitl_ticket_id": existing_trace.hitl_ticket_id,
            },
        )
        return {
            "deduplicated": True,
            "delivered": False,
            "escalated": existing_trace.response_mode == "human_only",
            "response_mode": existing_trace.response_mode,
            "hitl_ticket_id": existing_trace.hitl_ticket_id,
            "trace_id": existing_trace.trace_id,
        }

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

    # Escalation path. Coalesce onto an active ticket for the same chat when
    # one exists so a customer's rapid follow-up questions become one human
    # conversation instead of N parallel tickets + N acks.
    active_ticket = (
        hitl_ticket_repository.find_active_for_chat(request.chat_id)
        if request.chat_id is not None
        else None
    )
    if active_ticket is not None:
        # Customer already has an active ticket. Don't re-ack (they got one
        # on the original message); just forward the follow-up to the
        # assigned operator as a continuation.
        operator_username = (
            active_ticket.operator_username or _effective_hitl_operator_username()
        )
        logger.info(
            "hitl_escalation_start",
            extra={
                "trace_id": trace_id,
                "chat_id": request.chat_id,
                "customer_username": request.customer_username,
                "has_existing_ticket": True,
                "existing_ticket_id": active_ticket.id,
                "ticket_operator_username": operator_username,
                "is_follow_up": True,
            },
        )
        await _notify_hitl_operator_with_question(
            ticket_id=active_ticket.id,
            question=f"[follow-up] {request.text}",
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
            limitations=["awaiting_human_response", "coalesced_follow_up"],
            hitl_ticket_id=active_ticket.id,
        )
        return {
            "delivered": False,
            "escalated": True,
            "response_mode": "human_only",
            "hitl_ticket_id": active_ticket.id,
            "hitl_operator_username": operator_username,
            "trace_id": persisted_trace_id,
            "coalesced": True,
        }

    ack_message = _effective_inbound_ack_message(project_id=ctx.project_id)
    logger.info(
        "hitl_escalation_start",
        extra={
            "trace_id": trace_id,
            "chat_id": request.chat_id,
            "customer_username": request.customer_username,
            "has_existing_ticket": False,
            "existing_ticket_id": None,
            "is_follow_up": False,
        },
    )
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
    assignee = _pick_assignee_for_chat(request.chat_id)
    hitl_ticket_repository.assign(
        ticket_id=ticket.id,
        operator_username=assignee,
    )
    logger.info(
        "hitl_ticket_created",
        extra={
            "trace_id": trace_id,
            "ticket_id": ticket.id,
            "operator_username": assignee,
            "reason": "awaiting_human_response",
            "conversation_ref_snippet": request.text[:120],
        },
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
        hitl_ticket_id=ticket.id,
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
    chunks = rag_repository.retrieve(
        query=request.query,
        limit=request.limit,
        project_id=request.project_id,
    )
    return {
        "items": [
            {
                "id": chunk.id,
                "source_id": chunk.source_id,
                "chunk_text": chunk.chunk_text,
                "score": chunk.score,
                "project_id": chunk.project_id,
                "is_confidential": chunk.is_confidential,
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
    effective_operator = (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or settings.hitl_primary_operator_username
    )
    if request.updated_by != effective_operator:
        raise HTTPException(status_code=403, detail="not_authorized")

    first_name = _validate_persona_name(request.first_name)
    # Last name is optional: callers can rename the bot to a single given name
    # ("Анна") without inventing a surname. Empty / whitespace-only values are
    # accepted and stored as an empty string; a present non-empty value is
    # validated by the same regex as the first name.
    last_name_raw = request.last_name.strip() if request.last_name else ""
    last_name = _validate_persona_name(last_name_raw) if last_name_raw else ""

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

    full_name = f"{first_name} {last_name}".strip()
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
        name=f"{first_name} {last_name}".strip(),
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
    {
        "pdf",
        "docx",
        "pptx",
        "txt",
        "image",
        "audio",
        "video",
        "inline_text",
        "xlsx",
        "csv",
        "html",
        "md",
        "rtf",
        "epub",
        "zip",
    }
)
_OPERATOR_UPLOAD_MEDIA_TYPES = frozenset({"audio", "video"})
_operator_transcriber: object | None = None


def _get_operator_transcriber() -> object:
    global _operator_transcriber
    if _operator_transcriber is None:
        from services.api.app.operator_uploads.extractors import WhisperTranscriber

        _operator_transcriber = WhisperTranscriber()
    return _operator_transcriber


async def _perform_operator_upload(request: OperatorUploadRequest) -> dict[str, object]:
    from services.api.app.operator_uploads.extractors import (
        EXTRACTORS,
        ExtractionError,
        binary_sha256,
        extract_media,
        soft_wrap,
    )

    logger.info(
        "operator_upload_received",
        extra={
            "operator_username": request.operator_username,
            "operator_short_id": request.operator_short_id,
            "source_file_type": request.source_file_type,
            "source_file_name": request.source_file_name,
            "is_confidential": request.is_confidential,
            "stored_binary_path": request.stored_binary_path,
            "project_id_requested": request.project_id,
            "project_slug_requested": request.project_slug,
        },
    )

    if request.source_file_type not in _OPERATOR_UPLOAD_TYPES:
        logger.info(
            "operator_upload_failed",
            extra={
                "operator_username": request.operator_username,
                "stage": "type_validation",
                "error": "unsupported_source_file_type",
                "source_file_type": request.source_file_type,
            },
        )
        raise HTTPException(status_code=422, detail="unsupported_source_file_type")

    sha: str | None = None
    if request.source_file_type == "inline_text":
        if not request.inline_text or not request.inline_text.strip():
            raise HTTPException(status_code=422, detail="empty_inline_text")
    else:
        if not request.stored_binary_path:
            raise HTTPException(status_code=422, detail="missing_stored_binary_path")
        binary_path = Path(request.stored_binary_path)
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
                Path(request.stored_binary_path),  # type: ignore[arg-type]
                transcriber=_get_operator_transcriber(),  # type: ignore[arg-type]
            )
        else:
            extractor = EXTRACTORS[request.source_file_type]
            raw_text = extractor(Path(request.stored_binary_path))  # type: ignore[arg-type]
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
        logger.info(
            "operator_upload_failed",
            extra={
                "operator_username": request.operator_username,
                "stage": "extraction",
                "error": repr(exc),
                "source_file_type": request.source_file_type,
                "incident_id": incident.id,
            },
        )
        raise HTTPException(status_code=500, detail="operator_upload_failed") from exc

    logger.info(
        "operator_upload_extracted",
        extra={
            "operator_username": request.operator_username,
            "operator_short_id": request.operator_short_id,
            "source_file_type": request.source_file_type,
            "extracted_text_length": len(raw_text),
        },
    )

    wrapped = soft_wrap(raw_text)
    if not wrapped.strip():
        logger.info(
            "operator_upload_failed",
            extra={
                "operator_username": request.operator_username,
                "stage": "soft_wrap",
                "error": "empty_text_after_wrap",
                "raw_text_length": len(raw_text),
            },
        )
        raise HTTPException(status_code=422, detail="empty_text")

    resolved_project_id = _resolve_upload_project_id(
        operator_username=request.operator_username,
        project_id=request.project_id,
        project_slug=request.project_slug,
        short_id=request.operator_short_id,
    )
    candidate = knowledge_moderation_repository.create_approved_operator_upload(
        candidate_text=raw_text,
        published_text=wrapped,
        operator_username=request.operator_username,
        is_confidential=request.is_confidential,
        source_file_name=request.source_file_name,
        source_file_type=request.source_file_type,
        stored_binary_path=request.stored_binary_path,
        binary_sha256=sha,
        operator_short_id=request.operator_short_id,
    )
    if resolved_project_id is not None:
        knowledge_moderation_repository.set_project_id(
            candidate_id=candidate.id, project_id=resolved_project_id
        )
    source_id = f"knowledge_candidate:{candidate.id}"
    inserted_chunks = rag_repository.ingest(
        source_id=source_id,
        text=wrapped,
        is_confidential=request.is_confidential,
        project_id=resolved_project_id,
    )
    logger.info(
        "operator_upload_ingested",
        extra={
            "operator_username": request.operator_username,
            "operator_short_id": request.operator_short_id,
            "candidate_id": candidate.id,
            "source_id": source_id,
            "inserted_chunks": inserted_chunks,
            "project_id": resolved_project_id,
            "is_confidential": request.is_confidential,
            "extracted_chars": len(wrapped),
        },
    )
    return {
        "candidate_id": candidate.id,
        "source_id": source_id,
        "inserted_chunks": inserted_chunks,
        "extracted_chars": len(wrapped),
        "is_confidential": request.is_confidential,
        "deduplicated": False,
    }


@app.post("/knowledge/operator_upload")
async def operator_upload(request: OperatorUploadRequest) -> dict[str, object]:
    return await _perform_operator_upload(request)


_EXTENSION_TO_SOURCE_TYPE: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".txt": "txt",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    ".tiff": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "video",
    ".avi": "video",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".rtf": "rtf",
    ".epub": "epub",
    ".zip": "zip",
}


def _infer_source_file_type(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = Path(filename).suffix.lower()
    return _EXTENSION_TO_SOURCE_TYPE.get(suffix)


@app.post("/knowledge/operator_upload_multipart")
async def operator_upload_multipart(
    operator_username: Annotated[str, Form()],
    is_confidential: Annotated[bool, Form()] = False,
    source_file_type: Annotated[str | None, Form()] = None,
    inline_text: Annotated[str | None, Form()] = None,
    upload: Annotated[UploadFile | None, File()] = None,
) -> dict[str, object]:
    has_file = upload is not None
    has_inline = inline_text is not None and inline_text.strip() != ""
    if has_file and has_inline:
        raise HTTPException(status_code=422, detail="file_and_inline_text_both_set")
    if not has_file and not has_inline:
        raise HTTPException(status_code=422, detail="file_or_inline_text_required")

    if has_inline:
        request = OperatorUploadRequest(
            operator_username=operator_username,
            source_file_type="inline_text",
            inline_text=inline_text,
            is_confidential=is_confidential,
        )
        return await _perform_operator_upload(request)

    assert upload is not None
    inferred_type = source_file_type or _infer_source_file_type(upload.filename)
    if inferred_type is None:
        raise HTTPException(status_code=422, detail="unknown_source_file_type")

    storage_dir = Path(settings.operator_upload_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "").suffix
    stored_path = storage_dir / f"{uuid.uuid4().hex}{suffix}"
    contents = await upload.read()
    if len(contents) > settings.operator_upload_max_bytes:
        raise HTTPException(status_code=413, detail="upload_too_large")
    stored_path.write_bytes(contents)

    request = OperatorUploadRequest(
        operator_username=operator_username,
        source_file_type=inferred_type,
        source_file_name=upload.filename,
        stored_binary_path=str(stored_path),
        is_confidential=is_confidential,
    )
    return await _perform_operator_upload(request)


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
