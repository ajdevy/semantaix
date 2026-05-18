"""Admin/operator file inspection and deletion endpoints.

GET /admin/files, /admin/files/{short_id}, /admin/files/search
DELETE /admin/files/{short_id}, DELETE /admin/files?confirm=true

All routes require either a cookie session (from the admin auth router) or an
internal service token + ``as_user`` query parameter (so the bot_gateway can
query on behalf of an operator). Access rules are enforced inside
``OperatorFilesView`` and ``OperatorFilesAdminWriter`` via SQL WHERE clauses.

Story 09.07: deletion cascades to ``knowledge_moderation_candidates`` and
``rag_chunks`` so customer answers stop using the removed file immediately.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Request

from services.api.app.admin_auth import AdminAuthService
from services.api.app.operator_files_admin import OperatorFilesAdminWriter
from services.api.app.operator_files_view import OperatorFilesView

_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 200
_SEARCH_DEFAULT_LIMIT = 10
_SEARCH_MAX_LIMIT = 50
_SEARCH_MIN_QUERY = 2


def wire_admin_files_routes(
    app: FastAPI,
    *,
    auth_service: AdminAuthService,
    files_view: OperatorFilesView,
    files_admin_writer: OperatorFilesAdminWriter,
) -> None:
    @app.get("/admin/files")
    def _list_files(
        request: Request,
        limit: int = Query(_LIST_DEFAULT_LIMIT, ge=1, le=_LIST_MAX_LIMIT),
        owner: str | None = None,
        as_user: str | None = None,
    ) -> dict:
        principal = auth_service.require_session_or_internal(request, as_user)
        items = files_view.list_files(
            viewer_username=principal.username,
            viewer_role=principal.role,
            limit=limit,
            owner_filter=owner,
        )
        return {
            "items": [asdict(item) for item in items],
            "total": len(items),
        }

    @app.get("/admin/files/search")
    def _search_files(
        request: Request,
        q: str = Query(..., min_length=0),
        limit: int = Query(_SEARCH_DEFAULT_LIMIT, ge=1, le=_SEARCH_MAX_LIMIT),
        as_user: str | None = None,
    ) -> dict:
        if len(q.strip()) < _SEARCH_MIN_QUERY:
            raise HTTPException(status_code=400, detail="query_too_short")
        principal = auth_service.require_session_or_internal(request, as_user)
        hits = files_view.search_files(
            query=q.strip(),
            viewer_username=principal.username,
            viewer_role=principal.role,
            limit=limit,
        )
        return {
            "items": [asdict(hit) for hit in hits],
            "total": len(hits),
        }

    @app.get("/admin/files/{short_id}")
    def _file_detail(
        request: Request,
        short_id: str,
        as_user: str | None = None,
    ) -> dict:
        principal = auth_service.require_session_or_internal(request, as_user)
        detail = files_view.get_file(
            short_id=short_id,
            viewer_username=principal.username,
            viewer_role=principal.role,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="not_found")
        return asdict(detail)

    @app.delete("/admin/files/{short_id}")
    def _delete_file(
        request: Request,
        short_id: str,
        as_user: str | None = None,
    ) -> dict:
        principal = auth_service.require_session_or_internal(request, as_user)
        summary = files_admin_writer.delete(
            short_id=short_id,
            viewer_username=principal.username,
            viewer_role=principal.role,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail="not_found")
        return asdict(summary)

    @app.delete("/admin/files")
    def _delete_all_files(
        request: Request,
        confirm: bool = False,
        as_user: str | None = None,
    ) -> dict:
        principal = auth_service.require_session_or_internal(request, as_user)
        if not confirm:
            raise HTTPException(status_code=400, detail="confirm_required")
        summary = files_admin_writer.delete_all_for_user(
            username=principal.username,
        )
        return asdict(summary)
