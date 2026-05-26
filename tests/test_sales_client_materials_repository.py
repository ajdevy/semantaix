"""Unit tests for ``ClientMaterialsRepository`` (Story 12.05b minimal surface).

The full repository per Story 12.01 ships ``list_active``, ``get``,
``pick_by_tags``, ``update_telegram_file_id``, ``soft_delete``. 12.05b only
needs ``add(...)`` — that is the surface this module exercises. The schema
matches Story 12.01 so the table is forward-compatible when 12.01 lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.api.app.sales.client_materials_repository import (
    ClientMaterialsRepository,
    init_schema,
)

_NOW = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> ClientMaterialsRepository:
    return ClientMaterialsRepository(db_path=str(tmp_path / "sales.sqlite3"))


def test_add_returns_autoincrement_id(repo: ClientMaterialsRepository) -> None:
    first = repo.add(
        project_id=1,
        kind="pdf",
        local_path="/data/sales/x.pdf",
        byte_size=2048,
        now=_NOW,
    )
    second = repo.add(
        project_id=1,
        kind="photo",
        local_path="/data/sales/y.jpg",
        byte_size=4096,
        now=_NOW,
    )
    assert first == 1
    assert second == 2


def test_add_persists_all_optional_fields(
    repo: ClientMaterialsRepository, tmp_path: Path
) -> None:
    material_id = repo.add(
        project_id=7,
        kind="pdf",
        local_path="/data/sales/catalog.pdf",
        byte_size=12345,
        caption="Каталог туров",
        tags=["catalog", "tour"],
        source_operator_file_id="ABCDEFGH",
        telegram_file_id=None,
        now=_NOW,
    )
    # Read back via raw sqlite to verify columns persisted exactly.
    import sqlite3

    with sqlite3.connect(str(tmp_path / "sales.sqlite3")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM client_materials WHERE id = ?", (material_id,)
        ).fetchone()
    assert row is not None
    assert int(row["project_id"]) == 7
    assert str(row["kind"]) == "pdf"
    assert str(row["local_path"]) == "/data/sales/catalog.pdf"
    assert int(row["byte_size"]) == 12345
    assert str(row["caption"]) == "Каталог туров"
    assert str(row["source_operator_file_id"]) == "ABCDEFGH"
    assert row["telegram_file_id"] is None
    assert int(row["is_active"]) == 1
    # tags stored as JSON.
    import json

    assert json.loads(row["tags_json"]) == ["catalog", "tour"]
    assert str(row["created_at"]) == _NOW.isoformat()
    assert str(row["updated_at"]) == _NOW.isoformat()


def test_add_defaults_optional_fields_to_null_and_empty(
    repo: ClientMaterialsRepository, tmp_path: Path
) -> None:
    material_id = repo.add(
        project_id=3,
        kind="document",
        local_path="/data/sales/x.bin",
        byte_size=1,
        now=_NOW,
    )
    import sqlite3

    with sqlite3.connect(str(tmp_path / "sales.sqlite3")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM client_materials WHERE id = ?", (material_id,)
        ).fetchone()
    assert row["caption"] is None
    assert row["telegram_file_id"] is None
    assert row["source_operator_file_id"] is None
    assert row["duration_seconds"] is None
    import json

    assert json.loads(row["tags_json"]) == []


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "sales.sqlite3")
    init_schema(db_path)
    init_schema(db_path)
    # The second call must not raise. Verify the table still exists.
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "client_materials" in names


def test_add_rejects_naive_datetime(repo: ClientMaterialsRepository) -> None:
    naive = datetime(2026, 5, 26, 10, 0)
    with pytest.raises(ValueError):
        repo.add(
            project_id=1,
            kind="pdf",
            local_path="/data/sales/x.pdf",
            byte_size=10,
            now=naive,
        )
