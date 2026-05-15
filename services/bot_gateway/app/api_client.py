from __future__ import annotations

import httpx


class ApiClient:
    def __init__(self, *, base_url: str, timeout_seconds: int = 10) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

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
        timeout_seconds: int | None = None,
    ) -> dict:
        payload = {
            "operator_username": operator_username,
            "source_file_type": source_file_type,
            "source_file_name": source_file_name,
            "stored_binary_path": stored_binary_path,
            "is_confidential": is_confidential,
            "inline_text": inline_text,
        }
        return await self._post(
            "/knowledge/operator_upload",
            payload,
            timeout_override=timeout_seconds,
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
    ) -> dict:
        timeout = timeout_override if timeout_override is not None else self._timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{self._base_url}{path}", json=json)
            response.raise_for_status()
            return response.json()
