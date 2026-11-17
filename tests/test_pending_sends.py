# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The copies that were made and not sent (item 201).

A copy that could not reach a destination used to stay on this server and nothing remembered that it
was owed. ``pending_sends`` is that memory, one row per copy and destination, written and read only by
:class:`noust.core.pending_sends.PendingSends`. What is held here: a row is the same row however many
times its send fails, it says when it was first owed and how often it was tried, a delivered copy leaves
no trace, and a copy owed to a destination that no longer exists is not owed to anybody.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.pending_sends import MAX_REASON, PendingSends
from noust.core.store import NoustStore

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def pending(store: NoustStore) -> PendingSends:
    return PendingSends(store)


def owe(
    pending: PendingSends,
    item: str = "shop-example-com_20261117_010000",
    destination: str = "nas",
    *,
    kind: str = "app",
    at: datetime = NOW,
    reason: str = "i/o timeout",
    permanent: bool = False,
    next_attempt: datetime | None = None,
) -> None:
    pending.defer(
        kind,
        item,
        destination,
        owner="shop.example.com",
        copy_created_at=at,
        reason=reason,
        permanent=permanent,
        failure_id=None,
        now=at,
        next_attempt_at=next_attempt or at + timedelta(minutes=15),
    )


class TestOwing:
    def test_a_copy_that_was_not_sent_is_owed_to_its_destination(
        self, pending: PendingSends
    ) -> None:
        owe(pending)

        (row,) = pending.all()
        assert (row.kind, row.destination, row.owner) == ("app", "nas", "shop.example.com")
        assert row.item_id == "shop-example-com_20261117_010000"
        assert row.attempts == 0
        assert row.reason == "i/o timeout"
        assert row.noticed_at is None

    def test_the_same_copy_failing_again_is_the_same_row(self, pending: PendingSends) -> None:
        owe(pending)
        owe(pending, at=NOW + timedelta(hours=3), reason="connection refused")

        (row,) = pending.all()
        # It was first owed at 02:00; the second failure changes the reason and not the story.
        assert row.failed_at == NOW.isoformat(timespec="seconds")
        assert row.reason == "connection refused"

    def test_one_copy_owed_to_two_destinations_is_two_rows(self, pending: PendingSends) -> None:
        owe(pending, destination="nas")
        owe(pending, destination="cloud")

        assert sorted(row.destination for row in pending.all()) == ["cloud", "nas"]

    def test_a_reason_is_kept_to_a_size_a_store_can_hold(self, pending: PendingSends) -> None:
        owe(pending, reason="connection refused " * 1000)

        (row,) = pending.all()
        assert len(row.reason) <= MAX_REASON

    def test_what_is_not_a_kind_of_copy_is_refused(self, pending: PendingSends) -> None:
        with pytest.raises(ValueError, match="kind"):
            owe(pending, kind="notes")


class TestTrying:
    def test_a_failed_try_counts_and_says_when_the_next_is(self, pending: PendingSends) -> None:
        owe(pending)
        key = ("app", "shop-example-com_20261117_010000", "nas")

        again = pending.failed_again(
            *key,
            reason="connection refused",
            permanent=False,
            now=NOW + timedelta(minutes=15),
            next_attempt_at=NOW + timedelta(minutes=75),
            failure_id=7,
        )

        assert again is not None and again.attempts == 1
        assert again.next_attempt_at == (NOW + timedelta(minutes=75)).isoformat(timespec="seconds")
        assert again.failure_id == 7

    def test_what_is_due_is_what_has_waited_long_enough(self, pending: PendingSends) -> None:
        owe(pending, "early", next_attempt=NOW + timedelta(minutes=15))
        owe(pending, "late", next_attempt=NOW + timedelta(hours=4))

        due = pending.due(NOW + timedelta(minutes=16))

        assert [row.item_id for row in due] == ["early"]

    def test_a_delivered_copy_leaves_no_trace(self, pending: PendingSends) -> None:
        owe(pending)

        gone = pending.delivered("app", "shop-example-com_20261117_010000", "nas")

        assert gone is not None and gone.destination == "nas"
        assert pending.all() == []
        assert pending.delivered("app", "shop-example-com_20261117_010000", "nas") is None

    def test_what_a_notice_covered_is_remembered_as_covered(self, pending: PendingSends) -> None:
        owe(pending, "a")
        owe(pending, "b")

        pending.mark_noticed([("app", "a", "nas")], NOW)

        rows = {row.item_id: row for row in pending.all()}
        assert rows["a"].noticed_at == NOW.isoformat(timespec="seconds")
        assert rows["b"].noticed_at is None


class TestWhoIsOwed:
    def test_a_kind_of_copy_asks_which_of_its_copies_are_owed(self, pending: PendingSends) -> None:
        owe(pending, "one")
        owe(pending, "two", destination="cloud")
        owe(pending, "dump", kind="database")

        assert pending.ids("app") == {"one", "two"}
        assert pending.ids("database") == {"dump"}
        assert pending.ids("volume") == set()

    def test_what_is_owed_to_one_destination(self, pending: PendingSends) -> None:
        owe(pending, "one")
        owe(pending, "two", destination="cloud")

        assert [row.item_id for row in pending.for_destination("cloud")] == ["two"]

    def test_what_is_owed_for_an_owner(self, pending: PendingSends) -> None:
        owe(pending, "one")

        assert [row.item_id for row in pending.for_owner("app", "shop.example.com")] == ["one"]
        assert pending.for_owner("app", "blog.example.com") == []

    def test_a_destination_that_is_gone_is_owed_nothing(self, pending: PendingSends) -> None:
        owe(pending, "one", destination="nas")
        owe(pending, "two", destination="cloud")

        assert pending.forget_destination("nas") == 1

        assert [row.destination for row in pending.all()] == ["cloud"]

    def test_a_copy_deleted_by_hand_is_not_owed_any_more(self, pending: PendingSends) -> None:
        owe(pending, "one", destination="nas")
        owe(pending, "one", destination="cloud")

        assert pending.forget_copy("app", "one") == 2

        assert pending.all() == []


class TestTheStore:
    def test_a_fresh_store_has_the_table(self, store: NoustStore) -> None:
        with store._transaction() as cursor:
            rows = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pending_sends'"
            ).fetchall()

        assert len(rows) == 1

    def test_the_table_is_the_one_fragment_of_this_area(self) -> None:
        from noust.core import schema_v18

        assert "pending_sends" in schema_v18.SENDING_SQL
        assert isinstance(schema_v18.SENDING_COLUMNS, list)

    def test_a_store_without_it_gets_it_when_it_opens(self, tmp_path: Path) -> None:
        path = tmp_path / "old.db"
        NoustStore.reset_instance()
        NoustStore(path).close()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE pending_sends")
        conn.commit()
        conn.close()

        NoustStore.reset_instance()
        reopened = NoustStore(path)
        try:
            assert PendingSends(reopened).all() == []
        finally:
            reopened.close()
            NoustStore.reset_instance()


class TestTheClimbAndTheWayBack:
    def test_a_store_at_v17_climbs_and_gets_the_table_without_touching_its_rows(
        self, tmp_path: Path
    ) -> None:
        from noust.core.store import App

        path = tmp_path / "old.db"
        NoustStore.reset_instance()
        store = NoustStore(path)
        store.create_app(App(domain="shop.example.com", app_type="nextjs", app_path="/opt/shop"))
        store.close()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE pending_sends")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (17)")
        conn.commit()
        conn.close()

        NoustStore.reset_instance()
        reopened = NoustStore(path)
        try:
            assert PendingSends(reopened).all() == []
            shop = reopened.get_app("shop.example.com")
            assert shop is not None and shop.app_path == "/opt/shop"
        finally:
            reopened.close()
            NoustStore.reset_instance()

    def test_the_rows_survive_a_reopen_and_the_older_tables_are_not_changed_by_the_new_one(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "noust.db"
        NoustStore.reset_instance()
        store = NoustStore(path)
        PendingSends(store).defer(
            "app",
            "a",
            "nas",
            owner="shop.example.com",
            copy_created_at=NOW,
            reason="i/o timeout",
            permanent=False,
            failure_id=None,
            now=NOW,
            next_attempt_at=NOW + timedelta(minutes=15),
        )
        store.close()

        NoustStore.reset_instance()
        reopened = NoustStore(path)
        try:
            assert [row.item_id for row in PendingSends(reopened).all()] == ["a"]
            conn = sqlite3.connect(path)
            # The table stands alone: no foreign key into an older table, so a release that does not
            # know it (4.2 reading a copy of the store from before the climb) never meets it.
            assert conn.execute("PRAGMA foreign_key_list(pending_sends)").fetchall() == []
            conn.close()
        finally:
            reopened.close()
            NoustStore.reset_instance()
