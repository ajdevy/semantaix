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

    async def _post(self, path: str, json: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}{path}", json=json)
            response.raise_for_status()
            return response.json()
