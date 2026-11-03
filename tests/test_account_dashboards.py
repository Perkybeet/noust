# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The panels of the console's Home, kept per account.

``AccountDashboards`` is the one reader and writer of ``account_dashboards``: it judges what it is
given (the closed document of ``validators/dashboards.py``), keeps at most 20 panels for an account
in the statement that inserts the 21st (so two requests at once cannot both pass), and never lets an
account read or touch another's.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.account_dashboards import AccountDashboards, Dashboard
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.validators import dashboards


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    built = NoustStore(tmp_path / "noust.db")
    yield built
    NoustStore.reset_instance()


@pytest.fixture
def panels(store: NoustStore) -> AccountDashboards:
    return AccountDashboards(store)


def make_account(store: NoustStore, username: str) -> int:
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO accounts (username, role, created_at, updated_at) "
            "VALUES (?, 'viewer', 1.0, 1.0)",
            (username,),
        )
        return int(cursor.lastrowid or 0)


@pytest.fixture
def ana(store: NoustStore) -> int:
    return make_account(store, "ana")


@pytest.fixture
def ben(store: NoustStore) -> int:
    return make_account(store, "ben")


def layout(*ids: str) -> dict[str, Any]:
    return {
        "version": 1,
        "widgets": [
            {
                "id": widget,
                "type": "needs-attention",
                "x": 0,
                "y": index * 2,
                "w": 12,
                "h": 2,
                "settings": {},
            }
            for index, widget in enumerate(ids)
        ],
    }


def save(panels: AccountDashboards, account: int, dashboard: str, **given: Any) -> Dashboard:
    fields: dict[str, Any] = {"name": "Panel", "scope": "server", "layout": layout()}
    return panels.save(account, dashboard, **{**fields, **given})


class TestSaving:
    def test_an_account_with_nothing_saved_has_no_panels(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        assert panels.for_account(ana) == []
        assert panels.get(ana, "main") is None

    def test_a_new_panel_is_kept_and_read_back_whole(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        saved = save(
            panels, ana, "main", name="  Este servidor ", scope="server", layout=layout("a", "b")
        )

        assert saved.id == "main" and saved.name == "Este servidor" and saved.scope == "server"
        assert saved.layout == layout("a", "b")
        assert saved.position == 0 and saved.created_at == saved.updated_at > 0
        assert panels.get(ana, "main") == saved
        assert panels.for_account(ana) == [saved]

    def test_each_new_panel_goes_last(self, panels: AccountDashboards, ana: int) -> None:
        for name in ("one", "two", "three"):
            save(panels, ana, name)

        assert [(p.id, p.position) for p in panels.for_account(ana)] == [
            ("one", 0),
            ("two", 1),
            ("three", 2),
        ]

    def test_saving_again_changes_it_and_keeps_its_place_and_its_birth(
        self, panels: AccountDashboards, ana: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = iter([100.0, 150.0, 200.0])
        monkeypatch.setattr("noust.core.account_dashboards.time.time", lambda: next(clock))
        save(panels, ana, "one")
        save(panels, ana, "two")
        first = save(panels, ana, "one", name="Renamed", scope="fleet", layout=layout("x"))

        assert (first.name, first.scope, first.layout) == ("Renamed", "fleet", layout("x"))
        assert first.position == 0
        assert [p.id for p in panels.for_account(ana)] == ["one", "two"]

    def test_the_time_of_a_change_moves_and_the_time_of_birth_does_not(
        self, panels: AccountDashboards, ana: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = iter([100.0, 250.0])
        monkeypatch.setattr("noust.core.account_dashboards.time.time", lambda: next(clock))

        born = save(panels, ana, "main")
        changed = save(panels, ana, "main", name="Other")

        assert (born.created_at, born.updated_at) == (100.0, 100.0)
        assert (changed.created_at, changed.updated_at) == (100.0, 250.0)

    def test_what_is_stored_is_the_checked_document_and_nothing_else(
        self, panels: AccountDashboards, store: NoustStore, ana: int
    ) -> None:
        save(panels, ana, "main", layout=layout("a"))

        with store._transaction() as cursor:
            cursor.execute("SELECT layout FROM account_dashboards")
            text = cursor.fetchone()[0]

        assert text == dashboards.layout_text(layout("a"))

    def test_a_document_that_is_not_valid_is_refused_and_leaves_what_was_there(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        save(panels, ana, "main", layout=layout("a"))

        with pytest.raises(ValidationError) as caught:
            save(panels, ana, "main", name="New", layout={"version": 2, "widgets": []})

        assert caught.value.field == "layout.version"
        kept = panels.get(ana, "main")
        assert kept is not None and kept.name == "Panel" and kept.layout == layout("a")

    @pytest.mark.parametrize(
        ("field", "value"),
        [("name", ""), ("name", "a\nb"), ("scope", "node"), ("scope", None)],
    )
    def test_a_name_or_a_scope_that_is_not_valid_is_refused(
        self, panels: AccountDashboards, ana: int, field: str, value: Any
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            save(panels, ana, "main", **{field: value})

        assert caught.value.field == field
        assert panels.for_account(ana) == []

    @pytest.mark.parametrize("dashboard", ["", "Main", "a/b", "a\n", "order", "x" * 41])
    def test_an_id_that_is_not_valid_is_refused(
        self, panels: AccountDashboards, ana: int, dashboard: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            save(panels, ana, dashboard)

        assert caught.value.field == "dashboard"

    def test_an_account_that_does_not_exist_is_refused_by_the_store_and_leaves_nothing(
        self, panels: AccountDashboards
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            save(panels, 9999, "main")


class TestTheLimitOfPanels:
    def test_an_account_keeps_twenty_and_not_a_twenty_first(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        for number in range(20):
            save(panels, ana, f"p{number}")

        with pytest.raises(ValidationError) as caught:
            save(panels, ana, "one-more")

        assert caught.value.field == "dashboard" and "20" in caught.value.message
        assert len(panels.for_account(ana)) == 20
        assert panels.get(ana, "one-more") is None

    def test_a_panel_that_exists_can_still_be_changed_at_the_limit(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        for number in range(20):
            save(panels, ana, f"p{number}")

        changed = save(panels, ana, "p7", name="Still fine")

        assert changed.name == "Still fine" and len(panels.for_account(ana)) == 20

    def test_the_limit_is_each_accounts_own(
        self, panels: AccountDashboards, ana: int, ben: int
    ) -> None:
        for number in range(20):
            save(panels, ana, f"p{number}")

        assert save(panels, ben, "p0").id == "p0"

    def test_deleting_one_makes_room(self, panels: AccountDashboards, ana: int) -> None:
        for number in range(20):
            save(panels, ana, f"p{number}")
        panels.delete(ana, "p3")

        assert save(panels, ana, "again").position == 20
        assert len(panels.for_account(ana)) == 20


class TestWhoseTheyAre:
    def test_an_account_sees_and_changes_only_its_own(
        self, panels: AccountDashboards, ana: int, ben: int
    ) -> None:
        save(panels, ana, "main", name="Ana's")
        save(panels, ben, "main", name="Ben's")

        assert [p.name for p in panels.for_account(ana)] == ["Ana's"]
        assert [p.name for p in panels.for_account(ben)] == ["Ben's"]
        assert panels.delete(ben, "main") is True
        assert [p.name for p in panels.for_account(ana)] == ["Ana's"]
        assert panels.get(ben, "main") is None

    def test_the_panels_go_with_their_account(
        self, panels: AccountDashboards, store: NoustStore, ana: int, ben: int
    ) -> None:
        save(panels, ana, "main")
        save(panels, ben, "main")

        with store._transaction() as cursor:
            cursor.execute("DELETE FROM accounts WHERE id = ?", (ana,))

        assert panels.for_account(ana) == [] and len(panels.for_account(ben)) == 1


class TestDeleting:
    def test_it_says_whether_there_was_one(self, panels: AccountDashboards, ana: int) -> None:
        save(panels, ana, "main")

        assert panels.delete(ana, "main") is True
        assert panels.delete(ana, "main") is False
        assert panels.for_account(ana) == []

    def test_the_others_keep_their_order(self, panels: AccountDashboards, ana: int) -> None:
        for name in ("a", "b", "c"):
            save(panels, ana, name)

        panels.delete(ana, "b")

        last = panels.get(ana, "c")
        assert [p.id for p in panels.for_account(ana)] == ["a", "c"]
        assert last is not None and save(panels, ana, "d").position > last.position


class TestOrdering:
    def test_the_order_given_is_the_order_kept(self, panels: AccountDashboards, ana: int) -> None:
        for name in ("a", "b", "c"):
            save(panels, ana, name)

        ordered = panels.reorder(ana, ["c", "a", "b"])

        assert [p.id for p in ordered] == ["c", "a", "b"]
        assert [p.id for p in panels.for_account(ana)] == ["c", "a", "b"]
        assert [p.position for p in panels.for_account(ana)] == [0, 1, 2]

    def test_it_must_name_every_panel_once(self, panels: AccountDashboards, ana: int) -> None:
        for name in ("a", "b", "c"):
            save(panels, ana, name)

        for bad in (["a", "b"], ["a", "b", "c", "d"], ["a", "a", "b", "c"], ["a", "b", "x"], []):
            with pytest.raises(ValidationError) as caught:
                panels.reorder(ana, bad)
            assert caught.value.field == "order"

        assert [p.id for p in panels.for_account(ana)] == ["a", "b", "c"]

    def test_an_account_cannot_order_the_panels_of_another(
        self, panels: AccountDashboards, ana: int, ben: int
    ) -> None:
        save(panels, ana, "a")
        save(panels, ben, "b")

        with pytest.raises(ValidationError):
            panels.reorder(ben, ["a"])

        assert [p.id for p in panels.for_account(ana)] == ["a"]

    def test_nothing_to_order_is_an_empty_answer(self, panels: AccountDashboards, ana: int) -> None:
        assert panels.reorder(ana, []) == []

    def test_an_id_that_is_not_one_is_refused_before_anything_is_read(
        self, panels: AccountDashboards, ana: int
    ) -> None:
        save(panels, ana, "a")

        with pytest.raises(ValidationError) as caught:
            panels.reorder(ana, ["a\n"])

        assert caught.value.field == "order"


class TestInACopyOfTheStore:
    """A panel is a person's layout, not a secret: the evidence of an incident keeps it as it is."""

    def test_the_layout_column_has_been_decided_as_not_secret(self, store: NoustStore) -> None:
        from noust.core import store_secrets

        connection = sqlite3.connect(store.db_path)
        try:
            undecided = store_secrets.columns_to_review(connection)
        finally:
            connection.close()

        assert ("account_dashboards", "layout") in store_secrets.NOT_SECRET
        assert "account_dashboards" not in store_secrets.RULES
        assert [pair for pair in undecided if pair[0] == "account_dashboards"] == []

    def test_a_scrubbed_copy_keeps_the_panels_whole(
        self, panels: AccountDashboards, store: NoustStore, ana: int, tmp_path: Path
    ) -> None:
        from noust.core import store_secrets

        saved = save(panels, ana, "main", layout=layout("a", "b"))
        copy = tmp_path / "copy.db"
        source = sqlite3.connect(store.db_path)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()

        changed = store_secrets.scrub_database(copy)

        assert "account_dashboards" not in changed
        kept = sqlite3.connect(copy)
        try:
            row = kept.execute("SELECT name, layout FROM account_dashboards").fetchone()
        finally:
            kept.close()
        assert row[0] == saved.name and row[1] == dashboards.layout_text(saved.layout)
