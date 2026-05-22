"""Unit coverage for the calendar operator-vs-admin authorization helper
(Epic 11, story 11.08)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.api.app.calendar.authorization import (
    authorize_calendar_config,
    authorize_calendar_disconnect,
)
from services.api.app.calendar.settings_repository import CalendarProjectSettings


def _settings(operator: str | None) -> CalendarProjectSettings:
    return CalendarProjectSettings(
        project_id=1,
        enabled=True,
        calendar_operator=operator,
        project_timezone="Europe/Moscow",
        lookahead_days=60,
        updated_at=None,
    )


def test_admin_config_always_allowed():
    authorize_calendar_config(
        actor="@admin", actor_role="admin", project_settings=_settings("@op")
    )


def test_operator_config_allowed_when_no_settings():
    authorize_calendar_config(
        actor="@op", actor_role="operator", project_settings=None
    )


def test_operator_config_allowed_when_no_designated_operator():
    authorize_calendar_config(
        actor="@op", actor_role="operator", project_settings=_settings(None)
    )


def test_operator_config_allowed_when_matches_designated():
    authorize_calendar_config(
        actor="@op", actor_role="operator", project_settings=_settings("@op")
    )


def test_operator_config_rejected_when_not_designated():
    with pytest.raises(HTTPException) as exc:
        authorize_calendar_config(
            actor="@op2", actor_role="operator", project_settings=_settings("@op")
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "not_calendar_operator"


def test_config_unknown_role_rejected():
    with pytest.raises(HTTPException) as exc:
        authorize_calendar_config(
            actor="@op", actor_role="ghost", project_settings=None
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "unknown_actor_role"


def test_disconnect_allowed_for_operator():
    authorize_calendar_disconnect(actor_role="operator")


def test_disconnect_rejected_for_admin():
    with pytest.raises(HTTPException) as exc:
        authorize_calendar_disconnect(actor_role="admin")
    assert exc.value.status_code == 403
    assert exc.value.detail == "admin_cannot_disconnect"


def test_disconnect_unknown_role_rejected():
    with pytest.raises(HTTPException) as exc:
        authorize_calendar_disconnect(actor_role="ghost")
    assert exc.value.status_code == 403
    assert exc.value.detail == "unknown_actor_role"
