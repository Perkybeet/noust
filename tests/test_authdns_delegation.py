# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Delegating a domain to Noust DNS: the steps, and the check (4.1, item 104).

The steps are sentences with a code (the console translates them) in the order that works: glue
first, then the name servers, then the DS and only then. The check asks the name servers directly
and the public resolvers, over a network that answers from a table, and says which step is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.authdns_store import PRIMARY
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.delegation import DelegationGuide, same_network
from noust.managers.authdns.zones import ZoneEditor
from tests.support.authdns_support import PUBLIC, World, make_world
from tests.support.dns_packets import FakeNetwork, Reply, ds_record, ns_record, soa_record

pytestmark = pytest.mark.real_listeners

SECOND = "198.51.100.7"
RESOLVER = "1.1.1.1"
DIGEST = "ebe5d6007fdf87e3b70fbc2e1afb0cff87e66a0d8f70cae380d4352472e83665"
DS_LINE = f"example.com. DS 9431 13 2 {DIGEST.upper()}\n"


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


@pytest.fixture
def editor(world: World) -> ZoneEditor:
    world.servers.setup(PRIMARY, hostname="ns1.example.net")
    world.servers.add_secondary(SECOND, "ns2.example.net")
    world.runner.script(["systemctl", "is-active"], stdout="active\n")
    return ZoneEditor(world.store, world.servers, clock=lambda: 1793266825, sleep=lambda _: None)


def healthy(zone: str = "example.com", serial: int = 7) -> FakeNetwork:
    """A network where both name servers serve the zone and the resolvers find them."""
    replies: dict[tuple[str, str, str], Reply | Exception] = {}
    for address in (PUBLIC, SECOND):
        replies[(address, zone, "SOA")] = Reply([soa_record(serial)], authoritative=True)
        replies[(address, "recursion-check.example.org", "A")] = Reply(rcode=5)
    replies[(RESOLVER, zone, "NS")] = Reply(
        [ns_record("ns1.example.net"), ns_record("ns2.example.net")], recursion_available=True
    )
    replies[(RESOLVER, zone, "DS")] = Reply([], recursion_available=True)
    return FakeNetwork(replies)


def guide(editor: ZoneEditor, network: FakeNetwork) -> DelegationGuide:
    return DelegationGuide(
        editor,
        resolvers=[RESOLVER],
        transport=network,
        resolve=lambda name: ([], None),
    )


class TestTheSteps:
    def test_an_unsigned_zone_with_outside_name_servers_is_two_steps(
        self, editor: ZoneEditor
    ) -> None:
        editor.create_zone("example.com")

        plan = guide(editor, healthy()).plan("example.com")

        assert [step["code"] for step in plan.steps] == [
            "authdns.delegation.nameservers",
            "authdns.delegation.check",
        ]
        first = plan.steps[0]
        assert first["params"] == {
            "zone": "example.com",
            "nameservers": "ns1.example.net, ns2.example.net",
        }
        assert first["text"] == (
            "At your registrar, set the name servers of example.com to ns1.example.net, ns2.example.net"
        )
        assert first["hint"] and plan.steps[1]["params"] == {
            "command": "noust dns zone check example.com"
        }
        assert plan.ds == () and plan.dnssec is False

    def test_name_servers_inside_the_zone_come_with_glue_first(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.net", nameservers=["ns1.example.net", "ns2.example.net"])

        plan = guide(editor, healthy()).plan("example.net")

        assert [step["code"] for step in plan.steps][:2] == [
            "authdns.delegation.glue",
            "authdns.delegation.nameservers",
        ]
        assert plan.steps[0]["params"] == {
            "hosts": "ns1.example.net, ns2.example.net",
            "addresses": f"ns1.example.net {PUBLIC}; ns2.example.net {SECOND}",
        }
        assert [(s.name, s.glue, s.addresses) for s in plan.nameservers] == [
            ("ns1.example.net", True, (PUBLIC,)),
            ("ns2.example.net", True, (SECOND,)),
        ]

    def test_a_signed_zone_adds_the_ds_after_the_name_servers(
        self, editor: ZoneEditor, world: World
    ) -> None:
        editor.create_zone("example.com")
        world.runner.script(["keymgr"], stdout=DS_LINE)
        editor.set_dnssec("example.com", True)

        plan = guide(editor, healthy()).plan("example.com")

        assert [step["code"] for step in plan.steps] == [
            "authdns.delegation.nameservers",
            "authdns.delegation.ds",
            "authdns.delegation.check",
        ]
        assert plan.steps[1]["params"]["ds"] == f"9431 13 2 {DIGEST}"
        assert plan.dnssec is True and [r.key_tag for r in plan.ds] == [9431]

    def test_a_zone_with_one_name_server_is_warned_about_it(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com", nameservers=["ns1.example.net"])

        plan = guide(editor, healthy()).plan("example.com")

        assert [w["code"] for w in plan.warnings] == ["authdns.delegation.one_server"]

    def test_two_name_servers_in_one_network_are_warned_about_it(self, editor: ZoneEditor) -> None:
        editor.servers.add_secondary("203.0.113.99", "ns3.example.net")
        editor.create_zone("example.com", nameservers=["ns1.example.net", "ns3.example.net"])

        plan = guide(editor, healthy()).plan("example.com")

        assert [w["code"] for w in plan.warnings] == ["authdns.delegation.same_network"]
        assert plan.warnings[0]["params"] == {
            "first": "ns1.example.net",
            "second": "ns3.example.net",
        }

    def test_a_name_server_the_server_does_not_know_is_resolved(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com", nameservers=["ns1.example.net", "ns9.example.org"])
        resolved: list[str] = []

        def resolve(name: str) -> tuple[list[str], str | None]:
            resolved.append(name)
            return ["192.0.2.99"], RESOLVER

        DelegationGuide(editor, resolvers=[RESOLVER], transport=healthy(), resolve=resolve).plan(
            "example.com"
        )

        assert resolved == ["ns9.example.org"]

    def test_the_networks_of_two_addresses(self) -> None:
        assert same_network("203.0.113.10", "203.0.113.99")
        assert not same_network("203.0.113.10", "203.0.114.10")
        assert same_network("2001:db8:1::1", "2001:db8:1:ffff::1")
        assert not same_network("2001:db8:1::1", "2001:db8:2::1")
        assert not same_network("203.0.113.10", "2001:db8::1")


class TestTheCheck:
    @pytest.fixture(autouse=True)
    def _zone(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com")

    def codes(self, editor: ZoneEditor, network: FakeNetwork) -> list[str]:
        return [p["code"] for p in guide(editor, network).check("example.com").problems]

    def test_a_delegation_in_place_is_ready(self, editor: ZoneEditor) -> None:
        network = healthy()

        result = guide(editor, network).check("example.com")

        assert result.ready is True and result.problems == []
        assert result.delegated is True
        assert result.public_nameservers == ["ns1.example.net", "ns2.example.net"]
        assert [
            (s.name, s.answers, s.authoritative, s.serial, s.refuses_recursion)
            for s in result.servers
        ] == [
            ("ns1.example.net", True, True, 7, True),
            ("ns2.example.net", True, True, 7, True),
        ]
        assert (RESOLVER, "example.com", "NS", True) in network.asked
        assert (PUBLIC, "example.com", "SOA", False) in network.asked
        assert result.to_dict()["ready"] is True

    def test_a_registrar_that_still_points_elsewhere_is_what_is_missing(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        network.replies[(RESOLVER, "example.com", "NS")] = Reply(
            [ns_record("ns.oldhost.example")], recursion_available=True
        )

        result = guide(editor, network).check("example.com")

        assert result.delegated is False and result.ready is False
        (problem,) = result.problems
        assert problem["code"] == "authdns.check.not_delegated"
        assert problem["params"] == {"zone": "example.com", "found": "ns.oldhost.example"}
        assert problem["text"] == (
            "Resolvers find ns.oldhost.example for example.com, not your name servers"
        )

    def test_a_domain_no_resolver_has_yet_is_not_delegated(self, editor: ZoneEditor) -> None:
        network = healthy()
        network.replies[(RESOLVER, "example.com", "NS")] = Reply([], recursion_available=True)

        result = guide(editor, network).check("example.com")

        assert result.delegated is False
        assert result.problems[0]["params"]["found"] == "no name servers"

    def test_resolvers_that_do_not_answer_are_said_not_guessed(self, editor: ZoneEditor) -> None:
        network = healthy()
        del network.replies[(RESOLVER, "example.com", "NS")]

        result = guide(editor, network).check("example.com")

        assert result.delegated is None and result.ready is False
        assert [p["code"] for p in result.problems] == ["authdns.check.no_public_answer"]

    def test_a_name_server_that_is_silent_names_the_address_to_open(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        del network.replies[(SECOND, "example.com", "SOA")]

        result = guide(editor, network).check("example.com")

        (problem,) = result.problems
        assert problem["code"] == "authdns.check.silent"
        assert problem["params"] == {
            "server": "ns2.example.net",
            "address": SECOND,
            "zone": "example.com",
        }
        assert SECOND in problem["hint"]

    def test_a_server_that_answers_but_not_as_the_authority_is_a_different_problem(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        network.replies[(SECOND, "example.com", "SOA")] = Reply(rcode=5)

        assert self.codes(editor, network) == ["authdns.check.not_authoritative"]

    def test_a_server_that_recurses_is_called_out(self, editor: ZoneEditor) -> None:
        network = healthy()
        network.replies[(PUBLIC, "recursion-check.example.org", "A")] = Reply(
            [("A", bytes([1, 2, 3, 4]))], recursion_available=True
        )

        assert self.codes(editor, network) == ["authdns.check.recurses"]

    def test_a_secondary_behind_the_primary_is_named_with_both_serials(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        network.replies[(SECOND, "example.com", "SOA")] = Reply([soa_record(5)], authoritative=True)

        result = guide(editor, network).check("example.com")

        (problem,) = result.problems
        assert problem["code"] == "authdns.check.out_of_sync"
        assert problem["params"] == {
            "server": "ns2.example.net",
            "serial": 5,
            "zone": "example.com",
            "primary": "ns1.example.net",
            "primary_serial": 7,
        }


class TestTheCheckOfASignedZone:
    @pytest.fixture(autouse=True)
    def _signed(self, editor: ZoneEditor, world: World) -> None:
        editor.create_zone("example.com")
        world.runner.script(["keymgr"], stdout=DS_LINE)
        editor.set_dnssec("example.com", True)

    def test_the_ds_in_the_parent_that_is_ours_makes_it_ready(self, editor: ZoneEditor) -> None:
        network = healthy()
        network.replies[(RESOLVER, "example.com", "DS")] = Reply(
            [ds_record(9431, 13, 2, DIGEST)], recursion_available=True
        )

        result = guide(editor, network).check("example.com")

        assert (result.ds_published, result.ds_matches, result.ready) == (True, True, True)

    def test_no_ds_in_the_parent_is_the_step_that_is_missing(self, editor: ZoneEditor) -> None:
        result = guide(editor, healthy()).check("example.com")

        assert result.ds_published is False and result.ready is False
        assert [p["code"] for p in result.problems] == ["authdns.check.ds_missing"]

    def test_a_ds_that_is_not_the_key_of_the_zone_is_to_be_replaced(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        network.replies[(RESOLVER, "example.com", "DS")] = Reply(
            [ds_record(1111, 13, 2, "ab" * 32)], recursion_available=True
        )

        result = guide(editor, network).check("example.com")

        assert (result.ds_published, result.ds_matches) == (True, False)
        assert [p["code"] for p in result.problems] == ["authdns.check.ds_mismatch"]

    def test_the_ds_is_not_asked_to_be_there_before_the_name_servers_changed(
        self, editor: ZoneEditor
    ) -> None:
        network = healthy()
        network.replies[(RESOLVER, "example.com", "NS")] = Reply(
            [ns_record("ns.oldhost.example")], recursion_available=True
        )

        codes = [p["code"] for p in guide(editor, network).check("example.com").problems]

        assert codes == ["authdns.check.not_delegated"]


def test_a_ds_left_at_the_registrar_of_a_zone_that_is_not_signed_is_the_worst_state(
    editor: ZoneEditor,
) -> None:
    editor.create_zone("example.com")
    network = healthy()
    network.replies[(RESOLVER, "example.com", "DS")] = Reply(
        [ds_record(9431, 13, 2, DIGEST)], recursion_available=True
    )

    result = guide(editor, network).check("example.com")

    assert [p["code"] for p in result.problems] == ["authdns.check.ds_dangling"]
    assert result.ready is False
