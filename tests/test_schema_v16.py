# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v16: the tables and columns 4.1 adds, one fragment per area.

A store climbs from v15 to v16, a fresh store is born at v16, and a store a development build of
4.1 stamped v16 before an area wrote its fragment gets that fragment the next time it opens: lanes
add tables during the release, and a server that ran one of its builds must not keep a v16 store
without them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v16
from noust.core.store import SCHEMA_VERSION, NoustStore

LATE_TABLE = "CREATE TABLE IF NOT EXISTS v16_probe (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"


@pytest.fixture
def fresh() -> Iterator[None]:
    NoustStore.reset_instance()
    yield None
    NoustStore.reset_instance()


def version_of(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    finally:
        conn.close()


def tables_of(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def test_the_version_follows_v15() -> None:
    assert schema_v16.VERSION == 16
    assert SCHEMA_VERSION == schema_v16.VERSION


def test_a_fresh_store_is_born_at_v16(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"

    NoustStore(path)

    assert version_of(path) == 16


def test_a_store_at_v15_climbs_to_v16(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (15)")
    conn.commit()
    conn.close()

    NoustStore(path)

    assert version_of(path) == 16


def test_a_fragment_written_after_a_store_was_stamped_v16_is_added_when_it_opens(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    assert "v16_probe" not in tables_of(path)

    monkeypatch.setattr(schema_v16, "ROLES_SQL", LATE_TABLE)
    NoustStore(path)

    assert "v16_probe" in tables_of(path)
    assert version_of(path) == 16


def test_a_column_written_later_is_added_and_a_complete_store_is_left_alone(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    monkeypatch.setattr(schema_v16, "FLEET_COLUMNS", [("apps", "v16_probe_column", "TEXT")])

    NoustStore(path)
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    try:
        assert not schema_v16.incomplete(conn.cursor())
        columns = {row[1] for row in conn.execute("PRAGMA table_info(apps)").fetchall()}
    finally:
        conn.close()
    assert "v16_probe_column" in columns


def test_a_store_that_climbs_from_v15_has_the_tables_of_v16_the_first_time_it_opens(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (15)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(schema_v16, "ROLES_SQL", LATE_TABLE)

    NoustStore(path)

    # The climb itself adds them: waiting for the next opening leaves a process that has just
    # upgraded a server running on a store without the tables its own code reads.
    assert "v16_probe" in tables_of(path)
    assert version_of(path) == 16
