"""Stdlib logging setup for semantaix services.

Idempotent: safe to call from every service. Honors the LOG_LEVEL env var
first, then `AppSettings.log_level`, then falls back to INFO. Writes to
stdout so Docker's json-file driver captures it.

Two formatters supported (selected via LOG_FORMAT env or AppSettings):
- "text" (default): human-readable single-line output with `trace_id` only.
- "json": one JSON object per line, including every `extra={...}` field
  attached to the LogRecord. Used by agentic log readers that need to
  reconstruct the full causal chain for a single trace_id.

No third-party dependencies. Pure stdlib JSON.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Final

from platform_common.settings import get_settings

_CONFIGURED: bool = False

_TEXT_FORMAT: Final[str] = (
    "%(asctime)s %(levelname)-7s %(name)s "
    "trace=%(trace_id)s %(message)s"
)

# Attributes injected by the logging module itself on every LogRecord.
# Anything NOT in this set on a LogRecord came from an explicit extra={...}
# and should be included in JSON output.
_STDLIB_LOGRECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "trace_id",
    }
)


class _TraceIdDefaulter(logging.Filter):
    """Ensure %(trace_id)s always resolves, even when callers omit it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        return True


class _JsonExtrasFormatter(logging.Formatter):
    """One JSON object per line. Includes every `extra={...}` attribute.

    Fields always present: ts, level, logger, trace_id, event. Plus every
    non-stdlib attribute attached to the LogRecord. Values that are not
    JSON-serializable are coerced via repr().
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STDLIB_LOGRECORD_ATTRS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=repr)


def _resolve_format() -> str:
    raw = os.environ.get("LOG_FORMAT") or get_settings().log_format or "text"
    normalized = raw.strip().lower()
    return "json" if normalized == "json" else "text"


def configure_logging(service_name: str) -> None:
    """Configure root logging exactly once per process.

    Level resolution: LOG_LEVEL env, then settings.log_level, then INFO.
    Format resolution: LOG_FORMAT env (text|json), then settings.log_format,
    then text.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    raw = os.environ.get("LOG_LEVEL") or get_settings().log_level or "INFO"
    level = logging.getLevelName(raw.upper())
    if not isinstance(level, int):
        level = logging.INFO

    handler = logging.StreamHandler(stream=sys.stdout)
    if _resolve_format() == "json":
        handler.setFormatter(_JsonExtrasFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))
    handler.addFilter(_TraceIdDefaulter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "service_starting",
        extra={"service": service_name, "level": logging.getLevelName(level)},
    )


def reset_for_tests() -> None:
    """Reset the one-shot guard so pytest can re-invoke configure_logging."""
    global _CONFIGURED
    _CONFIGURED = False
