# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A central moves the copies of a server over a connection of its own (item 195).

The tunnel the console and its events use carries a request at a time that people wait for. A copy
of several gigabytes read through it would sit in front of them: both share one ssh connection and
its window. So collecting asks for a second tunnel to the same server, the same kind and held to the
same restrictions (the key, the options, the one port it may reach), started as another ssh and
closed with the first. What is pinned: it is another process on another local port with the same
command line; the console's tunnel is not touched by it opening, failing or closing; and a server
that dials in, which has one forward, has one connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.store import NodeRecord
from noust.fleet.client import NodeClient
from noust.fleet.keys import known_hosts_line
from noust.fleet.tunnels import TRANSFER
from tests.support.fleet_support import HOST_KEY, build_fleet


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    return build_fleet(tmp_path)


def register(fleet, name: str = "web-2") -> NodeRecord:
    return fleet.store.save_node(
        NodeRecord(
            name=name,
            ssh_host="web2.example.com",
            ssh_port=2222,
            ssh_user="root",
            host_key=known_hosts_line(name, HOST_KEY),
            console_port=8080,
        )
    )


def without_the_local_port(argv: list[str]) -> list[str]:
    """What a command line says with the local end of its ``-L`` taken out."""
    out = list(argv)
    index = out.index("-L")
    host, _local, rest = out[index + 1].split(":", 2)
    out[index + 1] = f"{host}:*:{rest}"
    return out


class TestASecondConnection:
    def test_it_is_another_ssh_on_another_local_port(self, fleet) -> None:
        register(fleet)

        console = fleet.tunnels.endpoint("web-2")
        transfer = fleet.tunnels.endpoint("web-2", channel=TRANSFER)

        assert console != transfer
        assert len(fleet.runner.processes) == 2
        assert fleet.runner.processes[0] is not fleet.runner.processes[1]

    def test_the_command_line_is_the_same_but_for_the_local_port(self, fleet) -> None:
        register(fleet)

        fleet.tunnels.endpoint("web-2")
        fleet.tunnels.endpoint("web-2", channel=TRANSFER)
        first, second = (p.argv for p in fleet.runner.processes)

        assert without_the_local_port(first) == without_the_local_port(second)
        # The one port it may reach is the console's, and nothing is forwarded back.
        assert "127.0.0.1:8080" in second[second.index("-L") + 1]
        assert "-R" not in second

    def test_each_is_reused_while_it_lives(self, fleet) -> None:
        register(fleet)
        console = fleet.tunnels.endpoint("web-2")
        transfer = fleet.tunnels.endpoint("web-2", channel=TRANSFER)

        assert fleet.tunnels.endpoint("web-2") == console
        assert fleet.tunnels.endpoint("web-2", channel=TRANSFER) == transfer
        assert len(fleet.runner.processes) == 2

    def test_an_unknown_channel_is_refused(self, fleet) -> None:
        register(fleet)

        with pytest.raises(NodeError):
            fleet.tunnels.endpoint("web-2", channel="bulk")


class TestTheConsolesTunnelIsNotTouched:
    def test_the_transfer_failing_to_open_leaves_the_console_up_and_unmarked(self, fleet) -> None:
        register(fleet)
        console = fleet.tunnels.endpoint("web-2")
        # The next ssh to start is the transfer's, and it never listens.
        fleet.probe_answers[50001] = False

        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2", channel=TRANSFER)

        assert fleet.tunnels.endpoint("web-2") == console
        status = fleet.tunnels.status("web-2")
        assert status["open"] and status["failures"] == 0

    def test_the_console_dying_leaves_the_transfer_up(self, fleet) -> None:
        register(fleet)
        fleet.tunnels.endpoint("web-2")
        transfer = fleet.tunnels.endpoint("web-2", channel=TRANSFER)
        fleet.runner.processes[0]._alive = False

        assert fleet.tunnels.endpoint("web-2", channel=TRANSFER) == transfer

    def test_the_status_of_a_node_is_the_consoles_and_the_transfers_is_asked_for(
        self, fleet
    ) -> None:
        register(fleet)
        fleet.tunnels.endpoint("web-2", channel=TRANSFER)

        assert fleet.tunnels.status("web-2")["open"] is False
        assert fleet.tunnels.status("web-2", channel=TRANSFER)["open"] is True


class TestClosing:
    def test_closing_a_node_closes_both(self, fleet) -> None:
        register(fleet)
        register(fleet, "web-3")
        fleet.tunnels.endpoint("web-2")
        fleet.tunnels.endpoint("web-2", channel=TRANSFER)
        fleet.tunnels.endpoint("web-3")

        fleet.tunnels.close("web-2")

        assert fleet.runner.processes[0].terminated and fleet.runner.processes[1].terminated
        assert not fleet.runner.processes[2].terminated

    def test_close_all_closes_every_one(self, fleet) -> None:
        register(fleet)
        fleet.tunnels.endpoint("web-2")
        fleet.tunnels.endpoint("web-2", channel=TRANSFER)

        fleet.tunnels.close_all()

        assert all(process.terminated for process in fleet.runner.processes)

    def test_an_idle_transfer_is_closed_like_any_tunnel_and_a_leased_one_is_not(
        self, fleet
    ) -> None:
        register(fleet)
        with fleet.tunnels.lease("web-2", channel=TRANSFER):
            fleet.clock.advance(fleet.tunnels.idle_seconds * 3)
            assert fleet.tunnels.reap_idle() == []
        fleet.clock.advance(fleet.tunnels.idle_seconds + 1)

        assert fleet.tunnels.reap_idle() == [f"web-2/{TRANSFER}"]


class TestTheClient:
    def test_a_client_for_the_transfer_asks_for_that_connection(self, fleet) -> None:
        register(fleet)
        fleet.tunnels.endpoint("web-2")

        client = NodeClient("web-2", tunnels=fleet.tunnels, channel=TRANSFER)

        assert (
            client.base_url()
            == f"http://127.0.0.1:{fleet.tunnels.endpoint('web-2', channel=TRANSFER)[1]}"
        )
        assert client.base_url() != NodeClient("web-2", tunnels=fleet.tunnels).base_url()


class TestAServerThatDialsIn:
    def test_it_has_one_forward_and_so_one_connection(self, fleet, monkeypatch) -> None:
        register(fleet)
        monkeypatch.setattr(
            type(fleet.tunnels),
            "_link_endpoint",
            lambda self, node, link: ("127.0.0.1", 47000),
        )
        monkeypatch.setattr(type(fleet.tunnels), "_link_of", lambda self, node: object())

        assert fleet.tunnels.endpoint("web-2", channel=TRANSFER) == ("127.0.0.1", 47000)
        assert fleet.tunnels.endpoint("web-2") == ("127.0.0.1", 47000)
        assert fleet.runner.processes == []
