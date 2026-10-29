# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust dns server`` and ``noust dns zone``.

The managers decide (``tests/test_authdns_server.py``, ``test_authdns_zones.py``,
``test_authdns_delegation.py``). Pinned here is the translation and what the operator is told: the
two costs of running a name server are said before one is set up, the transfer key travels on
standard input and never in an argument, a signed zone is not unsigned without saying the DS is
gone, the steps at the registrar are numbered sentences, and a check that finds something missing
is exit code 1 with the sentence and its fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.delegation import DelegationGuide
from noust.managers.authdns.zones import ZoneEditor
from tests.support.authdns_support import PUBLIC, SECRET, World, make_world
from tests.support.dns_packets import FakeNetwork, Reply, ns_record, soa_record

pytestmark = pytest.mark.real_listeners

SECOND = "198.51.100.7"
DS_LINE = (
    "example.com. DS 9431 13 2 EBE5D6007FDF87E3B70FBC2E1AFB0CFF87E66A0D8F70CAE380D4352472E83665\n"
)


@pytest.fixture
def world(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    runner: FakeRunner,
    fs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    built = make_world(tmp_path, store, secrets, runner, fs, monkeypatch)
    editor = ZoneEditor(store, built.servers, clock=lambda: 1793266825, sleep=lambda _: None)
    network = FakeNetwork(
        {
            **{
                (a, "example.com", "SOA"): Reply([soa_record(7)], authoritative=True)
                for a in (PUBLIC, SECOND)
            },
            **{(a, "recursion-check.example.org", "A"): Reply(rcode=5) for a in (PUBLIC, SECOND)},
            ("1.1.1.1", "example.com", "NS"): Reply(
                [ns_record("ns1.example.net"), ns_record("ns2.example.net")],
                recursion_available=True,
            ),
            ("1.1.1.1", "example.com", "DS"): Reply([], recursion_available=True),
        }
    )
    guide = DelegationGuide(
        editor, resolvers=["1.1.1.1"], transport=network, resolve=lambda n: ([], None)
    )
    monkeypatch.setattr("noust.cli.commands.dns_server.AuthDnsServers", lambda: built.servers)
    monkeypatch.setattr("noust.cli.commands.dns_zone.ZoneEditor", lambda: editor)
    monkeypatch.setattr("noust.cli.commands.dns_zone.DelegationGuide", lambda: guide)
    built.network = network  # type: ignore[attr-defined]
    return built


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, ["dns", *args], input=input_text)


def refused(args: list[str], log: list[str]) -> str:
    """Run a command that must be refused, and return what it said."""
    log.clear()
    assert main(["dns", *args]) == 1
    return "\n".join(log)


def as_json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestTheServer:
    def test_before_it_is_set_up_it_says_how(self, world: World) -> None:
        result = invoke(["server", "status"])

        assert result.exit_code == 0
        assert "Set up" in result.output and "no" in result.output
        assert PUBLIC in result.output
        assert "noust dns server setup --role primary" in result.output

    def test_the_two_costs_are_said_before_a_name_server_is_set_up(self, world: World) -> None:
        result = invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])

        assert result.exit_code == 0, result.output
        first = result.output.index("the whole internet depends on")
        second = result.output.index("flood other people with traffic")
        done = result.output.index("This server is a primary of Noust DNS")
        assert first < done and second < done
        assert "Add a secondary: noust dns server add-secondary" in result.output
        assert world.records.server() is not None

    def test_the_state_is_json_with_the_warnings_as_codes(self, world: World) -> None:
        data = as_json(
            invoke(
                ["server", "setup", "--role", "primary", "--hostname", "ns1.example.net", "--json"]
            )
        )
        status = as_json(invoke(["server", "status", "--json"]))

        assert data["role"] == "primary" and data["listen"] == [PUBLIC]
        assert status["warnings"] == [
            "authdns.warning.availability",
            "authdns.warning.amplification",
        ]

    def test_the_key_is_printed_alone_for_a_pipe_and_a_secondary_reads_it_from_standard_input(
        self, world: World
    ) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        key = invoke(["server", "key"]).output.strip()
        assert len(key) == 44 and " " not in key
        assert as_json(invoke(["server", "key", "--json"]))["secret"] == key

        # A fresh server: the secondary of that primary.
        world.servers.remove(delete_zones=True)
        result = invoke(
            [
                "server",
                "setup",
                "--role",
                "secondary",
                "--hostname",
                "ns2.example.net",
                "--primary",
                "198.51.100.9",
                "--key-stdin",
            ],
            input_text=SECRET + "\n",
        )

        assert result.exit_code == 0, result.output
        assert world.servers.transfer_key().secret == SECRET
        assert SECRET not in result.output

    def test_a_secondary_with_no_key_on_standard_input_is_a_usage_error(self, world: World) -> None:
        result = invoke(
            ["server", "setup", "--role", "secondary", "--primary", "198.51.100.9", "--key-stdin"],
            input_text="",
        )

        assert result.exit_code != 0 and "Nothing was read" in result.output

    def test_a_rehearsal_changes_nothing(self, world: World) -> None:
        result = CliRunner().invoke(
            root_cli, ["--dry-run", "dns", "server", "setup", "--role", "primary"]
        )

        assert result.exit_code == 0 and "Would set this server up as a primary" in result.output
        assert world.records.server() is None

    def test_secondaries_are_added_and_removed(self, world: World) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        world.runner.script(["systemctl", "is-active"], stdout="active\n")

        added = as_json(
            invoke(["server", "add-secondary", SECOND, "--hostname", "ns2.example.net", "--json"])
        )
        gone = invoke(["server", "remove-secondary", SECOND])
        again = invoke(["server", "remove-secondary", SECOND])

        assert added["address"] == SECOND and added["hostname"] == "ns2.example.net"
        assert "no longer gets" in gone.output and "was not a secondary" in again.output

    def test_removing_a_server_with_zones_asks_for_the_flag_by_name(
        self, world: World, log: list[str]
    ) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        world.runner.script(["systemctl", "is-active"], stdout="active\n")
        invoke(["zone", "create", "example.com"])

        said = refused(["server", "remove"], log)
        removed = invoke(["server", "remove", "--delete-zones"])

        assert "--delete-zones" in said
        assert removed.exit_code == 0 and world.records.server() is None


class TestTheZones:
    @pytest.fixture(autouse=True)
    def _primary(self, world: World) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        world.servers.add_secondary(SECOND, "ns2.example.net")
        world.runner.script(["systemctl", "is-active"], stdout="active\n")

    def test_a_zone_is_made_listed_shown_changed_and_deleted(self, world: World) -> None:
        created = as_json(invoke(["zone", "create", "example.com", "--json"]))
        listed = as_json(invoke(["zone", "list", "--json"]))
        text = invoke(["zone", "show", "example.com"])
        changed = as_json(invoke(["zone", "set", "example.com", "--ttl", "600", "--json"]))
        declined = invoke(["zone", "delete", "example.com"], input_text="n\n")
        kept = world.records.zone("example.com") is not None
        deleted = invoke(["zone", "delete", "example.com", "--yes"])

        assert created["name"] == "example.com"
        assert created["nameservers"] == ["ns1.example.net", "ns2.example.net"]
        assert [z["name"] for z in listed["zones"]] == ["example.com"]
        assert "ns1.example.net, ns2.example.net" in text.output
        assert changed["default_ttl"] == 600
        assert declined.exit_code != 0 and kept
        assert "example.com is deleted" in deleted.output
        assert "serves no zone" in invoke(["zone", "list"]).output

    def test_a_glue_pair_that_is_not_one_is_a_usage_error(self, world: World) -> None:
        result = invoke(["zone", "create", "example.com", "--glue", "ns9.example.com"])

        assert result.exit_code != 0 and "name=address" in result.output
        assert world.records.zones() == []

    def test_records_are_added_changed_and_removed_by_number(self, world: World) -> None:
        invoke(["zone", "create", "example.com"])

        added = as_json(
            invoke(
                [
                    "zone",
                    "record",
                    "add",
                    "example.com",
                    "www",
                    "a",
                    "192.0.2.1",
                    "--ttl",
                    "300",
                    "--json",
                ]
            )
        )
        changed = as_json(
            invoke(
                [
                    "zone",
                    "record",
                    "set",
                    "example.com",
                    str(added["id"]),
                    "--content",
                    "192.0.2.9",
                    "--json",
                ]
            )
        )
        shown = invoke(["zone", "show", "example.com"])
        removed = invoke(["zone", "record", "remove", "example.com", str(added["id"])])
        again = invoke(["zone", "record", "remove", "example.com", str(added["id"])])

        assert (added["name"], added["type"], added["ttl"]) == ("www", "A", 300)
        assert changed["content"] == "192.0.2.9" and changed["ttl"] == 300
        assert "192.0.2.9" in shown.output
        assert "is removed" in removed.output and "There was no record" in again.output

    def test_changing_nothing_is_asked_what_to_change(self, world: World, log: list[str]) -> None:
        invoke(["zone", "create", "example.com"])
        record = world.records.add_record("example.com", "www", "A", "192.0.2.1")

        said = refused(["zone", "record", "set", "example.com", str(record.id)], log)

        assert "Say what to change" in said

    def test_a_record_that_does_not_fit_is_refused_with_the_reason(
        self, world: World, log: list[str]
    ) -> None:
        invoke(["zone", "create", "example.com"])
        invoke(["zone", "record", "add", "example.com", "go", "CNAME", "target.example.net"])

        said = refused(["zone", "record", "add", "example.com", "go", "TXT", "hi"], log)

        assert "a CNAME stands alone" in said


class TestDnssec:
    @pytest.fixture(autouse=True)
    def _signed_zone(self, world: World) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        world.servers.add_secondary(SECOND, "ns2.example.net")
        world.runner.script(["systemctl", "is-active"], stdout="active\n")
        invoke(["zone", "create", "example.com"])
        world.runner.script(["keymgr"], stdout=DS_LINE)

    def test_enabling_prints_the_ds_to_give_the_registrar(self, world: World) -> None:
        result = invoke(["zone", "dnssec", "example.com", "--enable"])

        assert result.exit_code == 0, result.output
        assert "yes (ECDSAP256SHA256)" in result.output
        assert "9431 13 2 ebe5d600" in result.output
        assert "noust dns zone delegation example.com" in result.output

    def test_disabling_without_saying_the_ds_is_gone_is_refused(
        self, world: World, log: list[str]
    ) -> None:
        invoke(["zone", "dnssec", "example.com", "--enable"])

        said = refused(["zone", "dnssec", "example.com", "--disable"], log)
        done = invoke(["zone", "dnssec", "example.com", "--disable", "--ds-removed", "--json"])

        assert "--ds-removed" in said
        assert json.loads(done.output)["enabled"] is False

    def test_the_state_without_a_flag_changes_nothing(self, world: World) -> None:
        result = invoke(["zone", "dnssec", "example.com", "--json"])

        assert json.loads(result.output) == {
            "zone": "example.com",
            "enabled": False,
            "algorithm": "ECDSAP256SHA256",
            "ds": [],
        }

    def test_telling_the_server_the_registrar_has_the_ds(self, world: World) -> None:
        invoke(["zone", "dnssec", "example.com", "--enable"])

        result = invoke(["zone", "dnssec", "example.com", "--ds-published"])

        assert result.exit_code == 0
        assert [
            "knotc",
            "-c",
            str(world.config),
            "zone-ksk-submitted",
            "example.com.",
        ] in world.commands()


class TestTheDelegation:
    @pytest.fixture(autouse=True)
    def _zone(self, world: World) -> None:
        invoke(["server", "setup", "--role", "primary", "--hostname", "ns1.example.net"])
        world.servers.add_secondary(SECOND, "ns2.example.net")
        world.runner.script(["systemctl", "is-active"], stdout="active\n")
        invoke(["zone", "create", "example.com"])

    def test_the_steps_are_numbered_sentences_with_their_fix(self, world: World) -> None:
        result = invoke(["zone", "delegation", "example.com"])

        assert result.exit_code == 0
        assert (
            "1. At your registrar, set the name servers of example.com to ns1.example.net, ns2.example.net"
            in result.output
        )
        assert "2. Check that resolvers find your name servers" in result.output
        assert "Run: noust dns zone check example.com" in result.output

    def test_the_steps_as_json_carry_their_codes(self, world: World) -> None:
        data = as_json(invoke(["zone", "delegation", "example.com", "--json"]))

        assert [s["code"] for s in data["steps"]] == [
            "authdns.delegation.nameservers",
            "authdns.delegation.check",
        ]
        assert data["nameservers"][0] == {"name": "ns1.example.net", "addresses": [], "glue": False}

    def test_a_delegation_in_place_exits_zero(self, world: World) -> None:
        result = invoke(["zone", "check", "example.com"])

        assert result.exit_code == 0, result.output
        assert "example.com is delegated to this server" in result.output

    def test_what_is_missing_is_exit_one_with_the_sentence_and_its_fix(self, world: World) -> None:
        network: FakeNetwork = world.network  # type: ignore[attr-defined]
        network.replies[("1.1.1.1", "example.com", "NS")] = Reply(
            [ns_record("ns.old.example")], recursion_available=True
        )

        result = invoke(["zone", "check", "example.com"])

        assert result.exit_code == 1
        assert (
            "Resolvers find ns.old.example for example.com, not your name servers" in result.output
        )
        assert "wait for its TTL to pass" in result.output

    def test_the_check_as_json_has_the_same_answer(self, world: World) -> None:
        result = invoke(["zone", "check", "example.com", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output)["ready"] is True
