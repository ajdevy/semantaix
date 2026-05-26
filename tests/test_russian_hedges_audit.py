"""Story 13.06 — hedges file must not block legitimate price/duration phrasing.

FR-25 catalog answers include strings like "от 2000 ₽" and "60 минут".
The hedge guardrail does substring matching against normalized output,
so any entry like "от 2000" or "от 60" in ``data/russian_hedges.txt``
would silently kill every catalog reply.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_HEDGES_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "russian_hedges.txt"
)


_FORBIDDEN_HEDGES = (
    "от 2000 ₽",
    "от 60 минут",
    "от 2000",
    "от 60",
)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_HEDGES)
def test_hedges_do_not_block_catalog_phrasings(forbidden: str) -> None:
    text = _HEDGES_PATH.read_text(encoding="utf-8")
    entries = {
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert forbidden.lower() not in entries, (
        f"hedge entry {forbidden!r} would block legitimate catalog phrasings"
    )
