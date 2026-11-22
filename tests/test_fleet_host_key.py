# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Accepting the new SSH host key of a server, from the central.

When a server presents another host key the central closes its tunnel and says so, and the only way out
was to remove the server and add it again. It is one decision now, made by a person who has seen both
fingerprints: the one pinned and the one the server presents, asked of the server's own address. The
central takes a key only when its fingerprint is the one the person gave, replaces the pinned line and
reopens the tunnel, and puts the old pin back when the server does not answer with the new one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import NodeError
from noust.core.store import NoustStore
from noust.fleet.keys import known_hosts_line
from noust.fleet.ssh_options import Bastion, NodeSSHOptions
from tests.support.fleet_support import HOST_KEY, build_fleet, ed25519_line, fingerprint

NEW_KEY = ed25519_line(2)
NEW_FINGERPRINT = fingerprint(NEW_KEY)
PINNED_FINGERPRINT = fingerprint(HOST_KEY)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    built.added()
    yield built
    NoustStore.reset_instance()


def presents(fleet, key: str) -> None:
    """Make the address answer with this host key."""
    fleet.runner.script(["ssh-keyscan"], stdout=f"web2.example.com {key}\n")


def pinned_file(fleet) -> str:
    return fleet.keys.known_hosts_path("web-2").read_text()


class TestWhatTheServerPresentsNow:
    def test_the_pinned_and_the_presented_fingerprints_are_both_given(self, fleet) -> None:
        presents(fleet, NEW_KEY)

        reading = fleet.manager.read_host_key("web-2")

        assert reading.node == "web-2"
        assert reading.pinned == PINNED_FINGERPRINT
        assert reading.presented == NEW_FINGERPRINT
        assert reading.changed is True
        assert reading.address == "root@web2.example.com:22"

    def test_a_server_that_presents_the_pinned_key_has_not_changed(self, fleet) -> None:
        presents(fleet, HOST_KEY)

        reading = fleet.manager.read_host_key("web-2")

        assert reading.changed is False
        assert reading.presented == reading.pinned

    def test_it_asks_the_servers_own_address_and_port_and_changes_nothing(self, fleet) -> None:
        presents(fleet, NEW_KEY)
        before = fleet.manager.get("web-2")

        fleet.manager.read_host_key("web-2")

        asked = [call for call in fleet.runner.calls if call[:1] == ("ssh-keyscan",)]
        assert asked and asked[-1][-2:] == ("22", "web2.example.com")
        assert fleet.manager.get("web-2") == before
        assert pinned_file(fleet) == known_hosts_line("web-2", HOST_KEY) + "\n"

    def test_nothing_that_answers_is_said_as_such(self, fleet) -> None:
        fleet.runner.script(["ssh-keyscan"], stdout="", stderr="connection refused")

        with pytest.raises(NodeError):
            fleet.manager.read_host_key("web-2")

    def test_a_server_that_is_not_registered_is_refused(self, fleet) -> None:
        with pytest.raises(NodeError):
            fleet.manager.read_host_key("nope")


class TestAccepting:
    def test_the_key_whose_fingerprint_was_given_becomes_the_pinned_one(self, fleet) -> None:
        presents(fleet, NEW_KEY)

        done = fleet.manager.accept_host_key("web-2", fingerprint=NEW_FINGERPRINT)

        assert done.changed is True
        assert (done.previous, done.presented) == (PINNED_FINGERPRINT, NEW_FINGERPRINT)
        assert fleet.manager.get("web-2").host_key == known_hosts_line("web-2", NEW_KEY)
        assert pinned_file(fleet) == known_hosts_line("web-2", NEW_KEY) + "\n"

    def test_the_tunnel_is_closed_and_opened_again_with_the_new_pin(self, fleet) -> None:
        presents(fleet, NEW_KEY)
        before = len(fleet.runner.processes)

        fleet.manager.accept_host_key("web-2", fingerprint=NEW_FINGERPRINT)

        assert len(fleet.runner.processes) == before + 1
        assert fleet.manager.get("web-2").status == "reachable"
        assert pinned_file(fleet) == known_hosts_line("web-2", NEW_KEY) + "\n"

    def test_a_fingerprint_that_is_not_what_answers_changes_nothing(self, fleet) -> None:
        presents(fleet, NEW_KEY)
        before = fleet.manager.get("web-2")

        with pytest.raises(NodeError) as caught:
            fleet.manager.accept_host_key("web-2", fingerprint=fingerprint(ed25519_line(3)))

        assert NEW_FINGERPRINT in (caught.value.message + caught.value.details)
        assert fleet.manager.get("web-2") == before
        assert pinned_file(fleet) == known_hosts_line("web-2", HOST_KEY) + "\n"

    @pytest.mark.parametrize(
        "given",
        [
            "",
            "SHA256:",
            "sha256:abc",
            "MD5:aa:bb",
            "SHA256:" + "A" * 80,
            "abc",
            "SHA256:a b",
            "x\ny",
        ],
    )
    def test_a_text_that_is_not_a_fingerprint_is_refused_before_anything_is_asked(
        self, fleet, given: str
    ) -> None:
        presents(fleet, NEW_KEY)
        fleet.runner.calls.clear()

        with pytest.raises(NodeError) as caught:
            fleet.manager.accept_host_key("web-2", fingerprint=given)

        assert caught.value.field == "fingerprint"
        assert not any(call[:1] == ("ssh-keyscan",) for call in fleet.runner.calls)

    def test_a_server_that_presents_the_key_already_pinned_has_nothing_to_accept(
        self, fleet
    ) -> None:
        presents(fleet, HOST_KEY)

        done = fleet.manager.accept_host_key("web-2", fingerprint=PINNED_FINGERPRINT)

        assert done.changed is False
        assert fleet.manager.get("web-2").host_key == known_hosts_line("web-2", HOST_KEY)

    def test_a_server_that_does_not_answer_with_the_new_key_gets_the_old_pin_back(
        self, fleet
    ) -> None:
        presents(fleet, NEW_KEY)
        before = fleet.manager.get("web-2")
        fleet.runner.script(
            ["ssh"],
            exit_code=255,
            stderr="ssh: connect to host web2.example.com port 22: Connection refused",
        )

        with pytest.raises(NodeError):
            fleet.manager.accept_host_key("web-2", fingerprint=NEW_FINGERPRINT)

        assert fleet.manager.get("web-2").host_key == before.host_key
        assert pinned_file(fleet) == known_hosts_line("web-2", HOST_KEY) + "\n"

    def test_a_server_that_dials_in_has_no_host_key_of_ours_to_accept(
        self, fleet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fleet.manager.links.records, "get", lambda name: object())

        with pytest.raises(NodeError):
            fleet.manager.accept_host_key("web-2", fingerprint=NEW_FINGERPRINT)
        with pytest.raises(NodeError):
            fleet.manager.read_host_key("web-2")

    def test_a_server_behind_a_bastion_cannot_be_asked_from_here_and_says_so(self, fleet) -> None:
        hop = Bastion(host="bastion.example.com", port=2222, user="hop", host_key=ed25519_line(7))
        NodeSSHOptions(fleet.store).configure("web-2", bastion=hop)
        presents(fleet, NEW_KEY)

        with pytest.raises(NodeError) as caught:
            fleet.manager.read_host_key("web-2")

        assert "bastion" in caught.value.message
        assert "noust node remove web-2" in caught.value.details
        assert not any(call[:1] == ("ssh-keyscan",) for call in fleet.runner.calls)
