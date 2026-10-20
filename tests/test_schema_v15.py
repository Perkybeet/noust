# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v15: what the store holds that carried the old product's name.

The only thing is the folder a backup destination sends to when nobody named one. The step is
idempotent, so a store whose climb was interrupted (or that two processes opened at once) ends
the same, and it keeps the old folder next to the new one so what is already there stays listed
until its retention has removed it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from noust.core import legacy_names, schema_v15
from noust.core.store import SCHEMA_VERSION, NoustStore


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE backup_destinations (name TEXT PRIMARY KEY, backend TEXT NOT NULL, "
        "settings TEXT NOT NULL DEFAULT '{}', encrypted INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
    )
    return conn


def add(conn: sqlite3.Connection, name: str, settings: dict[str, str]) -> None:
    conn.execute(
        "INSERT INTO backup_destinations (name, backend, settings) VALUES (?, 's3', ?)",
        (name, json.dumps(settings)),
    )


def settings_of(conn: sqlite3.Connection, name: str) -> dict[str, str]:
    row = conn.execute(
        "SELECT settings FROM backup_destinations WHERE name = ?", (name,)
    ).fetchone()
    return json.loads(row["settings"])


def test_the_version_follows_v14() -> None:
    assert schema_v15.VERSION == 15
    assert SCHEMA_VERSION >= 15


def test_a_destination_on_the_default_folder_moves_to_the_new_one_and_remembers_the_old() -> None:
    conn = database()
    add(conn, "offsite", {"path": legacy_names.BACKUP_FOLDER, "region": "eu"})

    schema_v15.apply_v15(conn.cursor())

    assert settings_of(conn, "offsite") == {
        "path": "noust-backups",
        "legacy_path": legacy_names.BACKUP_FOLDER,
        "region": "eu",
    }


def test_a_folder_the_operator_chose_is_left_alone() -> None:
    conn = database()
    add(conn, "mine", {"path": "client-backups/server-1"})

    schema_v15.apply_v15(conn.cursor())

    assert settings_of(conn, "mine") == {"path": "client-backups/server-1"}


def test_running_it_twice_changes_nothing_more() -> None:
    conn = database()
    add(conn, "offsite", {"path": legacy_names.BACKUP_FOLDER})
    schema_v15.apply_v15(conn.cursor())
    once = settings_of(conn, "offsite")

    schema_v15.apply_v15(conn.cursor())

    assert settings_of(conn, "offsite") == once


def test_a_destination_whose_settings_cannot_be_read_is_left_as_it_is() -> None:
    conn = database()
    conn.execute(
        "INSERT INTO backup_destinations (name, backend, settings) VALUES ('odd', 's3', 'not json')"
    )

    schema_v15.apply_v15(conn.cursor())

    row = conn.execute("SELECT settings FROM backup_destinations WHERE name = 'odd'").fetchone()
    assert row["settings"] == "not json"


def test_a_store_without_the_table_has_nothing_to_do() -> None:
    conn = sqlite3.connect(":memory:")

    schema_v15.apply_v15(conn.cursor())


@pytest.fixture
def fresh() -> object:
    NoustStore.reset_instance()
    yield None
    NoustStore.reset_instance()


def test_a_store_at_v14_climbs_and_keeps_the_destination_usable(
    tmp_path: Path, fresh: object
) -> None:
    path = tmp_path / "noust.db"
    store = NoustStore(path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (14)")
    conn.execute(
        "INSERT INTO backup_destinations (name, backend, settings, created_at, updated_at) "
        "VALUES ('offsite', 's3', ?, 'now', 'now')",
        (json.dumps({"path": legacy_names.BACKUP_FOLDER}),),
    )
    conn.commit()
    conn.close()
    del store
    NoustStore.reset_instance()

    reopened = NoustStore(path)

    record = reopened.get_backup_destination("offsite")
    assert record is not None
    assert record.settings["path"] == "noust-backups"
    assert record.settings["legacy_path"] == legacy_names.BACKUP_FOLDER
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()
