"""Admin RAG retrieval inspection endpoint.

GET /admin/rag/inspect — returns the full retrieval decision for a query so
operators can diagnose "why didn't the bot answer this?" without replaying
sqlite queries against `.data/*.db`.

Auth: cookie session OR internal service token (mirrors admin_files.py).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request

from services.api.app.admin_auth import AdminAuthService
from services.api.app.rag import RagRepository, _tokenize
from services.api.app.russian_text import get_retrieval_stopwords

_TEXT_SNIPPET_MAX = 200


def _kb_ingest_status_summary(operator_files_db_path: str) -> dict[str, int]:
    path = Path(operator_files_db_path)
    if not path.exists():
        return {}
    uri = f"file:{path}?mode=ro"
    summary: dict[str, int] = {}
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT kb_ingest_status, COUNT(*) AS n "
            "FROM operator_files GROUP BY kb_ingest_status"
        ).fetchall()
    for row in rows:
        summary[str(row["kb_ingest_status"])] = int(row["n"])
    return summary


def _operator_files_for_sources(
    operator_files_db_path: str, source_ids: list[str]
) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    path = Path(operator_files_db_path)
    if not path.exists():
        return []
    # Source ids are formatted "knowledge_candidate:{candidate.id}" by
    # _perform_operator_upload. Map back to candidate ids and join.
    candidate_ids: list[int] = []
    for sid in source_ids:
        prefix = "knowledge_candidate:"
        if sid.startswith(prefix):
            try:
                candidate_ids.append(int(sid.removeprefix(prefix)))
            except ValueError:
                continue
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT short_id, kb_ingest_status, kb_inserted_chunks, "
            f"is_confidential, project_id, source_file_name, "
            f"knowledge_candidate_id, username, created_at "
            f"FROM operator_files "
            f"WHERE knowledge_candidate_id IN ({placeholders}) "
            f"ORDER BY created_at DESC",
            candidate_ids,
        ).fetchall()
    return [
        {
            "short_id": str(row["short_id"]),
            "kb_ingest_status": str(row["kb_ingest_status"]),
            "kb_inserted_chunks": (
                int(row["kb_inserted_chunks"])
                if row["kb_inserted_chunks"] is not None
                else None
            ),
            "is_confidential": bool(row["is_confidential"]),
            "project_id": (
                int(row["project_id"]) if row["project_id"] is not None else None
            ),
            "source_file_name": (
                str(row["source_file_name"])
                if row["source_file_name"] is not None
                else None
            ),
            "knowledge_candidate_id": int(row["knowledge_candidate_id"]),
            "uploaded_by": str(row["username"]),
            "uploaded_at": str(row["created_at"]),
        }
        for row in rows
    ]


def wire_admin_rag_inspect_routes(
    app: FastAPI,
    *,
    auth_service: AdminAuthService,
    rag_repository: RagRepository,
    operator_files_db_path: Callable[[], str],
    resolve_inbound_project_id: Callable[[int | None], int | None],
    default_project_id: Callable[[], int | None],
    grounding_threshold: Callable[[], float],
) -> None:
    @app.get("/admin/rag/inspect")
    def _inspect(
        request: Request,
        query: str = Query(..., min_length=1),
        chat_id: int | None = None,
        project_id: int | None = None,
        limit: int = Query(10, ge=1, le=50),
        as_user: str | None = None,
    ) -> dict[str, object]:
        auth_service.require_session_or_internal(request, as_user)

        lemmas_all = sorted(_tokenize(query))
        stopwords = get_retrieval_stopwords()
        lemmas_stopwords_removed = sorted(set(lemmas_all) & stopwords)
        lemmas_content = sorted(set(lemmas_all) - stopwords)
        scoring_tokens = lemmas_content or lemmas_all
        denominator = len(scoring_tokens)

        resolved_project_id = (
            project_id
            if project_id is not None
            else resolve_inbound_project_id(chat_id)
        )
        threshold = grounding_threshold()
        chunks = rag_repository.retrieve(
            query=query, limit=limit, project_id=resolved_project_id
        )
        candidates: list[dict[str, object]] = []
        chunk_tokens_cache: dict[int, set[str]] = {}
        scoring_set = set(scoring_tokens)
        for chunk in chunks:
            chunk_tokens = _tokenize(chunk.chunk_text)
            chunk_tokens_cache[chunk.id] = chunk_tokens
            candidates.append(
                {
                    "id": chunk.id,
                    "source_id": chunk.source_id,
                    "project_id": chunk.project_id,
                    "is_confidential": chunk.is_confidential,
                    "score": chunk.score,
                    "chunk_text_snippet": chunk.chunk_text[:_TEXT_SNIPPET_MAX],
                    "matched_lemmas": sorted(scoring_set & chunk_tokens),
                }
            )
        db_path = operator_files_db_path()
        source_ids_for_join = [c.source_id for c in chunks]
        operator_files = _operator_files_for_sources(
            db_path, source_ids_for_join
        )
        kb_summary = _kb_ingest_status_summary(db_path)

        return {
            "query": query,
            "lemmas_all": lemmas_all,
            "lemmas_content": lemmas_content,
            "lemmas_stopwords_removed": lemmas_stopwords_removed,
            "denominator": denominator,
            "chat_id": chat_id,
            "resolved_project_id": resolved_project_id,
            "default_project_id": default_project_id(),
            "threshold": threshold,
            "candidates": candidates,
            "top_chunk_passes_threshold": bool(
                candidates and candidates[0]["score"] >= threshold
            ),
            "operator_files": operator_files,
            "kb_ingest_status_summary": kb_summary,
        }
