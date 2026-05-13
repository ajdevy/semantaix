from __future__ import annotations

import pytest

from services.bot_gateway.app.telegram_update import (
    NormalizedTelegramMessage,
    TelegramAttachment,
    TelegramUpdateValidationError,
    normalize_update,
)


def _base_message(**overrides):
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "chat": {"id": 3},
            "from": {"id": 4, "username": "ajdevy"},
        },
    }
    payload["message"].update(overrides)
    return payload


def test_document_only_message_is_normalized():
    payload = _base_message(
        document={
            "file_id": "DOC123",
            "file_name": "deck.pdf",
            "mime_type": "application/pdf",
            "file_size": 12345,
        },
    )
    result = normalize_update(payload)
    assert isinstance(result, NormalizedTelegramMessage)
    assert result.text == ""
    assert result.attachments == (
        TelegramAttachment(
            file_id="DOC123",
            kind="document",
            mime_type="application/pdf",
            file_size=12345,
            file_name="deck.pdf",
        ),
    )


def test_photo_array_picks_largest():
    payload = _base_message(
        photo=[
            {"file_id": "P1", "width": 90, "height": 90, "file_size": 100},
            {"file_id": "P2", "width": 320, "height": 320, "file_size": 5000},
            {"file_id": "P3", "width": 1280, "height": 1280, "file_size": 80000},
        ],
        caption="/kb_add",
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.caption == "/kb_add"
    assert len(result.attachments) == 1
    assert result.attachments[0].file_id == "P3"
    assert result.attachments[0].kind == "photo"


def test_photo_falls_back_to_dimensions_when_size_missing():
    payload = _base_message(
        photo=[
            {"file_id": "P1", "width": 90, "height": 90},
            {"file_id": "P2", "width": 320, "height": 240},
        ],
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.attachments[0].file_id == "P2"


def test_photo_with_no_valid_items_returns_none():
    payload = _base_message(photo=[{"width": 50}])
    assert normalize_update(payload) is None


def test_photo_with_only_non_dict_entries_returns_none():
    payload = _base_message(photo=[None, "not-a-dict"])
    assert normalize_update(payload) is None


def test_photo_skips_non_dict_entries_then_picks_valid():
    payload = _base_message(
        photo=[
            None,
            "junk",
            {"file_id": "OK", "width": 100, "height": 100, "file_size": 4096},
        ],
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.attachments[0].file_id == "OK"


def test_voice_message_with_caption():
    payload = _base_message(
        voice={"file_id": "V1", "duration": 5, "file_size": 4096, "mime_type": "audio/ogg"},
        caption="сохрани в kb",
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.caption == "сохрани в kb"
    assert result.attachments[0].kind == "voice"
    assert result.attachments[0].mime_type == "audio/ogg"


def test_video_message():
    payload = _base_message(
        video={"file_id": "VID1", "file_size": 99999, "mime_type": "video/mp4"},
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.attachments[0].kind == "video"


def test_audio_message():
    payload = _base_message(
        audio={"file_id": "A1", "file_size": 1024, "file_name": "song.mp3"},
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.attachments[0].kind == "audio"
    assert result.attachments[0].file_name == "song.mp3"


def test_media_group_id_passthrough():
    payload = _base_message(
        document={"file_id": "X1"},
        media_group_id="group-abc",
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.media_group_id == "group-abc"


def test_text_with_attachment_carries_both():
    payload = _base_message(
        text="here is a doc",
        document={"file_id": "Y1"},
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.text == "here is a doc"
    assert result.attachments[0].file_id == "Y1"


def test_empty_message_returns_none():
    payload = _base_message()
    assert normalize_update(payload) is None


def test_caption_only_whitespace_is_treated_as_none():
    payload = _base_message(
        document={"file_id": "Z1"},
        caption="   ",
    )
    result = normalize_update(payload)
    assert result is not None
    assert result.caption is None


def test_invalid_payload_still_raises():
    with pytest.raises(TelegramUpdateValidationError):
        normalize_update({})


def test_document_with_invalid_fields_drops_attachment():
    payload = _base_message(document={"file_id": ""})
    assert normalize_update(payload) is None


def test_simple_media_with_invalid_file_id_is_dropped():
    payload = _base_message(audio={"file_id": 123})
    assert normalize_update(payload) is None


def test_attachment_with_non_string_optional_fields_normalized():
    payload = _base_message(
        document={
            "file_id": "OK",
            "file_size": "not-int",
            "mime_type": None,
            "file_name": 42,
        },
    )
    result = normalize_update(payload)
    assert result is not None
    attachment = result.attachments[0]
    assert attachment.file_size is None
    assert attachment.mime_type is None
    assert attachment.file_name is None
