# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node applied of the fleet's settings: ``fleet_managed`` and its states.

What is pinned: the node keeps a fingerprint and never a value; a setting that its own operator
changes (or removes) is found the next time anyone looks, whichever way it was changed - the
console, the command line, a hand-edited file - because the node looks at what it holds and does
not wait to be told; the central's push puts it back to managed; a node that is released keeps
what it has; and the secrets of a setting take part in the fingerprint without ever being kept.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.core.teams.model import ResourceNotFoundError
from noust.core.variables.model import SERVER
from noust.core.variables.store import VariableStore
from noust.fleet.settings.managed import ManagedSettings
from noust.fleet.settings.signature import SyncSignature

SIGNATURE = SyncSignature(person="ana", change="c0123456789ab")


class Held:
    """What a node holds, as a test changes it by hand."""

    def __init__(self) -> None:
        self.things: dict[tuple[str, str], dict[str, Any]] = {}

    def __call__(self, resource: str, name: str, **_: Any) -> dict[str, Any] | None:
        found = self.things.get((resource, name))
        return dict(found) if found is not None else None


@pytest.fixture
def held() -> Held:
    return Held()


@pytest.fixture
def managed(store: NoustStore, secrets: SecretStore, held: Held) -> ManagedSettings:
    return ManagedSettings(store, secrets, reader=held)


def apply(managed: ManagedSettings, held: Held, name: str = "ops", version: int = 7, **value: Any):
    held.things[("channel", name)] = value or {"type": "slack", "enabled": True}
    return managed.record_applied(
        "channel", name, central="fleet-nas", version=version, signature=SIGNATURE
    )


class TestRecordingWhatWasApplied:
    def test_a_pushed_setting_is_managed_with_the_central_the_version_and_the_person(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        row = apply(managed, held)

        assert (row.resource, row.name, row.state) == ("channel", "ops", "managed")
        assert (row.central, row.version, row.applied_by) == ("fleet-nas", 7, "ana")
        assert row.change_id == "c0123456789ab"
        assert managed.list() == [row]

    def test_what_is_kept_is_a_fingerprint_and_never_the_value(
        self, managed: ManagedSettings, held: Held, store: NoustStore
    ) -> None:
        apply(managed, held, token="bot:s3cret", type="telegram")

        rows = store._get_connection().execute("SELECT * FROM fleet_managed").fetchall()

        assert "s3cret" not in repr([tuple(row) for row in rows])
        assert len(rows[0]["fingerprint"]) == 64

    def test_the_fingerprint_depends_on_the_secrets_too_and_on_a_key_of_this_node(
        self, managed: ManagedSettings, held: Held, store: NoustStore, tmp_path: Any
    ) -> None:
        first = apply(managed, held, token="one", type="telegram").fingerprint
        second = apply(managed, held, token="two", type="telegram").fingerprint
        elsewhere = ManagedSettings(
            store, SecretStore(root=tmp_path / "other"), reader=held
        ).fingerprint({"token": "one", "type": "telegram"})

        assert first != second
        assert elsewhere != first, "a fingerprint must not be computable without this node's key"

    def test_recording_what_the_node_does_not_hold_is_refused(
        self, managed: ManagedSettings
    ) -> None:
        with pytest.raises(ResourceNotFoundError) as caught:
            managed.record_applied(
                "channel", "ghost", central="fleet-nas", version=1, signature=SIGNATURE
            )

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.managed_missing"
        assert managed.list() == []

    def test_applying_again_updates_the_row_and_makes_it_managed_again(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held, version=7, enabled=True, type="slack")
        held.things[("channel", "ops")]["enabled"] = False
        assert managed.verified()[0].state == "overridden"

        row = apply(managed, held, version=9, enabled=False, type="slack")

        assert (row.state, row.version, row.overridden_at) == ("managed", 9, None)
        assert managed.verified()[0].state == "managed"


class TestFindingWhatTheOperatorChanged:
    def test_an_untouched_setting_stays_managed(self, managed: ManagedSettings, held: Held) -> None:
        apply(managed, held)

        assert [row.state for row in managed.verified()] == ["managed"]

    def test_a_setting_changed_by_hand_is_overridden(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held)
        held.things[("channel", "ops")]["enabled"] = False

        row = managed.verified()[0]

        assert row.state == "overridden"
        assert row.overridden_at is not None
        assert row.version == 7, "it still says which version the node last took"

    def test_a_setting_removed_by_hand_is_overridden_too(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held)
        del held.things[("channel", "ops")]

        assert managed.verified()[0].state == "overridden"

    def test_a_secret_changed_by_hand_is_found_without_ever_reading_the_old_one(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held, token="one", type="telegram")
        held.things[("channel", "ops")]["token"] = "two"

        assert managed.verified()[0].state == "overridden"

    def test_once_overridden_it_stays_so_until_the_central_applies_it_again(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held, enabled=True, type="slack")
        held.things[("channel", "ops")]["enabled"] = False
        managed.verified()
        held.things[("channel", "ops")]["enabled"] = True  # changed back by hand

        assert managed.verified()[0].state == "overridden"

    def test_the_audit_trail_says_it_once(
        self, managed: ManagedSettings, held: Held, audited: list[Any]
    ) -> None:
        apply(managed, held)
        held.things[("channel", "ops")]["enabled"] = False

        managed.verified()
        managed.verified()

        events = [e for e in audited if e["event"] == "fleet.setting.diverge"]
        assert len(events) == 1
        assert events[0]["target"] == "channel:ops"

    def test_a_kind_this_node_cannot_read_is_left_as_it_is(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        def unreadable(resource: str, name: str, **_: Any) -> dict[str, Any] | None:
            raise KeyError(resource)

        managed = ManagedSettings(store, secrets, reader=unreadable)
        store._get_connection().execute(
            "INSERT INTO fleet_managed (resource, name, central, version, fingerprint, change_id, "
            "applied_by, applied_at) VALUES ('newer_kind', 'x', 'c', 1, 'f', 'c', 'a', 't')"
        )

        assert [row.state for row in managed.verified()] == ["managed"]


class TestLettingGo:
    def test_releasing_keeps_the_setting_and_forgets_it_was_the_fleets(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held)

        assert managed.release("channel", "ops") is True

        assert managed.list() == []
        assert ("channel", "ops") in held.things
        assert managed.release("channel", "ops") is False

    def test_a_central_that_leaves_takes_only_its_own_settings_off(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held, "a")
        held.things[("channel", "b")] = {"type": "slack"}
        managed.record_applied(
            "channel", "b", central="other-central", version=3, signature=SIGNATURE
        )

        assert managed.release_all("fleet-nas") == 1

        assert [row.name for row in managed.list()] == ["b"]


class TestAskingToGoBack:
    def test_the_operator_of_an_overridden_setting_can_ask_the_central_to_put_it_back(
        self, managed: ManagedSettings, held: Held
    ) -> None:
        apply(managed, held)
        held.things[("channel", "ops")]["enabled"] = False
        managed.verified()

        row = managed.request_restore("channel", "ops")

        assert row.restore_requested_at is not None
        assert row.state == "overridden"

    def test_a_setting_nobody_manages_cannot_be_asked_back(self, managed: ManagedSettings) -> None:
        with pytest.raises(ResourceNotFoundError) as caught:
            managed.request_restore("channel", "ops")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.not_managed"

    def test_applying_it_clears_the_request(self, managed: ManagedSettings, held: Held) -> None:
        apply(managed, held)
        held.things[("channel", "ops")]["enabled"] = False
        managed.verified()
        managed.request_restore("channel", "ops")

        row = apply(managed, held, version=11, enabled=True, type="slack")

        assert row.restore_requested_at is None


class TestWithTheNodesOwnManagers:
    def test_a_shared_variable_is_read_from_the_variable_store(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "REGION", "eu", actor="op")
        managed = ManagedSettings(store, secrets)

        row = managed.record_applied(
            "variable", "REGION", central="fleet-nas", version=4, signature=SIGNATURE
        )
        VariableStore(store).set(SERVER, 0, "REGION", "us", actor="op")

        assert row.state == "managed"
        assert managed.verified()[0].state == "overridden"
