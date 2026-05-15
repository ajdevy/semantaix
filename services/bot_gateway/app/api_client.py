from __future__ import annotations

import httpx


class ApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 10,
        internal_token: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._internal_token = internal_token

    def _internal_headers(self) -> dict[str, str]:
        if not self._internal_token:
            return {}
        return {"X-Internal-Token": self._internal_token}

    async def forward_inbound(
        self,
        *,
        text: str,
        chat_id: int,
        customer_username: str | None,
        trace_id: str,
    ) -> dict:
        payload = {
            "text": text,
            "chat_id": chat_id,
            "customer_username": customer_username,
            "trace_id": trace_id,
        }
        return await self._post("/conversations/inbound", payload)

    async def deliver_operator_reply(
        self, *, ticket_id: int, operator_username: str, reply_text: str
    ) -> dict:
        payload = {"operator_username": operator_username, "reply_text": reply_text}
        return await self._post(f"/hitl/tickets/{ticket_id}/reply", payload)

    async def submit_operator_upload(
        self,
        *,
        operator_username: str,
        source_file_type: str,
        source_file_name: str | None,
        stored_binary_path: str | None,
        is_confidential: bool,
        inline_text: str | None = None,
        operator_short_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        payload = {
            "operator_username": operator_username,
            "source_file_type": source_file_type,
            "source_file_name": source_file_name,
            "stored_binary_path": stored_binary_path,
            "is_confidential": is_confidential,
            "inline_text": inline_text,
            "operator_short_id": operator_short_id,
        }
        return await self._post(
            "/knowledge/operator_upload",
            payload,
            timeout_override=timeout_seconds,
        )

    async def list_projects(self) -> dict:
        return await self._get("/projects", auth=True)

    async def create_project(
        self, *, slug: str, name: str, description: str | None = None
    ) -> dict:
        return await self._post(
            "/projects",
            {"slug": slug, "name": name, "description": description},
            auth=True,
        )

    async def list_operators(self) -> dict:
        return await self._get("/operators", auth=True)

    async def attach_operator(
        self,
        *,
        username: str,
        project_id: int,
        chat_id: int | None = None,
        display_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "username": username,
            "project_id": project_id,
        }
        if chat_id is not None:
            payload["chat_id"] = chat_id
        if display_name is not None:
            payload["display_name"] = display_name
        return await self._post("/operators", payload, auth=True)

    async def detach_operator(self, *, username: str) -> dict:
        return await self._patch(
            f"/operators/{username}", {"is_active": False}, auth=True
        )

    async def find_candidate_by_short_id(self, *, short_id: str) -> dict:
        return await self._get(
            f"/knowledge/candidates/by-operator-file/{short_id}", auth=True
        )

    async def reassign_candidate(
        self, *, candidate_id: int, project_id: int
    ) -> dict:
        return await self._post(
            f"/knowledge/candidates/{candidate_id}/reassign",
            {"project_id": project_id},
            auth=True,
        )

    async def set_persona(
        self,
        *,
        first_name: str,
        last_name: str,
        updated_by: str,
        description: str | None = None,
        short_description: str | None = None,
    ) -> dict:
        payload: dict[str, str] = {
            "first_name": first_name,
            "last_name": last_name,
            "updated_by": updated_by,
        }
        if description is not None:
            payload["description"] = description
        if short_description is not None:
            payload["short_description"] = short_description
        return await self._post("/hitl/runtime-config/persona", payload)

    async def fetch_file_inspect(
        self,
        *,
        short_id: str,
        requester_username: str,
        internal_token: str,
    ) -> dict | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base_url}/admin/files/{short_id}",
                params={"as_user": requester_username},
                headers={"Authorization": f"Bearer {internal_token}"},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def search_files(
        self,
        *,
        query: str,
        requester_username: str,
        internal_token: str,
        limit: int = 10,
    ) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base_url}/admin/files/search",
                params={
                    "q": query,
                    "as_user": requester_username,
                    "limit": limit,
                },
                headers={"Authorization": f"Bearer {internal_token}"},
            )
        response.raise_for_status()
        return response.json()

    async def _post(
        self,
        path: str,
        json: dict,
        *,
        timeout_override: int | None = None,
        auth: bool = False,
    ) -> dict:
        timeout = timeout_override if timeout_override is not None else self._timeout
        headers = self._internal_headers() if auth else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url}{path}", json=json, headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def _get(self, path: str, *, auth: bool = False) -> dict:
        headers = self._internal_headers() if auth else None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base_url}{path}", headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def _patch(
        self, path: str, json: dict, *, auth: bool = False
    ) -> dict:
        headers = self._internal_headers() if auth else None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.patch(
                f"{self._base_url}{path}", json=json, headers=headers
            )
            response.raise_for_status()
            return response.json()
