# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The policy of a server as a setting of the fleet (item 166): what a node reads, how it is pushed and what
a node without it says.

A policy is the four settings of "General" that say how a server behaves and not what it is:
whether it asks for new releases, the address certificate notices go to, how many backups it keeps
of each application and how long a deployment may build. What is pinned: a node reads them from its
own configuration and fingerprints exactly those, so that a change to a folder or the web server
never makes the policy "its own"; a node whose operator already chose something else is not
overwritten on a first look; the central writes it by the node's own route and records it; and a
node on an older Noust, which has neither the route nor the kind, is told apart from one that is
only behind and does not break the rest of its row.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings import policy, snapshots
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.push import find, push
from noust.fleet.settings.status import read
from tests.support.fleet_actions import ADMIN
from tests.support.fleet_settings_actions_support import HELD, apply, node_items, save, states
from tests.support.fleet_settings_node import DEFAULT_POLICY, POLICY_PATH, ManagedNode
from tests.support.fleet_views_support import Central, build_central

OWN: dict[str, Any] = {
    "updates_check": False,
    "ssl_email": "ops@example.com",
    "backup_max_per_app": 5,
    "delivery_max_minutes": 30,
}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    yield Config()
    Config.reset_instance()


def change(config: Config, **keys: Any) -> None:
    """Its operator sets keys, by the command line or the console, so that the file says it."""
    for key, value in keys.items():
        config.set(key.replace("__", "."), value)
    config.write()


class TestWhatANodeReadsOfItsOwnPolicy:
    def test_an_untouched_node_reads_the_defaults_and_is_not_customised(
        self, config: Config
    ) -> None:
        assert policy.read(config) == DEFAULT_POLICY
        assert policy.read(config) == policy.defaults()
        assert policy.is_customized(policy.read(config)) is False

    def test_it_reads_each_field_where_the_servers_own_commands_keep_it(
        self, config: Config
    ) -> None:
        config.set("updates.check", False)
        config.set("ssl.email", "ops@example.com")
        config.set("backup.max_per_app", 5)
        config.set("deploy.max_minutes", 30)

        assert policy.read(config) == OWN
        assert policy.is_customized(OWN) is True

    @pytest.mark.parametrize("field", sorted(OWN))
    def test_one_field_that_is_not_the_default_is_enough_to_be_customised(self, field: str) -> None:
        assert policy.is_customized({**DEFAULT_POLICY, field: OWN[field]}) is True

    def test_a_hand_edited_value_that_does_not_hold_is_read_as_the_default(
        self, config: Config
    ) -> None:
        # The key's own validator refuses it when it is written; a deployment must not stop for it.
        config._config["deploy"]["max_minutes"] = "soon"

        assert policy.read(config)["delivery_max_minutes"] == DEFAULT_POLICY["delivery_max_minutes"]

    def test_writing_it_goes_through_the_validators_of_each_key(self, config: Config) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as minutes:
            policy.write(config, {**OWN, "delivery_max_minutes": 5000})
        with pytest.raises(ValidationError) as kept:
            policy.write(config, {**OWN, "backup_max_per_app": 0})

        assert minutes.value.field == "delivery_max_minutes"
        assert "deploy.max_minutes must be between 0 and 1440" in minutes.value.message
        assert kept.value.field == "backup_max_per_app"

    def test_a_refused_write_leaves_the_configuration_as_it_was(self, config: Config) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            policy.write(config, {**OWN, "delivery_max_minutes": 5000})

        assert policy.read(config) == DEFAULT_POLICY

    def test_writing_it_changes_the_four_keys_and_nothing_else(self, config: Config) -> None:
        config.set("backup.directory", "/srv/backups")
        config.set("ssl.provider", "certbot")

        policy.write(config, OWN)

        assert policy.read(config) == OWN
        assert config.get("backup.directory") == "/srv/backups"
        assert config.get("ssl.provider") == "certbot"


class TestItIsTheKeysTheServerAlreadyReads:
    def test_each_field_is_the_key_the_server_reads_it_by(self) -> None:
        from noust.core.update_checker import CONFIG_KEY

        assert policy.KEYS["updates_check"] == CONFIG_KEY
        assert policy.KEYS["ssl_email"] == "ssl.email"
        assert policy.KEYS["backup_max_per_app"] == "backup.max_per_app"
        assert policy.KEYS["delivery_max_minutes"] == "deploy.max_minutes"
        assert policy.FIELDS == tuple(policy.KEYS)

    def test_the_defaults_are_what_the_servers_own_defaults_say(self) -> None:
        from noust.core.config import DEFAULT_CONFIG
        from noust.managers.backup_manager import BackupManager

        assert policy.defaults() == {
            "updates_check": DEFAULT_CONFIG["updates"]["check"],
            "ssl_email": DEFAULT_CONFIG["ssl"]["email"],
            "backup_max_per_app": BackupManager.DEFAULT_MAX_BACKUPS,
            "delivery_max_minutes": DEFAULT_CONFIG["deploy"]["max_minutes"],
        }


class TestWhatItFingerprints:
    """A node says a setting is its operator's own when what it holds no longer matches."""

    def test_it_holds_the_four_fields_of_the_policy_under_the_one_name(
        self, config: Config
    ) -> None:
        held = snapshots.take("policy", "general")

        assert held == DEFAULT_POLICY

    def test_a_name_that_is_not_general_is_not_a_policy_the_node_holds(
        self, config: Config
    ) -> None:
        assert snapshots.take("policy", "other") is None

    def test_a_change_to_a_field_of_the_policy_changes_what_it_holds(self, config: Config) -> None:
        before = snapshots.take("policy", "general")
        change(config, ssl__email="ops@example.com")

        assert snapshots.take("policy", "general") != before

    def test_a_change_to_what_belongs_to_the_machine_does_not(self, config: Config) -> None:
        before = snapshots.take("policy", "general")
        change(
            config,
            backup__directory="/srv/backups",
            deploy__max_concurrent_builds=4,
            deploy__queue_limit=50,
            webserver="apache",
            ssl__provider="certbot",
        )

        assert snapshots.take("policy", "general") == before


class Recorder:
    """A node as far as writing a setting goes: it answers what it is told to and keeps the calls."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, path: str, *, body: Any = None, params: Any = None) -> Any:
        self.calls.append((method, path, body))
        return self.answers.get((method, path), {})


class TestWritingItOnANode:
    def test_the_node_is_asked_whether_its_operator_chose_something(self) -> None:
        node = Recorder({("GET", POLICY_PATH): {**DEFAULT_POLICY, "customized": False}})

        assert find(node, "policy", "general") is None
        assert node.calls == [("GET", POLICY_PATH, None)]

    def test_what_the_operator_chose_is_what_the_node_has(self) -> None:
        node = Recorder({("GET", POLICY_PATH): {**OWN, "customized": True}})

        found = find(node, "policy", "general")

        assert found is not None
        assert found["ssl_email"] == "ops@example.com"

    def test_an_answer_that_says_nothing_of_it_is_taken_as_nothing_chosen(self) -> None:
        assert find(Recorder({}), "policy", "general") is None

    def test_it_is_written_by_one_call_to_the_policy_route(self) -> None:
        node = Recorder({})

        how = push(node, "policy", "general", OWN, None)

        assert how == "created"
        assert node.calls == [("PUT", POLICY_PATH, OWN)]

    def test_over_what_the_node_has_it_is_an_update(self) -> None:
        node = Recorder({})

        assert push(node, "policy", "general", OWN, {"name": "general", **OWN}) == "updated"


@pytest.fixture
def central(tmp_path: Path) -> Iterator[Central]:
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
    for name in built.nodes:
        FleetSettings(built.store, built.manager.secrets).clear_resync(name)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def nodes(central: Central) -> dict[str, ManagedNode]:
    return {name: ManagedNode(node) for name, node in central.nodes.items()}


@pytest.fixture
def settings(central: Central) -> FleetSettings:
    return FleetSettings(central.store, central.manager.secrets)


def save_policy(settings: FleetSettings, value: dict[str, Any] | None = None, **kw: Any) -> Any:
    return save(settings, "general", "policy", value=value or OWN, **kw)


class TestTheCentralAppliesIt:
    def test_a_node_of_this_version_is_written_by_its_own_route_and_records_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        saved = save_policy(settings)

        snapshot = apply(central)

        assert snapshot["status"] == "succeeded"
        for name, node in nodes.items():
            assert states(snapshot, name) == [("policy", "general", "created")]
            assert node.held[("policy", "general")] == OWN
            assert [(w.method, w.path) for w in node.writes] == [
                ("PUT", POLICY_PATH),
                ("PUT", "/api/managed-settings/policy/general"),
            ]
            assert node.rows[("policy", "general")]["version"] == saved.version
            assert node.unsigned == 0

    def test_a_second_look_finds_nothing_to_do(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        save_policy(settings)
        apply(central)

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("policy", "general", "in_sync")]

    def test_a_node_whose_operator_already_chose_something_is_not_overwritten(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        nodes["web-2"].hold("policy", "general", **{**OWN, "backup_max_per_app": 7})
        save_policy(settings)

        snapshot = apply(central)

        assert states(snapshot, "web-2") == [("policy", "general", "conflict")]
        assert nodes["web-2"].held[("policy", "general")]["backup_max_per_app"] == 7
        assert states(snapshot, "web-1") == [("policy", "general", "created")]

    def test_it_is_taken_over_when_the_person_says_so(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        nodes["web-2"].hold("policy", "general", **{**OWN, "backup_max_per_app": 7})
        save_policy(settings)

        snapshot = apply(central, adopt=True)

        assert states(snapshot, "web-2") == [("policy", "general", "updated")]
        assert nodes["web-2"].held[("policy", "general")] == OWN

    def test_an_exception_gives_one_server_its_own_policy(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        save_policy(settings)
        save_policy(settings, {**OWN, "delivery_max_minutes": 120}, scope="node:web-3")

        apply(central)

        assert nodes["web-1"].held[("policy", "general")]["delivery_max_minutes"] == 30
        assert nodes["web-3"].held[("policy", "general")]["delivery_max_minutes"] == 120

    def test_what_its_operator_changes_afterwards_is_its_own_exception(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        for node in nodes.values():
            node.offer_policy()
        save_policy(settings)
        apply(central)
        nodes["web-1"].edit_by_hand("policy", "general", ssl_email="me@example.com")

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("policy", "general", "overridden")]
        assert nodes["web-1"].held[("policy", "general")]["ssl_email"] == "me@example.com"
        assert states(apply(central, restore=["policy/general"]), "web-1") == [
            ("policy", "general", "restored")
        ]


class TestANodeOfAnOlderNoust:
    """A 4.2 node has neither the route nor the kind: it is not behind, it cannot, and the row stands."""

    def test_it_says_it_cannot_and_the_rest_of_its_row_goes_on(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        nodes["web-1"].offer_policy()
        save_policy(settings)
        save(settings, "ops", "channel")

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [
            ("channel", "ops", "created"),
            ("policy", "general", "created"),
        ]
        assert states(snapshot, "web-2") == [
            ("channel", "ops", "created"),
            ("policy", "general", "unsupported"),
        ]
        item = next(i for i in node_items(snapshot, "web-2") if i["resource"] == "policy")
        assert item["coded"]["code"] == "fleet_settings.item_unsupported"
        assert "GET /api/config/policy" in item["message"]
        assert ("policy", "general") not in nodes["web-2"].held
        assert nodes["web-2"].rows.keys() == {("channel", "ops")}

    def test_nothing_is_written_to_it_and_nothing_breaks(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save_policy(settings)

        snapshot = apply(central)

        assert snapshot["status"] == "succeeded"
        assert all(n.writes == [] for n in nodes.values())

    def test_the_status_says_it_cannot_and_does_not_count_it_as_following(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        nodes["web-1"].offer_policy()
        nodes["web-3"].offer_policy()
        save_policy(settings)
        apply(central, "web-1", "web-3")

        view = read(settings, asker=ADMIN, manager=central.manager)

        entry = view["settings"][0]
        cells = {c["node"]: c for c in entry["servers"]}
        assert [cells[n]["sync"] for n in ("web-1", "web-2", "web-3")] == [
            "in_sync",
            "unsupported",
            "in_sync",
        ]
        assert cells["web-2"]["waiting"] is False
        assert (entry["following"], entry["of"]) == (2, 3)

    def test_a_node_of_this_version_that_has_not_got_it_yet_is_pending_and_waiting(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        nodes["web-1"].offer_policy()
        save_policy(settings)

        cell = read(settings, asker=ADMIN, manager=central.manager)["settings"][0]["servers"][0]

        assert (cell["node"], cell["sync"], cell["waiting"]) == ("web-1", "pending", True)

    def test_it_is_still_given_the_rest_of_the_settings_by_the_fleet(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "ops", "channel")
        save(settings, "REGION", "variable", value={"value": "eu"})

        snapshot = apply(central)

        assert states(snapshot, "web-2") == [
            ("channel", "ops", "created"),
            ("variable", "REGION", "created"),
        ]


class TestTheKindIsAmongThoseTheCentralOffers:
    def test_it_can_be_set_with_the_permission_of_the_servers_own_route(
        self, settings: FleetSettings
    ) -> None:
        saved = settings.save(
            "policy", "general", OWN, person="carol", held=frozenset({"settings.manage"})
        )

        assert (saved.resource, saved.name, saved.secret_paths) == ("policy", "general", ())
        assert settings.shown(saved)["value"] == OWN

    def test_without_that_permission_it_cannot(self, settings: FleetSettings) -> None:
        from noust.core.exceptions import PermissionError as NoustPermissionError

        with pytest.raises(NoustPermissionError):
            settings.save("policy", "general", OWN, person="carol", held=frozenset({"apps.manage"}))

    def test_the_central_sets_it_with_the_permissions_the_tests_give_a_person(
        self, settings: FleetSettings
    ) -> None:
        assert save_policy(settings).version >= 1
        assert "settings.manage" in HELD
