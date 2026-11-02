# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
This server's part in Noust DNS (4.1, item 104).

The engine is the real one over a scripted runner and a temporary directory, so what is pinned
is what ends up on the machine: the configuration Knot is given, the firewall rules asked for
and remembered, the transfer key kept as a secret and nowhere else, and that a setup that fails
at any step leaves the machine as it found it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.authdns_store import (
    PRIMARY,
    SECONDARY,
    AuthDnsZone,
)
from noust.core.exceptions import PortConflictError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.engine import EngineError
from noust.managers.authdns.server import KEY_SECRET, TransferKey
from tests.support.authdns_support import PUBLIC, SECRET, World, make_world

pytestmark = pytest.mark.real_listeners


@pytest.fixture
def world(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    runner: FakeRunner,
    fs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    return make_world(tmp_path, store, secrets, runner, fs, monkeypatch)


class TestWhatThereIs:
    def test_before_it_is_set_up_it_offers_the_addresses_and_the_warnings(
        self, world: World
    ) -> None:
        status = world.servers.status()

        assert status.configured is False and status.installed is True
        assert status.version == "3.4.6"
        assert status.candidates == [PUBLIC]
        assert status.warnings == [
            "authdns.warning.availability",
            "authdns.warning.amplification",
        ]
        assert status.to_dict()["role"] is None

    def test_a_knot_somebody_else_configured_is_reported_not_touched(self, world: World) -> None:
        world.config.parent.mkdir(parents=True)
        world.config.write_text("zone:\n  - domain: customer.example\n")

        status = world.servers.status()

        assert status.foreign_zones == ["customer.example"]

    def test_there_is_no_key_before_a_setup(self, world: World) -> None:
        with pytest.raises(ValidationError):
            world.servers.transfer_key()


class TestAPrimary:
    def test_it_is_configured_started_opened_in_the_firewall_and_recorded(
        self, world: World, audited: list[dict[str, Any]]
    ) -> None:
        status = world.servers.setup(PRIMARY, hostname="NS1.example.net", actor="cli:root")

        assert (status.configured, status.role, status.hostname) == (
            True,
            PRIMARY,
            "ns1.example.net",
        )
        assert status.listen == [PUBLIC]
        assert world.parsed()["server"]["listen"] == [f"{PUBLIC}@53"]
        assert ["systemctl", "restart", "knot"] in world.commands()
        assert world.firewall.asked == [
            ("allow", 53, "udp", "noust dns"),
            ("allow", 53, "tcp", "noust dns"),
        ]
        assert status.firewall["made"] == [
            {"port": 53, "proto": "udp", "source": "any"},
            {"port": 53, "proto": "tcp", "source": "any"},
        ]
        assert [e["event"] for e in audited] == ["dns.server"]
        assert audited[0]["details"]["action"] == "setup"

    def test_its_key_is_made_once_kept_as_a_secret_and_is_the_one_in_the_configuration(
        self, world: World
    ) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        key = world.servers.transfer_key()

        assert key.name == "noust-xfr" and key.algorithm == "hmac-sha256"
        assert len(key.secret) == 44
        (configured,) = world.parsed()["key"]
        assert configured["secret"] == key.secret
        # The secret is in the secret store and the configuration, and in no column.
        with world.store._transaction() as cursor:
            cursor.execute("SELECT * FROM authdns_server")
            row = repr([tuple(r) for r in cursor.fetchall()])
        assert key.secret not in row
        # Setting up again keeps the key: a secondary already has it.
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        assert world.servers.transfer_key() == key

    def test_setting_it_up_again_does_not_open_the_firewall_twice(self, world: World) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        status = world.servers.setup(PRIMARY, hostname="ns1.example.net")

        assert len(status.firewall["made"]) == 2

    def test_it_answers_where_it_is_told_and_not_where_it_is_not(self, world: World) -> None:
        status = world.servers.setup(
            PRIMARY, hostname="ns1.example.net", listen=["198.51.100.1", "2001:DB8::1"]
        )

        assert status.listen == ["198.51.100.1", "2001:db8::1"]
        assert world.parsed()["server"]["listen"] == ["198.51.100.1@53", "2001:db8::1@53"]

    def test_with_no_address_to_take_it_asks_for_one(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(world.servers, "_addresses", lambda: [])

        with pytest.raises(ValidationError) as raised:
            world.servers.setup(PRIMARY, hostname="ns1.example.net")

        assert raised.value.field == "listen"
        assert "--listen" in raised.value.details
        assert not world.config.exists() and world.records.server() is None

    def test_an_address_something_else_answers_on_is_refused_before_anything_changes(
        self, world: World
    ) -> None:
        world.runner.script(
            ["ss", "-lunpH"],
            stdout=f'UNCONN 0 0 {PUBLIC}:53 0.0.0.0:* users:(("named",pid=7,fd=9))\n',
        )

        with pytest.raises(PortConflictError):
            world.servers.setup(PRIMARY, hostname="ns1.example.net")

        assert not world.config.exists() and world.firewall.asked == []
        assert world.records.server() is None

    def test_our_own_knot_on_the_port_is_not_in_its_way(self, world: World) -> None:
        world.runner.script(
            ["ss", "-lunpH"],
            stdout=f'UNCONN 0 0 {PUBLIC}:53 0.0.0.0:* users:(("knotd",pid=7,fd=9))\n',
        )

        assert world.servers.setup(PRIMARY, hostname="ns1.example.net").configured is True

    def test_a_name_that_is_not_a_host_name_is_refused(self, world: World) -> None:
        for bad in ("not a host", "ns1", "."):
            with pytest.raises(ValidationError):
                world.servers.setup(PRIMARY, hostname=bad)
        assert world.records.server() is None

    def test_an_unknown_role_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError):
            world.servers.setup("boss", hostname="ns1.example.net")


class TestASecondary:
    def test_it_needs_the_primary_and_its_key(self, world: World) -> None:
        with pytest.raises(ValidationError) as no_primary:
            world.servers.setup(
                SECONDARY,
                hostname="ns2.example.net",
                transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
            )
        with pytest.raises(ValidationError) as no_key:
            world.servers.setup(
                SECONDARY, hostname="ns2.example.net", primary_address="198.51.100.9"
            )

        assert no_primary.value.field == "primary_address"
        assert no_key.value.field == "transfer_key"
        assert world.records.server() is None

    def test_it_copies_from_the_primary_with_the_key_it_was_given(self, world: World) -> None:
        status = world.servers.setup(
            SECONDARY,
            hostname="ns2.example.net",
            primary_address="198.51.100.9",
            transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
        )

        loaded = world.parsed()
        assert status.role == SECONDARY and status.primary_address == "198.51.100.9"
        assert world.servers.transfer_key().secret == SECRET
        assert [z["domain"] for z in loaded["zone"]] == ["noust-catalog.invalid."]
        assert loaded["zone"][0]["catalog-role"] == "interpret"
        (remote,) = loaded["remote"]
        assert remote["address"] == "198.51.100.9@53"


class TestChangingTheRole:
    def test_a_server_with_zones_does_not_become_a_secondary(self, world: World) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        world.records.create_zone(AuthDnsZone("example.com", ("ns1.example.net",), "a@example.com"))

        with pytest.raises(ValidationError) as raised:
            world.servers.setup(
                SECONDARY,
                hostname="ns1.example.net",
                primary_address="198.51.100.9",
                transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
            )

        assert "Remove Noust DNS here first" in raised.value.details
        assert world.records.server() is not None and world.records.server().role == PRIMARY  # type: ignore[union-attr]


class TestWhenAStepFails:
    def test_a_configuration_knot_refuses_leaves_the_machine_as_it_was(self, world: World) -> None:
        world.runner.script(["knotc"], stderr="error: invalid item", exit_code=1)

        with pytest.raises(EngineError):
            world.servers.setup(PRIMARY, hostname="ns1.example.net")

        assert world.records.server() is None
        assert world.secrets.read(KEY_SECRET) is None
        assert world.firewall.asked == []
        assert not world.config.exists()

    def test_a_firewall_that_refuses_a_rule_closes_the_one_it_made_and_the_rest(
        self, world: World
    ) -> None:
        world.firewall.refuse = "tcp"

        with pytest.raises(Exception) as raised:
            world.servers.setup(PRIMARY, hostname="ns1.example.net")

        assert "ufw refused" in str(raised.value)
        assert ("remove", 53, "udp", "noust dns") in world.firewall.asked
        assert world.firewall.present == set()
        assert world.records.server() is None and world.secrets.read(KEY_SECRET) is None
        assert ["systemctl", "disable", "--now", "knot"] in world.commands()

    def test_a_failure_on_a_server_that_was_set_up_keeps_what_it_had(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        before = world.records.server()
        # The failed change happens a second later: what is put back keeps its own time of change.
        monkeypatch.setattr(
            "noust.core.authdns_store.utc_now_iso", lambda *_: "2099-01-01T00:00:00+00:00"
        )
        key = world.servers.transfer_key()
        world.firewall.refuse = "never"
        world.runner.script(["systemctl", "is-active"], stdout="active\n")
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "reload"], stderr="failed", exit_code=1
        )

        with pytest.raises(EngineError):
            world.servers.setup(PRIMARY, hostname="ns9.example.net", listen=[PUBLIC])

        assert world.records.server() == before
        assert world.servers.transfer_key() == key


class TestSecondariesOfAPrimary:
    @pytest.fixture(autouse=True)
    def _primary(self, world: World) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        world.runner.script(["systemctl", "is-active"], stdout="active\n")

    def test_adding_one_notifies_it_with_the_key(
        self, world: World, audited: list[dict[str, Any]]
    ) -> None:
        added = world.servers.add_secondary("198.51.100.7", "ns2.example.net", actor="api:alice")

        assert added.address == "198.51.100.7"
        loaded = world.parsed()
        (remote,) = loaded["remote"]
        assert remote["address"] == "198.51.100.7@53" and remote["key"] == "noust-xfr"
        assert loaded["template"][0]["notify"] == [remote["id"]]
        assert audited[-1]["details"]["action"] == "secondary_add"

    def test_one_the_server_does_not_take_is_not_added(self, world: World) -> None:
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "reload"], stderr="failed", exit_code=1
        )

        with pytest.raises(EngineError):
            world.servers.add_secondary("198.51.100.7", "ns2.example.net")

        assert world.records.secondaries() == []
        assert "remote" not in world.parsed()

    def test_removing_one_stops_the_notices(self, world: World) -> None:
        world.servers.add_secondary("198.51.100.7")

        assert world.servers.remove_secondary("198.51.100.7") is True
        assert world.servers.remove_secondary("198.51.100.7") is False

        assert "remote" not in world.parsed()

    def test_only_a_primary_has_secondaries(self, world: World) -> None:
        world.servers.remove(delete_zones=True)
        world.servers.setup(
            SECONDARY,
            hostname="ns2.example.net",
            primary_address="198.51.100.9",
            transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
        )

        with pytest.raises(ValidationError):
            world.servers.add_secondary("198.51.100.7")


class TestTakingItAway:
    def test_the_configuration_goes_back_the_rules_are_closed_and_the_key_is_forgotten(
        self, world: World, audited: list[dict[str, Any]]
    ) -> None:
        world.config.parent.mkdir(parents=True)
        world.config.write_text("# a sample\nserver:\n")
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        world.runner.script(["systemctl", "is-active"], stdout="active\n")

        world.servers.remove(actor="cli:root")

        assert world.config.read_text() == "# a sample\nserver:\n"
        assert world.firewall.present == set()
        assert world.records.server() is None
        assert world.secrets.read(KEY_SECRET) is None
        assert audited[-1]["details"] == {"action": "remove", "role": PRIMARY, "actor": "cli:root"}

    def test_a_server_with_zones_asks_to_be_told_to_delete_them(self, world: World) -> None:
        world.servers.setup(PRIMARY, hostname="ns1.example.net")
        world.records.create_zone(AuthDnsZone("example.com", ("ns1.example.net",), "a@example.com"))

        with pytest.raises(ValidationError) as raised:
            world.servers.remove()

        assert raised.value.field == "delete_zones"
        assert world.records.server() is not None
        world.servers.remove(delete_zones=True)
        assert world.records.zones() == [] and world.records.server() is None

    def test_taking_away_what_is_not_there_is_nothing(self, world: World) -> None:
        world.servers.remove()

        assert world.commands() == []
