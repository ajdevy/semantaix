"""Admin/operator auth — schema (Epic 10) + session-cookie routes.

Two complementary pieces live here:

- ``AdminAuthRepository`` owns the legacy schema used by Epic 10 stories 10.01+
  for short-lived login codes and opaque session tokens (admin_login_codes,
  admin_sessions tables).
- ``AdminAuthService`` + ``wire_admin_auth_routes`` implement the
  inspect-extracted-text feature's auth surface: four endpoints
  (request_code, verify, me, logout) plus a ``require_session`` FastAPI
  dependency that returns a ``SessionPrincipal``. A second dependency
  ``require_session_or_internal`` lets internal services (currently the
  bot_gateway) bypass the cookie by passing
  ``Authorization: Bearer <internal_service_token>`` with an ``as_user``
  query parameter, so bot commands can scope ``/admin/files`` to the
  requesting user.

The service uses ``WebAuthRepository`` for code/session state; ``AdminAuthRepository``
remains for the Epic 10 stack to evolve independently.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from services.api.app.operator_chat_lookup import resolve_chat_id_for_username
from services.api.app.web_auth import WebAuthRepository


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class AdminAuthRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_login_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_username TEXT NOT NULL,
                    code_sha256 TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_codes_username "
                "ON admin_login_codes(admin_username)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_sha256 TEXT PRIMARY KEY,
                    admin_username TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )


class RequestCodeBody(BaseModel):
    username: str


class VerifyBody(BaseModel):
    username: str
    code: str


@dataclass(frozen=True)
class SessionPrincipal:
    username: str
    role: str


def _normalize_username(name: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        return ""
    if not candidate.startswith("@"):
        candidate = "@" + candidate
    return candidate


class AdminAuthService:
    def __init__(
        self,
        *,
        web_auth_repository: WebAuthRepository,
        hitl_repository,
        telegram_bot_sender,
        settings,
    ) -> None:
        self.web_auth_repository = web_auth_repository
        self.hitl_repository = hitl_repository
        self.telegram_bot_sender = telegram_bot_sender
        self.settings = settings

    def _resolve_chat_id(self, username: str) -> int | None:
        return resolve_chat_id_for_username(
            username=username,
            operator_files_db_path=self.settings.operator_files_db_path,
            hitl_repository=self.hitl_repository,
            primary_operator_username=self.settings.hitl_primary_operator_username,
        )

    def resolve_role(self, username: str) -> str | None:
        if username == self.settings.hitl_config_admin_username:
            return "admin"
        if username == self.settings.hitl_primary_operator_username:
            return "operator"
        # Anyone with at least one operator_files row counts as operator.
        if self._resolve_chat_id(username) is not None:
            return "operator"
        return None

    async def request_code(self, raw_username: str) -> dict:
        username = _normalize_username(raw_username)
        if not username:
            raise HTTPException(status_code=404, detail="username_unknown_or_chat_id_missing")
        chat_id = self._resolve_chat_id(username)
        if chat_id is None:
            raise HTTPException(
                status_code=404, detail="username_unknown_or_chat_id_missing"
            )
        code = self.web_auth_repository.create_code(
            username=username, chat_id=chat_id
        )
        await self.telegram_bot_sender.send_message(
            chat_id=chat_id,
            text=f"Код входа в админку: {code} (действует 5 минут).",
        )
        return {"sent": True}

    def verify(
        self, raw_username: str, code: str, response: Response
    ) -> dict:
        username = _normalize_username(raw_username)
        outcome = self.web_auth_repository.consume_code(
            username=username, code=code
        )
        if not outcome.ok:
            if outcome.reason == "expired":
                raise HTTPException(status_code=410, detail="expired")
            if outcome.reason == "too_many_attempts":
                raise HTTPException(status_code=429, detail="too_many_attempts")
            raise HTTPException(status_code=401, detail="invalid")
        role = self.resolve_role(username)
        if role is None:
            raise HTTPException(status_code=403, detail="not_allowed")
        # Rotate sessions for this user — old cookies become invalid.
        self.web_auth_repository.revoke_all_for_username(username=username)
        session_id = self.web_auth_repository.create_session(
            username=username, role=role
        )
        response.set_cookie(
            key=self.settings.web_session_cookie_name,
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=self.settings.web_session_cookie_secure,
            path="/",
        )
        return {"username": username, "role": role}

    def require_session(self, request: Request) -> SessionPrincipal:
        cookie = request.cookies.get(self.settings.web_session_cookie_name)
        if not cookie:
            raise HTTPException(status_code=401, detail="no_session")
        session = self.web_auth_repository.get_session(session_id=cookie)
        if session is None:
            raise HTTPException(status_code=401, detail="invalid_session")
        self.web_auth_repository.touch_session(session_id=cookie)
        return SessionPrincipal(username=session.username, role=session.role)


def wire_admin_auth_routes(app: FastAPI, *, service: AdminAuthService) -> None:
    @app.post("/admin/auth/request_code")
    async def _request_code(payload: RequestCodeBody) -> dict:
        return await service.request_code(payload.username)

    @app.post("/admin/auth/verify")
    def _verify(payload: VerifyBody, response: Response) -> dict:
        return service.verify(payload.username, payload.code, response)

    @app.get("/admin/auth/me")
    def _me(request: Request) -> dict:
        principal = service.require_session(request)
        return {"username": principal.username, "role": principal.role}

    @app.post("/admin/auth/logout")
    def _logout(request: Request, response: Response) -> dict:
        cookie = request.cookies.get(service.settings.web_session_cookie_name)
        if cookie:
            service.web_auth_repository.revoke_session(session_id=cookie)
        response.delete_cookie(
            key=service.settings.web_session_cookie_name, path="/"
        )
        return {"ok": True}
