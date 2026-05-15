"""Stdlib logging setup for semantaix services.

Idempotent: safe to call from every service. Honors the LOG_LEVEL env var
first, then `AppSettings.log_level`, then falls back to INFO. Writes to
stdout so Docker's json-file driver captures it.

No third-party dependencies. The formatter includes a `trace_id` field when
callers pass `extra={"trace_id": ...}` (which bot_gateway already does
throughout) and falls back to "-" when absent so the line layout is stable.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

from platform_common.settings import get_settings

_CONFIGURED: bool = False

_FORMAT: Final[str] = (
    "%(asctime)s %(levelname)-7s %(name)s "
    "trace=%(trace_id)s %(message)s"
)


class _TraceIdDefaulter(logging.Filter):
    """Ensure %(trace_id)s always resolves, even when callers omit it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        return True


def configure_logging(service_name: str) -> None:
    """Configure root logging exactly once per process.

    Resolution order for level: LOG_LEVEL env var, then `settings.log_level`,
    then INFO. Unknown values fall back to INFO.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    raw = os.environ.get("LOG_LEVEL") or get_settings().log_level or "INFO"
    level = logging.getLevelName(raw.upper())
    if not isinstance(level, int):
        level = logging.INFO

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
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
        "service_starting service=%s level=%s",
        service_name,
        logging.getLevelName(level),
    )


def reset_for_tests() -> None:
    """Reset the one-shot guard so pytest can re-invoke configure_logging."""
    global _CONFIGURED
    _CONFIGURED = False
