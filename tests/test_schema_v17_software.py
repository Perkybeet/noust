# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v17, the software lane's fragment: the ledger of what Noust installed (item 118).

The rules are those of ``tests/test_schema_v17.py``, which holds the climb and the completion of
a store a development build stamped early. Here is what only this fragment decides: the table, its
columns, and that nothing in it names an application or a database, so no team's scope reaches it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v17
from noust.core.store import NoustStore


@pytest.fixture
def fresh() -> Iterator[None]:
    NoustStore.reset_instance()
    yield None
    NoustStore.reset_instance()


def tables_of(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def columns_of(path: Path) -> dict[str, tuple[str, int, int]]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("PRAGMA table_info(installed_software)").fetchall()
    finally:
        conn.close()
    return {str(row[1]): (str(row[2]), int(row[3]), int(row[5])) for row in rows}


def drop_ledger(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE installed_software")
    conn.commit()
    conn.close()


def stamp(path: Path, version: int) -> None:
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    conn.close()


def test_a_fresh_store_has_the_ledger(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"

    NoustStore(path)

    # (type, not null, primary key)
    assert columns_of(path) == {
        "component": ("TEXT", 0, 1),
        "packages": ("TEXT", 1, 0),
        "repository_files": ("TEXT", 1, 0),
        "installed_at": ("TEXT", 1, 0),
        "actor": ("TEXT", 0, 0),
        "via": ("TEXT", 1, 0),
    }


def test_a_store_that_climbs_from_v16_has_it_after_the_climb(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 16)
    drop_ledger(path)
    assert "installed_software" not in tables_of(path)

    NoustStore(path)

    assert "installed_software" in tables_of(path)


def test_a_store_stamped_v17_before_the_ledger_existed_gets_it_when_it_opens(
    tmp_path: Path, fresh: None
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    drop_ledger(path)

    NoustStore(path)

    assert "installed_software" in tables_of(path)


def test_the_fragment_leaves_the_other_areas_pairs_alone() -> None:
    assert "account_dashboards" not in schema_v17.SOFTWARE_SQL
    assert "installed_software" not in schema_v17.HOME_SQL
    assert schema_v17.SOFTWARE_COLUMNS == []


def test_nothing_in_the_ledger_names_an_application_or_a_database() -> None:
    body = re.search(
        r"CREATE TABLE IF NOT EXISTS installed_software \((.*?)\n\);",
        schema_v17.SOFTWARE_SQL,
        re.S,
    )
    assert body is not None
    names = {
        line.split()[0]
        for line in body.group(1).splitlines()
        if line.startswith("    ") and not line.startswith("     ")
    }

    # A team's scope reaches a row by one of these (tests/test_scope_architecture.py); the
    # server's software has none, and its routes are the instance's.
    assert not names & {"app_id", "app_domain", "domain", "db_name", "database", "repository"}
