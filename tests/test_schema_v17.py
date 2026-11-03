# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v17: the tables and columns 4.2 adds, one fragment per area.

The rules are those of v16 (``tests/test_schema_v16.py``): a store climbs from v16 to v17 in the
opening that finds it, a fresh store is born at v17, and a store a development build of 4.2
stamped v17 before an area wrote its fragment gets that fragment the next time it opens. 4.2 has
two areas, each owned by one lane: the home panels (``HOME_SQL``) and the server's software
(``SOFTWARE_SQL``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v17
from noust.core.store import SCHEMA_VERSION, App, NoustStore

LATE_TABLE = "CREATE TABLE IF NOT EXISTS v17_probe (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"


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


def stamp(path: Path, version: int) -> None:
    """Make the store at ``path`` say it is at ``version``, as one an older Noust left."""
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    conn.close()


def test_the_version_follows_v16() -> None:
    assert schema_v17.VERSION == 17
    assert SCHEMA_VERSION == schema_v17.VERSION


def test_each_area_of_4_2_has_its_own_pair() -> None:
    # A lane fills its pair and never another's: the software lane's starts empty.
    assert schema_v17.HOME_SQL.strip()
    assert schema_v17.HOME_COLUMNS == []
    assert isinstance(schema_v17.SOFTWARE_SQL, str)
    assert isinstance(schema_v17.SOFTWARE_COLUMNS, list)


def test_a_fresh_store_is_born_at_v17_with_the_home_panels(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"

    NoustStore(path)

    assert version_of(path) == 17
    assert "account_dashboards" in tables_of(path)


def test_a_store_at_v16_climbs_to_v17_and_keeps_its_rows(tmp_path: Path, fresh: None) -> None:
    path = tmp_path / "noust.db"
    store = NoustStore(path)
    store.create_app(App(domain="shop.example.com", app_type="nextjs", app_path="/opt/shop"))
    stamp(path, 16)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE account_dashboards")
    conn.commit()
    conn.close()
    assert "account_dashboards" not in tables_of(path)

    reopened = NoustStore(path)

    assert version_of(path) == 17
    assert "account_dashboards" in tables_of(path)
    assert reopened.get_app("shop.example.com") is not None


def test_a_store_that_climbs_from_v16_has_the_tables_of_v17_the_first_time_it_opens(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 16)
    monkeypatch.setattr(schema_v17, "SOFTWARE_SQL", LATE_TABLE)

    NoustStore(path)

    # The climb itself adds them: waiting for the next opening leaves a process that has just
    # upgraded a server running on a store without the tables its own code reads.
    assert "v17_probe" in tables_of(path)
    assert version_of(path) == 17


def test_a_fragment_written_after_a_store_was_stamped_v17_is_added_when_it_opens(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    assert "v17_probe" not in tables_of(path)

    monkeypatch.setattr(schema_v17, "SOFTWARE_SQL", LATE_TABLE)
    NoustStore(path)

    assert "v17_probe" in tables_of(path)
    assert version_of(path) == 17


def test_a_column_written_later_is_added_and_a_complete_store_is_left_alone(
    tmp_path: Path, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "noust.db"
    NoustStore(path)
    NoustStore.reset_instance()
    monkeypatch.setattr(schema_v17, "HOME_COLUMNS", [("apps", "v17_probe_column", "TEXT")])

    NoustStore(path)
    NoustStore.reset_instance()
    conn = sqlite3.connect(path)
    try:
        assert not schema_v17.incomplete(conn.cursor())
        columns = {row[1] for row in conn.execute("PRAGMA table_info(apps)").fetchall()}
    finally:
        conn.close()
    assert "v17_probe_column" in columns


def test_a_store_a_development_build_stamped_v16_early_is_completed_before_it_climbs(
    tmp_path: Path, fresh: None
) -> None:
    # The climb to v17 leaves the store "not at v16" for the check that completes v16, so v16 is
    # completed first or the table it lacked would be missing for good.
    path = tmp_path / "noust.db"
    NoustStore(path)
    stamp(path, 16)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE db_server_defaults")
    conn.commit()
    conn.close()

    NoustStore(path)

    assert "db_server_defaults" in tables_of(path)
    assert version_of(path) == 17


class TestAccountDashboards:
    @pytest.fixture
    def conn(self, tmp_path: Path, fresh: None) -> Iterator[sqlite3.Connection]:
        path = tmp_path / "noust.db"
        NoustStore(path)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = ON")
        for name in ("ana", "ben"):
            connection.execute(
                "INSERT INTO accounts (username, role, created_at, updated_at) "
                "VALUES (?, 'viewer', 1.0, 1.0)",
                (name,),
            )
        connection.commit()
        yield connection
        connection.close()

    def account_id(self, conn: sqlite3.Connection, username: str) -> int:
        return int(
            conn.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()[0]
        )

    def add(self, conn: sqlite3.Connection, account: int, dashboard: str, scope: str = "server"):
        conn.execute(
            "INSERT INTO account_dashboards "
            "(account_id, id, name, scope, position, layout, created_at, updated_at) "
            "VALUES (?, ?, 'Main', ?, 0, '{}', 1.0, 1.0)",
            (account, dashboard, scope),
        )

    def test_two_accounts_can_each_have_a_panel_with_the_same_id(
        self, conn: sqlite3.Connection
    ) -> None:
        self.add(conn, self.account_id(conn, "ana"), "main")
        self.add(conn, self.account_id(conn, "ben"), "main")

        assert conn.execute("SELECT COUNT(*) FROM account_dashboards").fetchone()[0] == 2

    def test_an_account_cannot_have_two_panels_with_the_same_id(
        self, conn: sqlite3.Connection
    ) -> None:
        ana = self.account_id(conn, "ana")
        self.add(conn, ana, "main")

        with pytest.raises(sqlite3.IntegrityError):
            self.add(conn, ana, "main")

    def test_a_panel_follows_the_server_or_the_fleet_and_nothing_else(
        self, conn: sqlite3.Connection
    ) -> None:
        ana = self.account_id(conn, "ana")
        self.add(conn, ana, "a", "server")
        self.add(conn, ana, "b", "fleet")

        with pytest.raises(sqlite3.IntegrityError):
            self.add(conn, ana, "c", "node")

    def test_deleting_an_account_deletes_its_panels_and_only_its_own(
        self, conn: sqlite3.Connection
    ) -> None:
        ana, ben = self.account_id(conn, "ana"), self.account_id(conn, "ben")
        self.add(conn, ana, "main")
        self.add(conn, ben, "main")

        conn.execute("DELETE FROM accounts WHERE id = ?", (ana,))

        rows = conn.execute("SELECT account_id FROM account_dashboards").fetchall()
        assert [row[0] for row in rows] == [ben]

    def test_a_panel_of_an_account_that_does_not_exist_is_refused(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            self.add(conn, 9999, "main")
