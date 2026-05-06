import pytest

from services.api.app import telegram_bot_sender as sender_module
from services.api.app.telegram_bot_sender import TelegramBotSender


@pytest.mark.asyncio
async def test_send_message_requires_token():
    sender = TelegramBotSender(bot_token="")
    with pytest.raises(RuntimeError, match="missing_bot_token"):
        await sender.send_message(chat_id=1, text="test")


@pytest.mark.asyncio
async def test_send_message_success(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"message_id": 77}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            assert "sendMessage" in url
            assert json == {"chat_id": 10, "text": "hello"}
            return FakeResponse()

    monkeypatch.setattr(sender_module.httpx, "AsyncClient", lambda timeout: FakeClient())
    sender = TelegramBotSender(bot_token="token")
    assert await sender.send_message(chat_id=10, text="hello") == 77
