"""Tests for the stdlib-based logging configurator."""

from __future__ import annotations

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
