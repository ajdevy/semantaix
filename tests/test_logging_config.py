"""Tests for the stdlib-based logging configurator."""

from __future__ import annotations

import io
import json
import logging

import pytest

from platform_common import logging_config


@pytest.fixture(autouse=True)
def _reset_logging():
    """Each test resets the one-shot guard and root handlers."""
    logging_config.reset_for_tests()
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    logging_config.reset_for_tests()


def test_configure_logging_honors_log_level_env_var(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logging_config.configure_logging("test_service")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_falls_back_to_info_for_unknown_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "garbage")
    logging_config.configure_logging("test_service")
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging("test_service")
    handlers_after_first = list(logging.getLogger().handlers)
    logging_config.configure_logging("test_service")
    assert logging.getLogger().handlers == handlers_after_first


def test_reset_for_tests_clears_the_guard(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging("test_service")
    logging_config.reset_for_tests()
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logging_config.configure_logging("test_service")
    assert logging.getLogger().level == logging.WARNING


def test_trace_id_defaulter_supplies_dash_when_omitted():
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="x", lineno=1,
        msg="hello", args=None, exc_info=None,
    )
    assert not hasattr(record, "trace_id")
    flt = logging_config._TraceIdDefaulter()
    assert flt.filter(record) is True
    assert record.trace_id == "-"


def _format_one(formatter: logging.Formatter, **extras: object) -> dict:
    record = logging.LogRecord(
        name="svc.module", level=logging.INFO,
        pathname="x", lineno=1, msg="event_name", args=None, exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    if not hasattr(record, "trace_id"):
        record.trace_id = "-"
    rendered = formatter.format(record)
    return json.loads(rendered)


def test_json_formatter_emits_required_fields():
    payload = _format_one(
        logging_config._JsonExtrasFormatter(),
        trace_id="t-1",
        custom_field="hello",
        count=3,
    )
    assert payload["event"] == "event_name"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "svc.module"
    assert payload["trace_id"] == "t-1"
    assert payload["custom_field"] == "hello"
    assert payload["count"] == 3
    assert "ts" in payload


def test_json_formatter_omits_stdlib_attrs_but_includes_extras():
    payload = _format_one(
        logging_config._JsonExtrasFormatter(),
        trace_id="t-2",
        chunk_source_ids=["kb-1", "kb-2"],
        score=0.42,
    )
    assert payload["chunk_source_ids"] == ["kb-1", "kb-2"]
    assert payload["score"] == 0.42
    for forbidden in ("pathname", "lineno", "msg", "args", "process"):
        assert forbidden not in payload


def test_json_formatter_coerces_unserializable_via_repr():
    class _NotJson:
        def __repr__(self) -> str:
            return "<NotJson>"

    payload = _format_one(
        logging_config._JsonExtrasFormatter(), trace_id="t-3", weird=_NotJson()
    )
    assert payload["weird"] == "<NotJson>"


def test_json_formatter_renders_exc_info():
    formatter = logging_config._JsonExtrasFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname="x", lineno=1,
            msg="had_error", args=None, exc_info=sys.exc_info(),
        )
        record.trace_id = "t-4"
    payload = json.loads(formatter.format(record))
    assert "exc" in payload
    assert "RuntimeError" in payload["exc"]


def test_configure_logging_text_format_default(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    logging_config.configure_logging("test_service")
    formatter = logging.getLogger().handlers[0].formatter
    assert not isinstance(formatter, logging_config._JsonExtrasFormatter)


def test_configure_logging_json_format_when_env_set(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_config.configure_logging("test_service")
    formatter = logging.getLogger().handlers[0].formatter
    assert isinstance(formatter, logging_config._JsonExtrasFormatter)


def test_configure_logging_json_format_emits_one_line_per_event(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_config.configure_logging("test_service")
    handler = logging.getLogger().handlers[0]
    buffer = io.StringIO()
    handler.stream = buffer
    logger = logging.getLogger("test.event")
    logger.info("structured_event", extra={"trace_id": "t-5", "k": 1})
    output = buffer.getvalue().strip().splitlines()
    assert output, "no output"
    payload = json.loads(output[-1])
    assert payload["event"] == "structured_event"
    assert payload["trace_id"] == "t-5"
    assert payload["k"] == 1


def test_configure_logging_unknown_log_format_falls_back_to_text(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "garbage")
    logging_config.configure_logging("test_service")
    formatter = logging.getLogger().handlers[0].formatter
    assert not isinstance(formatter, logging_config._JsonExtrasFormatter)
