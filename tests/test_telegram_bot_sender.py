import pytest

from services.api.app import telegram_bot_sender as sender_module
from services.api.app.telegram_bot_sender import TelegramBotSender


@pytest.mark.asyncio
async def test_send_message_requires_token():
    sender = TelegramBotSender(bot_token="")
    with pytest.raises(RuntimeError, match="missing_bot_token"):
        await sender.send_message(chat_id=1, text="test")


@pytest.mark.asyncio
async def test_send_message_rejects_placeholder_token():
    sender = TelegramBotSender(bot_token="replace-me")
    with pytest.raises(RuntimeError, match="missing_bot_token"):
        await sender.send_message(chat_id=1, text="test")


class _CapturingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json):
        self.calls.append((url, json))

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": {"message_id": 77}}

        return _Resp()


@pytest.mark.asyncio
async def test_send_message_success(monkeypatch):
    captured: list[tuple[str, dict]] = []

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
            captured.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(sender_module.httpx, "AsyncClient", lambda timeout: FakeClient())
    sender = TelegramBotSender(bot_token="token")
    assert await sender.send_message(chat_id=10, text="hello") == 77
    assert captured[0][0].endswith("/bot{0}/sendMessage".format("token"))
    assert captured[0][1] == {"chat_id": 10, "text": "hello"}


@pytest.mark.asyncio
async def test_set_my_name_posts_setMyName_payload(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(sender_module.httpx, "AsyncClient", lambda timeout: client)
    sender = TelegramBotSender(bot_token="abc")
    result = await sender.set_my_name(name="Анна Иванова")
    assert result == {"ok": True, "result": {"message_id": 77}}
    assert client.calls[0][0].endswith("/botabc/setMyName")
    assert client.calls[0][1] == {"name": "Анна Иванова"}


@pytest.mark.asyncio
async def test_set_my_description_posts_setMyDescription_payload(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(sender_module.httpx, "AsyncClient", lambda timeout: client)
    sender = TelegramBotSender(bot_token="abc")
    await sender.set_my_description(description="Здравствуйте!")
    assert client.calls[0][0].endswith("/botabc/setMyDescription")
    assert client.calls[0][1] == {"description": "Здравствуйте!"}


@pytest.mark.asyncio
async def test_set_my_short_description_posts_setMyShortDescription_payload(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(sender_module.httpx, "AsyncClient", lambda timeout: client)
    sender = TelegramBotSender(bot_token="abc")
    await sender.set_my_short_description(short_description="На связи.")
    assert client.calls[0][0].endswith("/botabc/setMyShortDescription")
    assert client.calls[0][1] == {"short_description": "На связи."}


@pytest.mark.asyncio
async def test_identity_methods_skip_when_token_unconfigured():
    sender_a = TelegramBotSender(bot_token="")
    sender_b = TelegramBotSender(bot_token="replace-me")
    for sender in (sender_a, sender_b):
        result = await sender.set_my_name(name="x")
        assert result == {"ok": False, "skipped": "missing_bot_token"}
        result = await sender.set_my_description(description="x")
        assert result == {"ok": False, "skipped": "missing_bot_token"}
        result = await sender.set_my_short_description(short_description="x")
        assert result == {"ok": False, "skipped": "missing_bot_token"}
