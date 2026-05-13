from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pypdf
import pytest
from docx import Document
from PIL import Image
from pptx import Presentation
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from services.api.app.operator_uploads import extractors


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing the given Russian text."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    page = writer.pages[0]
    stream = DecodedStreamObject()
    content = (
        "BT /F1 12 Tf 72 770 Td (" + text.replace("(", "\\(").replace(")", "\\)") + ") Tj ET"
    )
    stream.set_data(content.encode("latin-1", errors="replace"))
    page[NameObject("/Contents")] = stream
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/MediaBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(595), NumberObject(842)]
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("Расписание работы офиса")
    document.add_paragraph("Понедельник — пятница: с 9:00 до 18:00")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Город"
    table.cell(0, 1).text = "Москва"
    table.cell(1, 0).text = "Телефон"
    table.cell(1, 1).text = "+7-495-000-00-00"
    document.save(str(path))


def _write_pptx(path: Path) -> None:
    presentation = Presentation()
    slide_layout = presentation.slide_layouts[5]
    slide = presentation.slides.add_slide(slide_layout)
    slide.shapes.title.text = "Презентация для клиентов"
    textbox = slide.shapes.add_textbox(left=914400, top=914400, width=4572000, height=1828800)
    textbox.text_frame.text = "Услуги доступны 24/7"
    notes_frame = slide.notes_slide.notes_text_frame
    notes_frame.text = "Не забыть упомянуть скидку"
    presentation.save(str(path))


def _write_png(path: Path) -> None:
    image = Image.new("RGB", (32, 32), color=(255, 255, 255))
    image.save(str(path), format="PNG")


def test_extract_pdf_returns_text(tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(_make_pdf("Hello PDF"))
    result = extractors.extract_pdf(pdf_path)
    assert "Hello PDF" in result


def test_extract_docx_returns_paragraphs_and_table_cells(tmp_path: Path):
    docx_path = tmp_path / "sample.docx"
    _write_docx(docx_path)
    result = extractors.extract_docx(docx_path)
    assert "Расписание работы офиса" in result
    assert "Понедельник" in result
    assert "Москва" in result
    assert "+7-495-000-00-00" in result


def test_extract_pptx_returns_slide_text_and_notes(tmp_path: Path):
    pptx_path = tmp_path / "sample.pptx"
    _write_pptx(pptx_path)
    result = extractors.extract_pptx(pptx_path)
    assert "Презентация для клиентов" in result
    assert "Услуги доступны 24/7" in result
    assert "Не забыть упомянуть скидку" in result


def test_extract_txt_decodes_utf8(tmp_path: Path):
    txt_path = tmp_path / "sample.txt"
    txt_path.write_text("Это тестовый файл", encoding="utf-8")
    result = extractors.extract_txt(txt_path)
    assert result == "Это тестовый файл"


def test_extract_txt_replaces_invalid_bytes(tmp_path: Path):
    txt_path = tmp_path / "broken.txt"
    txt_path.write_bytes(b"\xff\xfe" + "абв".encode("utf-8"))
    result = extractors.extract_txt(txt_path)
    assert "абв" in result


def test_extract_image_uses_pytesseract(monkeypatch, tmp_path: Path):
    png_path = tmp_path / "sample.png"
    _write_png(png_path)
    called_with: dict = {}

    def fake_ocr(image, lang):
        called_with["lang"] = lang
        return "Распознанный текст"

    monkeypatch.setattr(extractors.pytesseract, "image_to_string", fake_ocr)
    result = extractors.extract_image(png_path)
    assert "Распознанный текст" in result
    assert called_with["lang"] == "rus+eng"


def test_soft_wrap_preserves_short_sentences():
    text = "Короткое предложение. Второе короткое."
    result = extractors.soft_wrap(text, max_chars=100)
    assert result == "Короткое предложение.\nВторое короткое."


def test_soft_wrap_wraps_long_sentence_at_whitespace():
    sentence = "слово " * 60
    result = extractors.soft_wrap(sentence.strip(), max_chars=50)
    assert all(len(line) <= 50 for line in result.splitlines())
    assert "слово" in result


def test_soft_wrap_hard_cuts_when_no_whitespace():
    long_token = "x" * 250
    result = extractors.soft_wrap(long_token, max_chars=100)
    lines = result.splitlines()
    assert all(len(line) <= 100 for line in lines)
    assert "".join(lines) == long_token


def test_extract_dispatch_routes_to_extractor(tmp_path: Path):
    txt_path = tmp_path / "x.txt"
    txt_path.write_text("hello", encoding="utf-8")
    assert extractors.extract("txt", txt_path).strip() == "hello"


def test_extract_dispatch_raises_on_unknown_type(tmp_path: Path):
    with pytest.raises(extractors.ExtractionError) as exc:
        extractors.extract("bogus", tmp_path / "anything")
    assert "unsupported_file_type" in exc.value.reason


def test_extract_dispatch_raises_on_empty_text(tmp_path: Path):
    txt_path = tmp_path / "empty.txt"
    txt_path.write_text("   \n   \n", encoding="utf-8")
    with pytest.raises(extractors.ExtractionError) as exc:
        extractors.extract("txt", txt_path)
    assert exc.value.reason == "empty_text"


class FakeTranscriber:
    def __init__(self, return_text: str = "Расшифровка"):
        self.calls: list[tuple[Path, str]] = []
        self._return_text = return_text

    def transcribe(self, audio_path: Path, *, language: str) -> str:
        self.calls.append((audio_path, language))
        return self._return_text


@pytest.mark.asyncio
async def test_extract_audio_happy_path(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"opus-bytes")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 12.5)
    transcriber = FakeTranscriber("Привет это тестовое сообщение")
    result = await extractors.extract_audio(audio, transcriber=transcriber, max_seconds=60)
    assert "Привет" in result
    assert transcriber.calls[0][1] == "ru"


@pytest.mark.asyncio
async def test_extract_audio_too_long_skips_transcription(monkeypatch, tmp_path: Path):
    audio = tmp_path / "long.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 9999.0)
    transcriber = FakeTranscriber()
    with pytest.raises(extractors.ExtractionError) as exc:
        await extractors.extract_audio(audio, transcriber=transcriber, max_seconds=60)
    assert exc.value.reason == "audio_too_long"
    assert transcriber.calls == []


@pytest.mark.asyncio
async def test_extract_audio_empty_transcript_raises(monkeypatch, tmp_path: Path):
    audio = tmp_path / "silence.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 1.0)
    transcriber = FakeTranscriber(return_text="   ")
    with pytest.raises(extractors.ExtractionError) as exc:
        await extractors.extract_audio(audio, transcriber=transcriber, max_seconds=60)
    assert exc.value.reason == "empty_text"


@pytest.mark.asyncio
async def test_extract_audio_uses_settings_default_cap(monkeypatch, tmp_path: Path):
    audio = tmp_path / "v.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 5.0)
    transcriber = FakeTranscriber("ok")
    result = await extractors.extract_audio(audio, transcriber=transcriber)
    assert result == "ok"


@pytest.mark.asyncio
async def test_extract_video_runs_ffmpeg_then_audio(monkeypatch, tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 30.0)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"audio-data")
        return subprocess_completed(cmd)

    monkeypatch.setattr(extractors.subprocess, "run", fake_run)
    transcriber = FakeTranscriber("video transcript")
    result = await extractors.extract_video(video, transcriber=transcriber, max_seconds=120)
    assert "video transcript" in result
    assert captured_cmds[0][0] == "ffmpeg"


@pytest.mark.asyncio
async def test_extract_video_too_long_skips_ffmpeg(monkeypatch, tmp_path: Path):
    video = tmp_path / "long.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 99999.0)
    called = {"ran": False}

    def fake_run(*args, **kwargs):
        called["ran"] = True
        return subprocess_completed(args[0])

    monkeypatch.setattr(extractors.subprocess, "run", fake_run)
    with pytest.raises(extractors.ExtractionError) as exc:
        await extractors.extract_video(video, transcriber=FakeTranscriber(), max_seconds=60)
    assert exc.value.reason == "audio_too_long"
    assert called["ran"] is False


def subprocess_completed(cmd):
    import subprocess as _sp

    return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_probe_duration_parses_ffprobe_output(monkeypatch, tmp_path: Path):
    media = tmp_path / "m.mp3"
    media.write_bytes(b"x")

    def fake_run(cmd, **kwargs):
        return subprocess_completed_with_stdout(cmd, "42.5\n")

    monkeypatch.setattr(extractors.subprocess, "run", fake_run)
    assert extractors._probe_duration(media) == 42.5


def test_probe_duration_raises_on_empty_output(monkeypatch, tmp_path: Path):
    media = tmp_path / "m.mp3"
    media.write_bytes(b"x")
    monkeypatch.setattr(
        extractors.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess_completed_with_stdout(cmd, "   \n"),
    )
    with pytest.raises(extractors.ExtractionError) as exc:
        extractors._probe_duration(media)
    assert exc.value.reason == "ffprobe_no_duration"


def subprocess_completed_with_stdout(cmd, stdout):
    import subprocess as _sp

    return _sp.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")


@pytest.mark.asyncio
async def test_extract_media_dispatches_audio_and_video(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(extractors, "_probe_duration", lambda path: 5.0)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    async def stub_extract_video(path, *, transcriber, max_seconds=None):
        return "video!"

    monkeypatch.setattr(extractors, "extract_video", stub_extract_video)
    result_audio = await extractors.extract_media(
        "audio", audio, transcriber=FakeTranscriber("audio!")
    )
    result_video = await extractors.extract_media(
        "video", video, transcriber=FakeTranscriber("ignored")
    )
    assert result_audio == "audio!"
    assert result_video == "video!"


@pytest.mark.asyncio
async def test_extract_media_rejects_unknown_type(tmp_path: Path):
    with pytest.raises(extractors.ExtractionError) as exc:
        await extractors.extract_media(
            "pdf", tmp_path / "x", transcriber=FakeTranscriber()
        )
    assert "unsupported_media_type" in exc.value.reason


def test_binary_sha256_streams(tmp_path: Path):
    path = tmp_path / "blob.bin"
    contents = b"A" * 200_000
    path.write_bytes(contents)
    import hashlib

    expected = hashlib.sha256(contents).hexdigest()
    assert extractors.binary_sha256(path) == expected


def test_whisper_transcriber_uses_lazy_loaded_model(monkeypatch):
    transcriber = extractors.WhisperTranscriber()

    class FakeSegment:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeModel:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def transcribe(self, audio_path: str, *, language: str):
            self.calls.append((audio_path, language))
            return ([FakeSegment("hello"), FakeSegment("world")], None)

    fake_model = FakeModel()
    monkeypatch.setattr(transcriber, "_load", lambda: fake_model)
    result = transcriber.transcribe(Path("/tmp/x.mp3"), language="ru")
    assert result == "hello world"
    assert fake_model.calls[0] == ("/tmp/x.mp3", "ru")
    # Second call reuses the same model
    transcriber.transcribe(Path("/tmp/y.mp3"), language="ru")
    assert len(fake_model.calls) == 2
