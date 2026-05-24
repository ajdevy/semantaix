"""Coverage for data/russian_calendar_terms.json (Epic 12, story 12.01).

No Python code reads this file yet (story 12.06 owns the renderer); this test
guards the shape so 12.06 can land cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "russian_calendar_terms.json"


def test_russian_calendar_terms_loads_and_has_expected_shape():
    raw = _DATA_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert set(data.keys()) >= {
        "weekday_short",
        "weekday_long",
        "month_genitive",
        "closed_prefix",
    }
    weekday_keys = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert set(data["weekday_short"].keys()) == weekday_keys
    assert set(data["weekday_long"].keys()) == weekday_keys
    month_keys = {str(i) for i in range(1, 13)}
    assert set(data["month_genitive"].keys()) == month_keys
    assert isinstance(data["closed_prefix"], str)
    # Spot-check Russian values to catch encoding regressions.
    assert data["weekday_short"]["mon"] == "пн"
    assert data["month_genitive"]["1"] == "января"
