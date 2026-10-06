# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Schema v14 on its own: the fragment pairs, ``incomplete`` and ``apply_v14``."""

import sqlite3

import pytest

from noust.core import schema_v14

AREAS = (
    "TEAMS",
    "BUILD",
    "CONTAINER",
    "RECIPES",
    "DELIVERY",
    "SOURCES",
    "DATABASES",
    "DBINSTANCES",
    "BACKUPS",
    "ACCESS",
    "NOTIFY",
)


@pytest.fixture(autouse=True)
def mechanism_only(monkeypatch):
    """
    Empty every area's fragment: these tests are about the mechanism.

    What a lane writes in its own fragment is tested by that lane, against the real
    fragment (the console's exposure, ``tests/test_console_exposure_store.py``), so a
    lane filling its pair cannot break the tests of the helpers that gather them.
    """
    for area in AREAS:
        monkeypatch.setattr(schema_v14, f"{area}_SQL", "")
        monkeypatch.setattr(schema_v14, f"{area}_COLUMNS", [])


def _run_script(cursor: sqlite3.Cursor, script: str) -> None:
    """Run a fragment the way the store's runner does, statement by statement."""
    for statement in script.split(";"):
        if statement.strip():
            cursor.execute(statement)


@pytest.fixture(autouse=True)
def blank_areas(monkeypatch):
    """
    Start from empty fragments: these tests are about the mechanism, not about
    what each lane declared. Each lane tests its own fragment against a real store.
    """
    for area in AREAS:
        monkeypatch.setattr(schema_v14, f"{area}_SQL", "")
        monkeypatch.setattr(schema_v14, f"{area}_COLUMNS", [])


@pytest.fixture
def cursor():
    """A cursor on an in-memory database holding the one older table v14 may alter."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE apps (id INTEGER PRIMARY KEY, domain TEXT)")
    yield cur
    conn.close()


@pytest.fixture(autouse=True)
def unfilled_areas(monkeypatch):
    """
    Every area as the foundation left it: empty.

    These tests are about the fragment machinery, not about what any lane put in
    its own fragment (each lane's tests cover that), so they run over empty ones.
    """
    for area in AREAS:
        monkeypatch.setattr(schema_v14, f"{area}_SQL", "")
        monkeypatch.setattr(schema_v14, f"{area}_COLUMNS", [])


def _declare(monkeypatch, area: str = "TEAMS") -> None:
    # Only the probe is under test: the lanes' own fragments are not.
    for other in AREAS:
        monkeypatch.setattr(schema_v14, f"{other}_SQL", "")
        monkeypatch.setattr(schema_v14, f"{other}_COLUMNS", [])
    monkeypatch.setattr(
        schema_v14,
        f"{area}_SQL",
        """
        CREATE TABLE IF NOT EXISTS unit_probe (id INTEGER PRIMARY KEY, name TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unit_probe_name ON unit_probe(name);
        """,
    )
    monkeypatch.setattr(schema_v14, f"{area}_COLUMNS", [("apps", "unit_probe_column", "TEXT")])


def test_the_version_is_14():
    assert schema_v14.VERSION == 14


@pytest.mark.parametrize("area", AREAS)
def test_every_area_has_one_fragment_and_one_column_list(area):
    assert isinstance(getattr(schema_v14, f"{area}_SQL"), str)
    columns = getattr(schema_v14, f"{area}_COLUMNS")
    assert isinstance(columns, list)
    assert all(len(column) == 3 for column in columns)


def test_the_helpers_gather_every_area_once():
    assert len(schema_v14._fragments()) == len(AREAS)
    assert schema_v14._columns() == [
        column for area in AREAS for column in getattr(schema_v14, f"{area}_COLUMNS")
    ]


def test_empty_fragments_leave_a_store_complete_and_change_nothing(cursor, monkeypatch):
    # The lanes fill their fragments; this is about what an empty one does.
    for area in AREAS:
        monkeypatch.setattr(schema_v14, f"{area}_SQL", "")
        monkeypatch.setattr(schema_v14, f"{area}_COLUMNS", [])
    assert schema_v14.incomplete(cursor) is False

    schema_v14.apply_v14(cursor, _run_script)

    cursor.execute("SELECT name FROM sqlite_master")
    assert {row[0] for row in cursor.fetchall()} == {"apps"}


def test_incomplete_reports_a_missing_table_index_or_column(cursor, monkeypatch):
    _declare(monkeypatch)
    assert schema_v14.incomplete(cursor) is True

    # The table and the column alone are not enough while the index is missing.
    cursor.execute("CREATE TABLE unit_probe (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute("ALTER TABLE apps ADD COLUMN unit_probe_column TEXT")
    assert schema_v14.incomplete(cursor) is True

    cursor.execute("CREATE UNIQUE INDEX idx_unit_probe_name ON unit_probe(name)")
    assert schema_v14.incomplete(cursor) is False


def test_apply_creates_what_is_missing_and_is_idempotent(cursor, monkeypatch):
    _declare(monkeypatch, "NOTIFY")

    schema_v14.apply_v14(cursor, _run_script)
    schema_v14.apply_v14(cursor, _run_script)

    assert schema_v14.incomplete(cursor) is False
    assert "unit_probe_column" in schema_v14._existing_columns(cursor, "apps")
    assert schema_v14._existing_columns(cursor, "unit_probe") == {"id", "name"}


def test_apply_adds_only_the_columns_that_are_missing(cursor, monkeypatch):
    cursor.execute("ALTER TABLE apps ADD COLUMN unit_probe_column TEXT")
    cursor.execute("INSERT INTO apps (domain, unit_probe_column) VALUES ('a.example.com', 'kept')")
    _declare(monkeypatch)

    schema_v14.apply_v14(cursor, _run_script)

    cursor.execute("SELECT unit_probe_column FROM apps")
    assert cursor.fetchall() == [("kept",)]
