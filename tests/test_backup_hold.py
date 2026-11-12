# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server does not rotate away a copy its central has not taken yet (item 195).

The rule is one function every retention asks, so what is pinned here is the rule itself against a
real store: who counts, for how long, and what happens when nobody can say. The retentions of each
kind of copy are held to calling it in the tests beside this one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit, backup_hold
from noust.core.backup_collection_store import FRESH_DAYS, CentralHeld, HeldCopy
from noust.core.store import NoustStore

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def speak(central: str, *ids: str, at: datetime = NOW) -> None:
    held = CentralHeld()
    held.replace(central, [HeldCopy("app", item) for item in ids])
    # What was said is dated by the clock; the tests that need another moment say it by hand.
    with held.store._transaction() as cursor:
        cursor.execute(
            "UPDATE backup_central_syncs SET synced_at = ? WHERE central = ?",
            (at.isoformat(), central),
        )


class TestWhoIsWaiting:
    def test_with_no_central_nothing_is_spared(self) -> None:
        assert backup_hold.spared("app", [("a", days_ago(1))], now=NOW) == set()

    def test_a_copy_the_central_lacks_is_spared_and_one_it_has_is_not(self) -> None:
        speak("nas", "has")

        spared = backup_hold.spared("app", [("has", days_ago(1)), ("lacks", days_ago(1))], now=NOW)

        assert spared == {"lacks"}

    def test_while_any_central_that_follows_lacks_it(self) -> None:
        speak("nas", "a", "b")
        speak("office", "a")

        spared = backup_hold.spared("app", [("a", days_ago(1)), ("b", days_ago(1))], now=NOW)

        assert spared == {"b"}

    def test_a_central_that_has_not_spoken_for_a_week_is_not_waited_for(self) -> None:
        speak("nas", at=NOW - timedelta(days=FRESH_DAYS + 1))

        assert backup_hold.spared("app", [("a", days_ago(1))], now=NOW) == set()

    def test_a_central_still_counts_on_the_last_day_it_may(self) -> None:
        speak("nas", at=NOW - timedelta(days=FRESH_DAYS - 1))

        assert backup_hold.spared("app", [("a", days_ago(1))], now=NOW) == {"a"}

    def test_what_it_holds_of_another_kind_is_another_thing(self) -> None:
        speak("nas", "a")

        assert backup_hold.spared("database", [("a", days_ago(1))], now=NOW) == {"a"}


class TestForHowLong:
    def test_a_copy_older_than_the_limit_goes_back_to_the_retention(self) -> None:
        speak("nas")

        spared = backup_hold.spared(
            "app",
            [
                ("young", days_ago(backup_hold.HOLD_MAX_DAYS - 1)),
                ("old", days_ago(backup_hold.HOLD_MAX_DAYS + 1)),
            ],
            now=NOW,
        )

        assert spared == {"young"}

    def test_a_time_with_no_zone_is_this_servers_and_a_bad_one_is_not_old(self) -> None:
        speak("nas")
        recent = (datetime.now() - timedelta(hours=2)).isoformat()

        spared = backup_hold.spared(
            "app", [("naive", recent), ("garbled", "not a date")], now=datetime.now(timezone.utc)
        )

        assert spared == {"naive", "garbled"}


class TestWhenNobodyCanSay:
    def test_a_store_that_cannot_be_read_keeps_what_was_about_to_be_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: Any, now: Any = None) -> Any:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(CentralHeld, "following", broken)

        assert backup_hold.spared("app", [("a", days_ago(1)), ("b", days_ago(99))], now=NOW) == {
            "a",
            "b",
        }


class TestSayingSo:
    @pytest.fixture
    def recorded(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: found.append((event, kw)) or None)
        return found

    def test_a_copy_a_central_was_waiting_for_is_named_when_it_goes(
        self, recorded: list[tuple[str, dict[str, Any]]]
    ) -> None:
        speak("nas")

        backup_hold.note_rotated_out("app", "shop_1", owner="shop.example.com", now=NOW)

        ((event, details),) = recorded
        assert event == "backup.uncollected_delete"
        assert details["target"] == "app:shop.example.com"
        assert details["details"] == {"copy": "shop_1", "centrals": ["nas"]}

    def test_nothing_is_said_for_a_copy_every_central_has(
        self, recorded: list[tuple[str, dict[str, Any]]]
    ) -> None:
        speak("nas", "shop_1")

        backup_hold.note_rotated_out("app", "shop_1", owner="shop.example.com", now=NOW)

        assert recorded == []

    def test_nothing_is_said_when_no_central_follows(
        self, recorded: list[tuple[str, dict[str, Any]]]
    ) -> None:
        backup_hold.note_rotated_out("app", "shop_1", owner="shop.example.com", now=NOW)

        assert recorded == []


class TestTheRecordOfWhatACentralHolds:
    def test_a_central_is_forgotten_with_what_it_held(self) -> None:
        speak("nas", "a")

        CentralHeld().forget("nas")

        assert CentralHeld().syncs() == [] and CentralHeld().holders("app", "a") == []

    def test_a_central_that_spoke_again_is_dated_again(self) -> None:
        speak("nas", "a", at=NOW - timedelta(days=30))
        assert CentralHeld().following(NOW) == []

        CentralHeld().replace("nas", [HeldCopy("app", "a")])

        assert [sync.central for sync in CentralHeld().following()] == ["nas"]
