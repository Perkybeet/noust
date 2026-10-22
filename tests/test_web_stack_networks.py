# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The networks of a stack over the API.

What is read from Docker is pinned in ``tests/test_stack_networks.py``. Here: that the application's
team reads it and another team does not even see that the application exists, that nothing needs
sudo mode (it is a look), and the shape the console reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.managers.stack_networks import Member, StackNetwork, StackNetworks
from tests.teams_support import World, build_world

APP = "shop.acme.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def answer() -> StackNetworks:
    return StackNetworks(
        app=APP,
        containers=("shop-web-1",),
        networks=(
            StackNetwork(
                name="proxy",
                id="ffffffffffff",
                logical="",
                driver="bridge",
                scope="local",
                internal=False,
                ipv6=False,
                subnets=("172.19.0.0/16",),
                owner="",
                members=(
                    Member("shop-web-1", "172.19.0.5", True),
                    Member("traefik", "172.19.0.2", False),
                ),
                findings=("It is shared: traefik is on it too.",),
            ),
        ),
    )


def test_the_applications_team_reads_its_networks_without_sudo_mode(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.web.api import app_stack

    monkeypatch.setattr(app_stack, "stack_networks", lambda app: answer())

    response = world.client.get(f"/api/apps/{APP}/networks", headers=world.as_("dana"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["isolated"] is False and body["containers"] == ["shop-web-1"]
    (network,) = body["networks"]
    assert network["shared"] is True
    assert [(m["name"], m["own"]) for m in network["members"]] == [
        ("shop-web-1", True),
        ("traefik", False),
    ]
    assert network["findings"] == ["It is shared: traefik is on it too."]


def test_another_teams_application_is_not_there_to_ask_about(world: World) -> None:
    response = world.client.get(f"/api/apps/{APP}/networks", headers=world.as_("bob"))

    assert response.status_code == 404


def test_an_application_that_does_not_run_in_docker_says_so_instead_of_failing(
    world: World, runner: Any
) -> None:
    response = world.client.get(f"/api/apps/{APP}/networks", headers=world.as_("alice"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applicable"] is False and "does not run in Docker" in body["reason"]
    assert body["networks"] == []
    assert not any(call[0] == "docker" for call in runner.calls)
