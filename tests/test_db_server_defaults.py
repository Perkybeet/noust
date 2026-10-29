# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which database server a new database of an engine is made on (4.1, item 103).

One default per engine on a server, as a hosting panel has: the system's, or a container Noust
runs. The store keeps only the operator's choice (``db_server_defaults``, schema v16); what is
the default when nobody chose is worked out by :mod:`noust.managers.database.servers`. What is
pinned here: the table exists in a fresh store and in one a development build stamped v16
before the area had its fragment, a choice replaces the one before it, every value is judged
before it is written, and forgetting a server forgets that it was the default.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from noust.core import schema_v16
from noust.core.db_server_defaults import DbServerDefaultStore
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore


@pytest.fixture
def defaults(store: NoustStore) -> DbServerDefaultStore:
    return DbServerDefaultStore(store)


def run_script(cursor: sqlite3.Cursor, script: str) -> None:
    for statement in script.split(";"):
        if statement.strip():
            cursor.execute(statement)


class TestTheFragment:
    def test_a_fresh_store_has_the_table(self, tmp_path: Path, store: NoustStore) -> None:
        connection = sqlite3.connect(tmp_path / "noust.db")
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        finally:
            connection.close()

        assert "db_server_defaults" in tables

    def test_a_store_stamped_v16_without_the_area_is_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = sqlite3.connect(":memory:")
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")
        monkeypatch.setattr(schema_v16, "ROLES_SQL", "")
        monkeypatch.setattr(schema_v16, "FLEET_SQL", "")
        monkeypatch.setattr(schema_v16, "NOTIFY_SQL", "")
        monkeypatch.setattr(schema_v16, "DNS_SQL", "")
        monkeypatch.setattr(schema_v16, "SOURCES_SQL", "")

        assert schema_v16.incomplete(cursor) is True
        schema_v16.apply_v16(cursor, run_script)

        assert schema_v16.incomplete(cursor) is False
        assert cursor.execute("SELECT count(*) FROM db_server_defaults").fetchone() == (0,)


class TestChoosing:
    def test_nothing_is_chosen_until_somebody_chooses(self, defaults: DbServerDefaultStore) -> None:
        assert defaults.get("mysql") is None
        assert defaults.all() == {}

    def test_a_choice_is_kept_with_who_made_it(self, defaults: DbServerDefaultStore) -> None:
        row = defaults.set("mysql", "mysql@legacy", set_by="alice")

        assert (row.flavour, row.server_key, row.set_by) == ("mysql", "mysql@legacy", "alice")
        assert defaults.get("mysql") == row
        assert row.set_at

    def test_a_second_choice_replaces_the_first(self, defaults: DbServerDefaultStore) -> None:
        defaults.set("mysql", "mysql@legacy", set_by="alice")
        defaults.set("mysql", "mysql", set_by="bob")

        row = defaults.get("mysql")
        assert row is not None and (row.server_key, row.set_by) == ("mysql", "bob")
        assert len(defaults.all()) == 1

    def test_each_engine_has_its_own(self, defaults: DbServerDefaultStore) -> None:
        defaults.set("mysql", "mysql@a", set_by=None)
        defaults.set("postgresql", "postgresql@b", set_by=None)

        assert {flavour: row.server_key for flavour, row in defaults.all().items()} == {
            "mysql": "mysql@a",
            "postgresql": "postgresql@b",
        }

    def test_taking_it_back_leaves_the_engine_with_none(
        self, defaults: DbServerDefaultStore
    ) -> None:
        defaults.set("mysql", "mysql@legacy", set_by=None)

        assert defaults.clear("mysql") is True
        assert defaults.get("mysql") is None
        assert defaults.clear("mysql") is False

    def test_forgetting_a_server_forgets_that_it_was_the_default(
        self, defaults: DbServerDefaultStore
    ) -> None:
        defaults.set("mysql", "mysql@legacy", set_by=None)
        defaults.set("mariadb", "mariadb@other", set_by=None)

        assert defaults.forget("mysql@legacy") == 1
        assert defaults.get("mysql") is None and defaults.get("mariadb") is not None
        assert defaults.forget("mysql@legacy") == 0


class TestWhatIsWritten:
    @pytest.mark.parametrize(
        "flavour", ["", "MySQL", "my sql", "mysql;", "1sql", "a", "x" * 40, "mysql\n"]
    )
    def test_a_flavour_that_is_not_one_is_refused(
        self, defaults: DbServerDefaultStore, flavour: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            defaults.set(flavour, "mysql@a", set_by=None)

        assert caught.value.field == "engine"
        assert defaults.all() == {}

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "mysql@",
            "@a",
            "MySQL@a",
            "mysql@a b",
            "mysql@a;rm",
            "mysql@a@b",
            "mysql@a\n",
            "mysql@" + "a" * 60,
            "mysql@project.service",
        ],
    )
    def test_a_key_that_is_not_one_of_a_server_noust_can_default_is_refused(
        self, defaults: DbServerDefaultStore, key: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            defaults.set("mysql", key, set_by=None)

        assert caught.value.field == "server"
        assert defaults.all() == {}

    def test_the_actor_is_bounded_text(self, defaults: DbServerDefaultStore) -> None:
        with pytest.raises(ValidationError):
            defaults.set("mysql", "mysql@a", set_by="x" * 500)
