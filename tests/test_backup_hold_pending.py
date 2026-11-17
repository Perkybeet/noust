# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A retention does not delete a copy that is still owed to a destination (item 201).

The copy that did not arrive is on this server and nowhere else. The retention of every kind of
copy asks one rule (:mod:`noust.core.backup_hold`) which copies to leave alone, and a copy that is
owed to a destination is one of them, for the same fourteen days a copy a central has not taken is.
Past them the retention decides again (a disk cannot wait for ever) and says what went.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit, backup_hold
from noust.core.pending_sends import PendingSends
from noust.core.store import NoustStore

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    yield instance
    NoustStore.reset_instance()


def days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def owe(item: str, *, kind: str = "app", destination: str = "nas", age_days: float = 1) -> None:
    PendingSends().defer(
        kind,
        item,
        destination,
        owner="shop.example.com",
        copy_created_at=NOW - timedelta(days=age_days),
        reason="i/o timeout",
        permanent=False,
        failure_id=None,
        now=NOW,
        next_attempt_at=NOW + timedelta(minutes=15),
    )


def test_a_copy_that_is_owed_is_spared_and_one_that_arrived_is_not() -> None:
    owe("owed")

    spared = backup_hold.spared("app", [("owed", days_ago(3)), ("sent", days_ago(3))], now=NOW)

    assert spared == {"owed"}


def test_it_is_the_copy_of_that_kind_that_is_spared() -> None:
    owe("dump", kind="database")

    assert backup_hold.spared("app", [("dump", days_ago(1))], now=NOW) == set()
    assert backup_hold.spared("database", [("dump", days_ago(1))], now=NOW) == {"dump"}


def test_past_fourteen_days_the_retention_decides_again() -> None:
    owe("old", age_days=15)

    assert backup_hold.spared("app", [("old", days_ago(15))], now=NOW) == set()
    assert backup_hold.spared("app", [("old", days_ago(13))], now=NOW) == {"old"}


def test_when_nobody_can_say_what_is_owed_the_copies_are_kept_this_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: PendingSends, kind: str) -> set[str]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(PendingSends, "ids", broken)

    spared = backup_hold.spared("app", [("a", days_ago(1)), ("b", days_ago(1))], now=NOW)

    assert spared == {"a", "b"}


def test_a_retention_that_deletes_an_owed_copy_says_so_and_the_owing_goes_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owe("old", age_days=20)
    said: list[dict[str, Any]] = []
    monkeypatch.setattr(
        audit,
        "record",
        lambda event, **kwargs: said.append({"event": event, **kwargs}),
    )
    deleted: list[str] = []

    backup_hold.rotate_out(deleted.append, "app", "old", "shop.example.com", now=NOW)

    assert deleted == ["old"]
    assert PendingSends().all() == []
    (record,) = said
    assert record["event"] == "backup.unsent_delete"
    assert record["details"]["destinations"] == ["nas"]


def test_a_copy_that_was_sent_leaves_no_word_when_it_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said: list[str] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: said.append(event))

    backup_hold.rotate_out(lambda item: None, "app", "sent", "shop.example.com", now=NOW)

    assert said == []
