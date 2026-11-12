# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How Noust reaches what it deployed when it does not share the machine's loopback.

On a server a deployment answers on 127.0.0.1 and the health gate asks it there. In the container of a NAS
that address is the container's own: the application Noust deployed is in another container, on the
machine's Docker, and 127.0.0.1 reaches nothing. So Noust and what it deploys share a Docker network, and
the probe goes to the service by its address on that network and by its own port, not the one published on
the machine. What is held here is how that address is found (which network, Noust's own container joining it
once and never twice), that a central that shares the machine's network (``network_mode: host``) is asked
nothing and keeps 127.0.0.1, and that every way it can fail says what it was looking for.
"""

from __future__ import annotations

import json

import pytest

from noust.core.docker import reach
from noust.core.exceptions import DockerError
from noust.core.net import ALL_INTERFACES
from noust.core.runner import FakeRunner

ME = "a" * 64
APP = "b" * 64
STACK_NETWORK = "shop-example-com_default"


def networks(**named: str) -> str:
    """What ``docker inspect`` prints for a container's networks: name to an entry with its address."""
    return json.dumps({name: {"IPAddress": address} for name, address in named.items()})


def docker(
    *,
    mine: str | None = None,
    theirs: str | None = None,
    mode: str = "bridge",
    join_error: str = "",
) -> FakeRunner:
    """A Docker that knows Noust's own container (``ME``) and the one deployed (``APP``)."""
    runner = FakeRunner()
    runner.script(
        ["docker", "inspect", "--type", "container", "--format", reach.MODE_FORMAT, ME], stdout=mode
    )
    runner.script(
        ["docker", "inspect", "--type", "container", "--format", reach.NETWORKS_FORMAT, ME],
        stdout=mine if mine is not None else networks(bridge="172.17.0.2"),
    )
    runner.script(
        ["docker", "inspect", "--type", "container", "--format", reach.NETWORKS_FORMAT, APP],
        stdout=theirs if theirs is not None else networks(**{STACK_NETWORK: "172.18.0.3"}),
    )
    runner.script(
        ["docker", "network", "connect"],
        stderr=join_error,
        exit_code=1 if join_error else 0,
    )
    return runner


def connects(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:3] == ("docker", "network", "connect")]


class TestWhoNoustIs:
    def test_the_container_is_found_by_the_folder_docker_keeps_its_files_in(self) -> None:
        mountinfo = (
            "1213 1204 8:1 /var/lib/docker/containers/"
            f"{ME}/hostname /etc/hostname rw,relatime - ext4 /dev/sda1 rw\n"
        )

        assert reach.own_container_id(mountinfo=mountinfo, hostname="whatever") == ME

    def test_without_that_the_host_name_is_the_short_id_docker_gives_by_default(self) -> None:
        assert reach.own_container_id(mountinfo="", hostname=ME[:12]) == ME[:12]

    def test_a_host_name_somebody_chose_is_not_an_id_and_so_nobody_is_known(self) -> None:
        assert reach.own_container_id(mountinfo="", hostname="nas-central") is None

    def test_a_process_that_is_not_in_a_container_is_nobody(self) -> None:
        assert reach.own_container_id(mountinfo="25 1 0:22 / /proc rw - proc proc rw\n") is None


class TestTheAddress:
    def test_noust_joins_the_network_of_the_stack_and_asks_the_service_by_its_address(self) -> None:
        runner = docker()

        address = reach.service_address(runner, APP, me=ME)

        assert address == "172.18.0.3"
        assert connects(runner) == [("docker", "network", "connect", STACK_NETWORK, ME)]

    def test_a_network_noust_is_already_on_is_not_joined_again(self) -> None:
        runner = docker(mine=networks(bridge="172.17.0.2", **{STACK_NETWORK: "172.18.0.9"}))

        assert reach.service_address(runner, APP, me=ME) == "172.18.0.3"
        assert connects(runner) == []

    def test_the_network_a_person_made_is_preferred_to_the_default_bridge(self) -> None:
        runner = docker(theirs=networks(bridge="172.17.0.5", backend="172.20.0.3"))

        assert reach.service_address(runner, APP, me=ME) == "172.20.0.3"
        assert connects(runner) == [("docker", "network", "connect", "backend", ME)]

    def test_the_default_bridge_is_used_when_it_is_all_there_is(self) -> None:
        runner = docker(theirs=networks(bridge="172.17.0.5"))

        assert reach.service_address(runner, APP, me=ME) == "172.17.0.5"
        # Noust is on it already: it was started on the default bridge.
        assert connects(runner) == []

    def test_a_central_on_the_machines_own_network_keeps_asking_loopback(self) -> None:
        runner = docker(mode="host")

        assert reach.service_address(runner, APP, me=ME) is None
        assert connects(runner) == []

    def test_a_container_with_no_address_on_any_network_says_what_it_looked_for(self) -> None:
        with pytest.raises(DockerError) as caught:
            reach.service_address(docker(theirs=networks(**{STACK_NETWORK: ""})), APP, me=ME)

        assert "network" in caught.value.message.lower()
        assert APP[:12] in caught.value.message

    def test_a_network_that_cannot_be_joined_says_what_docker_said(self) -> None:
        runner = docker(join_error="Error response from daemon: network is internal")

        with pytest.raises(DockerError) as caught:
            reach.service_address(runner, APP, me=ME)

        assert "network is internal" in caught.value.details

    def test_a_race_that_joined_it_first_is_not_a_failure(self) -> None:
        runner = docker(join_error="endpoint with name noust already exists in network x")

        assert reach.service_address(runner, APP, me=ME) == "172.18.0.3"

    def test_a_central_that_does_not_know_which_container_it_is_says_so_before_asking_docker(
        self,
    ) -> None:
        runner = docker()

        with pytest.raises(DockerError, match="which container"):
            reach.service_address(runner, APP, me=None)

        assert runner.calls == []


def ports(runner: FakeRunner, shown: dict[str, object] | None) -> FakeRunner:
    runner.script(
        ["docker", "inspect", "--type", "container", "--format", reach.PORTS_FORMAT, APP],
        stdout=json.dumps(shown),
    )
    return runner


class TestThePort:
    """The port to ask is the one the service listens on, found from the one published on the machine."""

    def test_it_is_the_port_inside_whose_published_side_is_the_recorded_one(self) -> None:
        runner = ports(
            FakeRunner(),
            {
                "80/tcp": [{"HostIp": ALL_INTERFACES, "HostPort": "8081"}],
                "9000/tcp": [{"HostIp": ALL_INTERFACES, "HostPort": "9001"}],
            },
        )

        assert reach.container_port(runner, APP, 9001) == 9000

    def test_one_port_published_is_the_answer_when_the_recorded_one_is_not_among_them(self) -> None:
        runner = ports(FakeRunner(), {"3000/tcp": [{"HostIp": ALL_INTERFACES, "HostPort": "8081"}]})

        assert reach.container_port(runner, APP, 8888) == 3000

    def test_a_port_that_is_exposed_and_not_published_counts_for_the_one_there_is(self) -> None:
        assert reach.container_port(ports(FakeRunner(), {"8080/tcp": None}), APP, 8081) == 8080

    def test_udp_is_not_a_web_port(self) -> None:
        runner = ports(FakeRunner(), {"53/udp": [{"HostIp": ALL_INTERFACES, "HostPort": "5353"}]})

        with pytest.raises(DockerError):
            reach.container_port(runner, APP, 5353)

    def test_several_and_no_way_to_tell_which_says_so_instead_of_picking(self) -> None:
        runner = ports(FakeRunner(), {"80/tcp": None, "81/tcp": None})

        with pytest.raises(DockerError) as caught:
            reach.container_port(runner, APP, 8081)

        assert "80" in caught.value.details and "81" in caught.value.details
