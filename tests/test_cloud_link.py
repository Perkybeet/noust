# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server that was not created by Noust, linked to its machine at a provider (checklist F-26).

Creating a server at a provider already puts the machine on record, so it can be powered on and deleted
from the fleet. A server enrolled by hand has no such record: linking it writes one, after asking the
provider whether the machine exists and is where the server is. What it does not do is touch the
machine: linking and unlinking change only this central's record.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import CloudProviderError, NodeError
from noust.core.store import NodeRecord
from tests.test_cloud_provision import World, server_json, world  # noqa: F401


def register(world: World, name: str = "web-2", host: str = "203.0.113.9") -> None:  # noqa: F811
    world.store.save_node(
        NodeRecord(
            name=name,
            ssh_host=host,
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key=f"noust-node-{name} ssh-ed25519 AAAA",
            console_port=8080,
        )
    )


def test_an_existing_server_is_linked_to_its_machine(world: World) -> None:  # noqa: F811
    register(world)

    machine = world.provisioner.link("web-2", "hz", "42", actor="ana")

    record = world.servers.get("web-2")
    assert (record.state, record.provider, record.provider_id, record.account) == (
        "ready",
        "hetzner",
        "42",
        "hz",
    )
    assert record.address == "203.0.113.9" and record.created_by == "ana"
    assert machine["node"] == "web-2"
    assert world.api.sent("DELETE", "/servers/42") == [], "linking touches nothing at the provider"
    assert world.api.sent("POST", "/servers/42/actions/poweron") == []


def test_the_machine_must_exist_at_the_provider(world: World) -> None:  # noqa: F811
    register(world)
    world.api.on("GET", "/servers/99", (404, {"error": {"code": "not_found", "message": "gone"}}))

    with pytest.raises(CloudProviderError):
        world.provisioner.link("web-2", "hz", "99")

    assert world.servers.find("web-2") is None


def test_a_machine_somewhere_else_than_the_server_is_refused_unless_forced(world: World) -> None:  # noqa: F811
    register(world, host="198.51.100.7")

    with pytest.raises(NodeError) as caught:
        world.provisioner.link("web-2", "hz", "42")

    assert "203.0.113.9" in caught.value.message and "198.51.100.7" in caught.value.message
    assert world.servers.find("web-2") is None
    world.provisioner.link("web-2", "hz", "42", force=True)
    assert world.servers.get("web-2").state == "ready"


def test_a_server_reached_by_name_cannot_be_compared_and_is_linked(world: World) -> None:  # noqa: F811
    register(world, host="web2.example.com")

    world.provisioner.link("web-2", "hz", "42")

    assert world.servers.get("web-2").state == "ready"


def test_a_server_that_is_not_registered_cannot_be_linked(world: World) -> None:  # noqa: F811
    with pytest.raises(NodeError) as caught:
        world.provisioner.link("ghost", "hz", "42")

    assert "ghost" in caught.value.message


def test_a_server_is_linked_once(world: World) -> None:  # noqa: F811
    register(world)
    world.provisioner.link("web-2", "hz", "42")

    with pytest.raises(NodeError) as caught:
        world.provisioner.link("web-2", "hz", "42")

    assert "already" in caught.value.message


def test_a_machine_belongs_to_one_server(world: World) -> None:  # noqa: F811
    register(world)
    register(world, name="web-3", host="203.0.113.9")
    world.provisioner.link("web-2", "hz", "42")

    with pytest.raises(NodeError) as caught:
        world.provisioner.link("web-3", "hz", "42")

    assert "web-2" in caught.value.message


def test_the_power_state_of_a_linked_machine_is_the_providers(world: World) -> None:  # noqa: F811
    register(world)
    world.provisioner.link("web-2", "hz", "42")
    world.api._routes[:] = [
        route
        for route in world.api._routes
        if not (route[0] == "GET" and route[1].pattern == "/servers/42")
    ]
    world.api.on("GET", "/servers/42", (200, server_json("off")))

    described = world.provisioner.machine("web-2", live=True)

    assert described["remote"]["state"] == "stopped"
    assert described["remote"]["raw_state"] == "off"


def test_a_linked_machine_can_be_powered_on(world: World) -> None:  # noqa: F811
    register(world)
    world.provisioner.link("web-2", "hz", "42")
    world.api.on("POST", "/servers/42/actions/poweron", (201, {"action": {"id": 1}}))

    world.provisioner.power_on("web-2", actor="ana")

    assert len(world.api.sent("POST", "/servers/42/actions/poweron")) == 1


def test_unlinking_forgets_the_record_and_leaves_the_machine_alone(world: World) -> None:  # noqa: F811
    register(world)
    world.provisioner.link("web-2", "hz", "42")

    world.provisioner.detach("web-2", actor="ana")

    assert world.servers.find("web-2") is None
    assert world.api.sent("DELETE", "/servers/42") == []
    assert world.store.get_node("web-2") is not None


def test_a_machine_that_was_deleted_can_be_linked_again(world: World) -> None:  # noqa: F811
    register(world)
    world.provisioner.link("web-2", "hz", "42")
    world.api.on("DELETE", "/servers/42", (200, {"action": {"id": 2}}))
    world.provisioner.delete_machine("web-2")

    world.provisioner.link("web-2", "hz", "42")

    assert world.servers.get("web-2").state == "ready"
