# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a central collects: who is followed, the speed, the retention, the room kept (item 195).

Collecting is off until somebody turns it on, for the fleet or for one server, and "N of M" is one
function. Every knob is judged on its way in, a change is all or nothing, and a row nobody can read is
the default and not a reason to stop collecting.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NodeRecord, NoustStore
from noust.fleet.collect.records import (
    Collected,
    CollectedFile,
    CollectedStore,
    NodeStates,
)
from noust.fleet.collect.settings import DEFAULTS, KEYS, Follows, SettingsStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for name in ("a", "b", "c"):
        instance.save_node(
            NodeRecord(
                name=name,
                ssh_host=f"{name}.example.com",
                ssh_port=22,
                ssh_user="noust-tunnel",
                host_key=f"noust-node-{name} ssh-ed25519 AAAA",
                console_port=8080,
            )
        )
    yield instance
    NoustStore.reset_instance()


class TestTheKnobs:
    def test_a_central_that_was_never_told_collects_nothing(self, store: NoustStore) -> None:
        settings = SettingsStore(store).get()

        assert settings == DEFAULTS and settings.enabled is False
        assert Follows(store).counts().following == 0

    def test_what_is_set_is_kept_and_the_rest_stays_default(self, store: NoustStore) -> None:
        SettingsStore(store).set(rate_limit_kbps=2048, retention_count=10, kinds=["noust", "app"])

        settings = SettingsStore(store).get()

        assert settings.rate_bytes_per_second == 2048 * 1024
        assert settings.retention_count == 10 and settings.kinds == ("app", "noust")
        assert settings.poll_seconds == DEFAULTS.poll_seconds

    def test_a_limit_is_taken_off_with_none(self, store: NoustStore) -> None:
        SettingsStore(store).set(rate_limit_kbps=100, retention_days=7)

        SettingsStore(store).set(rate_limit_kbps=None, retention_days=None)

        assert SettingsStore(store).get().rate_limit_kbps is None

    @pytest.mark.parametrize(
        ("knob", "value"),
        [
            ("enabled", "yes"),
            ("enabled", 1),
            ("kinds", []),
            ("kinds", ["app", "secrets"]),
            ("kinds", "app"),
            ("rate_limit_kbps", 0),
            ("rate_limit_kbps", -5),
            ("rate_limit_kbps", True),
            ("rate_limit_kbps", 1.5),
            ("poll_seconds", 1),
            ("poll_seconds", 10**6),
            ("pass_hours", 0),
            ("retention_count", 0),
            ("retention_days", -1),
            ("retention_gb", 0),
            ("retention_gb", "big"),
            ("min_free_gb", -1),
            ("nonsense", 1),
        ],
    )
    def test_a_value_that_is_not_right_is_refused_naming_the_knob_and_nothing_changes(
        self, store: NoustStore, knob: str, value: Any
    ) -> None:
        SettingsStore(store).set(retention_count=5)

        with pytest.raises(ValidationError) as raised:
            SettingsStore(store).set(**{"poll_seconds": 120, knob: value})

        assert raised.value.field == knob
        assert SettingsStore(store).get().poll_seconds == DEFAULTS.poll_seconds
        assert SettingsStore(store).get().retention_count == 5

    def test_a_row_nobody_can_read_is_the_default(self, store: NoustStore) -> None:
        with store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO collect_settings (key, value, updated_at) VALUES "
                "('poll_seconds', 'not json', 'x'), ('rate_limit_kbps', '-9', 'x'), "
                "('old_knob', '1', 'x')"
            )

        assert SettingsStore(store).get() == DEFAULTS

    def test_every_knob_is_one_the_settings_have(self) -> None:
        assert set(KEYS) == set(DEFAULTS.to_dict())


class TestWhoIsFollowed:
    def test_the_fleet_answers_for_every_server_that_has_no_answer_of_its_own(
        self, store: NoustStore
    ) -> None:
        SettingsStore(store).set(enabled=True)

        assert all(Follows(store).follows(name) for name in "abc")

    def test_a_server_that_has_its_own_answer_keeps_it_whatever_the_fleet_says(
        self, store: NoustStore
    ) -> None:
        SettingsStore(store).set(enabled=True)
        Follows(store).set_follow("b", False)
        SettingsStore(store).set(enabled=False)
        Follows(store).set_follow("c", True)

        assert [Follows(store).follows(name) for name in "abc"] == [False, False, True]

    def test_none_gives_a_server_back_to_the_fleet(self, store: NoustStore) -> None:
        SettingsStore(store).set(enabled=True)
        Follows(store).set_follow("a", False)

        Follows(store).set_follow("a", None)

        assert Follows(store).follows("a")

    def test_the_count_is_followed_of_managed_and_names_the_exceptions(
        self, store: NoustStore
    ) -> None:
        SettingsStore(store).set(enabled=True)
        Follows(store).set_follow("b", False)

        count = Follows(store).counts()

        assert (count.following, count.of, count.exceptions) == (2, 3, ("b",))

    def test_a_server_that_is_not_managed_cannot_be_given_an_answer(
        self, store: NoustStore
    ) -> None:
        with pytest.raises(ValidationError) as raised:
            Follows(store).set_follow("ghost", True)

        assert raised.value.field == "node"

    def test_an_answer_that_agrees_with_the_fleet_is_not_an_exception(
        self, store: NoustStore
    ) -> None:
        SettingsStore(store).set(enabled=True)
        Follows(store).set_follow("a", True)

        assert Follows(store).counts().exceptions == ()


def copy(node: str = "a", item_id: str = "x", size: int = 10, **kw: Any) -> Collected:
    return Collected(
        node=node,
        kind=kw.pop("kind", "app"),
        item_id=item_id,
        owner="shop.example.com",
        folder="shop",
        origin="scheduled",
        created_at="2026-11-10T02:00:00+00:00",
        collected_at="2026-11-10T02:05:00+00:00",
        size=size,
        files=(CollectedFile("x.tar.gz", size, "a" * 64, "archive"),),
        verified="digest",
        **kw,
    )


class TestTheRecords:
    def test_a_copy_is_written_and_read_back(self, store: NoustStore) -> None:
        CollectedStore(store).add(copy(meta={"domain": "shop.example.com"}))

        got = CollectedStore(store).get("a", "app", "x")

        assert got is not None and got.files[0].sha256 == "a" * 64
        assert got.meta == {"domain": "shop.example.com"}
        assert "path" not in str(got.to_dict())

    def test_retiring_keeps_the_record_and_empties_it(self, store: NoustStore) -> None:
        CollectedStore(store).add(copy())

        assert CollectedStore(store).retire("a", "app", "x") is True

        got = CollectedStore(store).get("a", "app", "x")
        assert got is not None and got.state == "retired" and got.files == () and got.size == 0
        assert CollectedStore(store).list() == []
        assert CollectedStore(store).retire("a", "app", "x") is False

    def test_the_list_is_by_server_kind_and_the_servers_a_person_sees(
        self, store: NoustStore
    ) -> None:
        CollectedStore(store).add(copy("a", "1"))
        CollectedStore(store).add(copy("b", "2", kind="database"))
        CollectedStore(store).add(copy("c", "3"))

        found = CollectedStore(store)

        assert [c.node for c in found.list(node="a")] == ["a"]
        assert [c.node for c in found.list(kind="database")] == ["b"]
        assert sorted(c.node for c in found.list(nodes=["a", "c"])) == ["a", "c"]
        assert found.list(nodes=[]) == []

    def test_the_weight_is_what_is_held_by_kind(self, store: NoustStore) -> None:
        CollectedStore(store).add(copy("a", "1", size=100))
        CollectedStore(store).add(copy("a", "2", size=50))
        CollectedStore(store).add(copy("a", "3", size=7, kind="volume"))
        CollectedStore(store).retire("a", "app", "2")

        assert CollectedStore(store).usage("a") == {"app": 100, "volume": 7}

    def test_a_retired_record_goes_when_the_server_stops_listing_it(
        self, store: NoustStore
    ) -> None:
        CollectedStore(store).add(copy("a", "1"))
        CollectedStore(store).add(copy("a", "2"))
        CollectedStore(store).retire("a", "app", "1")
        CollectedStore(store).retire("a", "app", "2")

        dropped = CollectedStore(store).forget_retired_except("a", {("app", "2")})

        assert dropped == 1
        assert CollectedStore(store).get("a", "app", "1") is None
        assert CollectedStore(store).get("a", "app", "2") is not None

    def test_a_server_that_is_removed_takes_its_records_with_it(self, store: NoustStore) -> None:
        CollectedStore(store).add(copy("a", "1"))
        NodeStates(store).record_pass("a", ok=True)

        CollectedStore(store).forget_node("a")

        assert CollectedStore(store).list(state=None) == [] and NodeStates(store).all() == {}


class TestHowEachServerIsDoing:
    def test_a_failure_counts_in_a_row_and_a_success_clears_it(self, store: NoustStore) -> None:
        states = NodeStates(store)

        states.record_pass("a", ok=False, error="no route", code="unreachable")
        second = states.record_pass("a", ok=False, error="no route", code="unreachable")
        assert (second.failures, second.last_error_code) == (2, "unreachable")
        done = states.record_pass("a", ok=True, fingerprint="f1", support={"mode": "full"})

        assert (done.failures, done.last_error, done.fingerprint) == (0, None, "f1")
        assert done.last_ok_at and done.support == {"mode": "full"}

    def test_a_failure_keeps_when_it_last_worked_and_forgets_the_fingerprint(
        self, store: NoustStore
    ) -> None:
        states = NodeStates(store)
        ok = states.record_pass("a", ok=True, fingerprint="f1")

        failed = states.record_pass("a", ok=False, error="x", code="mismatch")

        assert failed.last_ok_at == ok.last_ok_at and failed.fingerprint is None

    def test_a_look_that_found_nothing_new_is_not_a_whole_pass(self, store: NoustStore) -> None:
        states = NodeStates(store)
        ok = states.record_pass("a", ok=True, fingerprint="f1")

        states.record_look("a")

        after = states.get("a")
        assert after.last_ok_at == ok.last_ok_at and after.fingerprint == "f1"

    def test_a_server_that_was_never_looked_at_has_an_empty_state(self, store: NoustStore) -> None:
        assert NodeStates(store).get("a").last_pass_at is None
