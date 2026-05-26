"""Pure renderer for ``ProjectService`` rows → natural Russian prose (FR-25).

The catalog answer branch (``GroundedRagAnswerer``) feeds these rendered
strings to the LLM. The hard structural guarantee FR-25 relies on lives
here: **field labels never appear in the output**. The LLM cannot leak
``Название:`` / ``Цена:`` / ``Длительность:`` because they are not in the
input it sees — only the natural prose this module emits.

Stories: 13.06 (catalog cutover) + 13.01 (terms-data file).

Examples (per FR-25):
- Full row → ``"Маникюр — 60 минут, пн-сб 10:00-19:00, цена от 2000 ₽. Классический и аппаратный."``
- Catalog-only → ``"Маникюр — цена от 2000 ₽. Классический и аппаратный."``
- Name-only → ``"Маникюр."``
- Date exceptions → appended as ``"закрыто: 1 января, 9 мая"``.

All helpers are pure. The terms-data file is loaded once via the
module-level lazy cache (``get_terms_data``); tests can monkeypatch the
path or pass an explicit ``terms`` dict.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from services.api.app.calendar.project_services_repository import ProjectService

_DATA_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "russian_calendar_terms.json"
)

_WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@lru_cache(maxsize=4)
def _load_terms_cached(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_terms_data(*, path: str | None = None) -> dict:
    """Return the cached terms dict from ``data/russian_calendar_terms.json``.

    The cache is keyed on the path so tests can supply a fixture file
    without contaminating production lookups.
    """
    return _load_terms_cached(path or str(_DATA_PATH))


def load_russian_calendar_terms() -> dict:
    """Public alias for ``get_terms_data()`` used by callers outside this module."""
    return get_terms_data()


def _render_duration(duration_minutes: int | None) -> str | None:
    if duration_minutes is None:
        return None
    minutes = int(duration_minutes)
    if minutes <= 0:
        return None
    # Russian plural rules: 1 → минута; 2-4 → минуты; 5+ (and 0, 11-14) → минут.
    last_two = minutes % 100
    last = minutes % 10
    if 11 <= last_two <= 14:
        unit = "минут"
    elif last == 1:
        unit = "минута"
    elif 2 <= last <= 4:
        unit = "минуты"
    else:
        unit = "минут"
    return f"{minutes} {unit}"


def _render_days(
    service_days_json: list[str] | None, *, terms: dict
) -> str | None:
    if not service_days_json:
        return None
    short = terms.get("weekday_short", {})
    # Stable order along _WEEKDAY_ORDER; ignore unknown codes.
    ordered = [d for d in _WEEKDAY_ORDER if d in service_days_json]
    if not ordered:
        return None
    if len(ordered) == 7:
        return "ежедневно"
    # Consecutive-range collapse: if ordered is a single consecutive slice,
    # render as "пн-сб"; otherwise comma-list the short codes.
    first_idx = _WEEKDAY_ORDER.index(ordered[0])
    last_idx = _WEEKDAY_ORDER.index(ordered[-1])
    if last_idx - first_idx + 1 == len(ordered) and len(ordered) >= 2:
        return f"{short.get(ordered[0], ordered[0])}-{short.get(ordered[-1], ordered[-1])}"
    return ", ".join(short.get(code, code) for code in ordered)


def _render_window(window: list[str]) -> str | None:
    if not window or len(window) != 2:
        return None
    return f"{window[0]}-{window[1]}"


def _render_hours_for_day(windows: list[list[str]]) -> str | None:
    rendered = [r for r in (_render_window(w) for w in windows) if r]
    if not rendered:
        return None
    return ", ".join(rendered)


def _render_hours(
    working_hours_json: dict[str, list[list[str]]] | None, *, terms: dict
) -> str | None:
    """Render the most-representative day window list.

    v1 simplification: pick the first defined day's windows (in weekday
    order). If all defined days share an identical windows list, that's
    still the same string; mixed-window per-day rendering is out of scope
    for this pass (documented in story 13.06).
    """
    if not working_hours_json:
        return None
    # Preserve weekday order so the chosen window is deterministic.
    for code in _WEEKDAY_ORDER:
        if code in working_hours_json:
            return _render_hours_for_day(working_hours_json[code])
    return None


def _render_date_exceptions(
    date_exceptions_json: list[str] | None, *, terms: dict
) -> str | None:
    if not date_exceptions_json:
        return None
    month_map = terms.get("month_genitive", {})
    closed_prefix = terms.get("closed_prefix", "закрыто:")
    parts: list[str] = []
    for entry in date_exceptions_json:
        try:
            year_s, month_s, day_s = str(entry).split("-")
            month = int(month_s)
            day = int(day_s)
        except (ValueError, AttributeError):
            continue
        month_name = month_map.get(str(month))
        if not month_name:
            continue
        parts.append(f"{day} {month_name}")
    if not parts:
        return None
    return f"{closed_prefix} {', '.join(parts)}"


def _render_price(price_text: str | None) -> str | None:
    if price_text is None:
        return None
    stripped = price_text.strip()
    if not stripped:
        return None
    return f"цена {stripped}"


def render_project_service_prose(
    service: ProjectService, *, terms: dict
) -> str:
    """Render a single service row as natural Russian prose, NO field labels.

    Format (per FR-25): ``"<Name> — <duration>, <days> <hours>, <price>. <description>"``
    where each segment is omitted when its source field is empty.
    A name-only row collapses to ``"<Name>."``.
    """
    name = (service.name or "").strip()
    if not name:
        return ""

    # Segment 1: "<name> — <facts>" where facts is duration / days+hours / price /
    # closed-dates joined by ", ". The description is a separate sentence.
    facts: list[str] = []
    duration = _render_duration(service.duration_minutes)
    if duration:
        facts.append(duration)

    days_rendered = _render_days(service.service_days, terms=terms)
    hours_rendered = _render_hours(service.working_hours, terms=terms)
    if days_rendered and hours_rendered:
        facts.append(f"{days_rendered} {hours_rendered}")
    elif days_rendered:
        facts.append(days_rendered)
    elif hours_rendered:
        facts.append(hours_rendered)

    price = _render_price(service.price_text)
    if price:
        facts.append(price)

    closed = _render_date_exceptions(service.date_exceptions, terms=terms)
    if closed:
        facts.append(closed)

    head = name
    if facts:
        head = f"{name} — {', '.join(facts)}"

    description = (service.description or "").strip()
    sentence_one = head + "."
    if description:
        body = description if description.endswith(".") else description + "."
        return f"{sentence_one} {body}"
    return sentence_one


def render_project_services_block(
    services: list[ProjectService], *, terms: dict
) -> str:
    """Render a bullet block (one row per line) for the merged catalog chunk."""
    lines: list[str] = []
    for service in services:
        prose = render_project_service_prose(service, terms=terms)
        if prose:
            lines.append(f"• {prose}")
    return "\n".join(lines)


def reset_terms_cache_for_tests() -> None:
    """Test hook: clear the file cache so a monkeypatched path is re-read."""
    _load_terms_cached.cache_clear()


__all__: tuple[str, ...] = (
    "get_terms_data",
    "load_russian_calendar_terms",
    "render_project_service_prose",
    "render_project_services_block",
    "reset_terms_cache_for_tests",
)
