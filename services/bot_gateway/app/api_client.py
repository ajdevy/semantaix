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
