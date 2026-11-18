# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container of an application that keeps falling asks for attention where the operator looks first.

Home, the bell and the list of applications said an application ran while its container was restarted
six times in ten minutes because it ran out of memory: what the Docker zone knows of a container only
lived in the zone. The list of what needs attention (``GET /api/overview``, which Home and the bell read)
carries it now, as a failure of the application with the story of its container, and it asks Docker the way
every other source of the list does: once in each window the Overview is kept, never once per request, and
not at all where the machine has no Docker to ask or this central does not use it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.managers.overview import reset_cache
from tests.support.docker_web import ACME, DockerWorld, build

pytestmark = pytest.mark.real_app_containers


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    # The configuration remembers the role it read: a test that makes the machine a hub must not leave
    # that to the next test of the worker, whose environment is back to a server's.
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    from noust.central.machine import MachineFacts
    from noust.web.api import docker as docker_api

    reset_cache()
    built = build(tmp_path, monkeypatch)

    def what_the_machine_has(*args: Any, ask_docker: bool = True, **kwargs: Any) -> MachineFacts:
        # What the facts of the machine answer for this one (the suite's default says it has all
        # of it): Docker is there when it is installed and the central may look at it.
        return MachineFacts(
            systemd=True,
            web_server=True,
            docker=ask_docker and built.machine.installed,
            root=True,
            journal=True,
        )

    monkeypatch.setattr("noust.central.machine.read_machine_facts", what_the_machine_has)
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        reset_cache()


def fall(docker: DockerWorld, *, oom: bool = True) -> None:
    """The container of the application of one team begins to fall, as an application's does."""
    box = docker.machine.boxes["acme-web-1"]
    box.state, box.status = "restarting", "Restarting (137) 40 seconds ago"
    box.exit_code, box.oom, box.restarts_recent = 137, oom, 6
    box.memory = 256 * 2**20


def overview(docker: DockerWorld) -> dict[str, Any]:
    response = docker.world.client.get("/api/overview", headers=docker.world.as_("master"))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def rows_of(body: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    return [item for item in body["attention"] if item["id"] == f"app:{domain}"]


def test_an_application_whose_container_keeps_falling_asks_for_attention(
    docker: DockerWorld,
) -> None:
    fall(docker)

    [item] = rows_of(overview(docker), ACME)

    assert item["severity"] == "fail"
    assert item["subject"] == {"kind": "app", "domain": ACME}
    [why] = item["reasons"]
    assert why["code"] == "service_failed"
    assert why["actions"] == ["view_log", "diagnose"]
    assert why["detail_coded"] == {
        "code": "attention.container_loop_oom_limit",
        "params": {"container": "acme-web-1", "restarts": 6, "minutes": 10, "limit_mb": 256},
    }


def test_an_application_whose_containers_are_well_is_not_in_the_list_for_them(
    docker: DockerWorld,
) -> None:
    # The machine has a loop that nobody of Noust made (api-loop) and a container stopped on purpose
    # (adminer): they are the zone's to tell, not a failure of an application.
    body = overview(docker)

    assert rows_of(body, ACME) == []
    assert not [item for item in body["attention"] if item["subject"].get("kind") == "app"]


def test_docker_is_asked_once_in_each_window_and_not_once_per_request(
    docker: DockerWorld,
) -> None:
    fall(docker)

    for _ in range(3):
        overview(docker)

    asked = [call for call in docker.machine.calls if call[:2] == ("docker", "events")]
    assert len(asked) == 1


def test_it_asks_only_what_it_needs_and_changes_nothing(docker: DockerWorld) -> None:
    fall(docker)
    docker.machine.calls.clear()

    overview(docker)

    # The listing, what the containers say of themselves and how often they restarted: nothing that
    # changes a container, and neither the disk, nor the images, nor a registry (the zone's own page
    # is where those are read).
    docker_calls = [call for call in docker.machine.calls if call[:1] == ("docker",)]
    assert {call[1] for call in docker_calls} == {"ps", "inspect", "events"}


def test_a_machine_without_docker_has_its_list_and_nothing_is_asked(docker: DockerWorld) -> None:
    from noust.web.api import docker as docker_api

    docker.machine.installed = False
    docker_api.reset_engine_cache()
    docker.machine.calls.clear()

    body = overview(docker)

    assert not [item for item in body["attention"] if item["subject"].get("kind") == "app"]
    assert not [
        call
        for call in docker.machine.calls
        if call[:2] in (("docker", "ps"), ("docker", "inspect"))
    ]


def test_a_central_that_does_not_use_its_machines_docker_leaves_it_alone(
    docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hub is a central that deploys nothing itself until it is told to; the socket mounted into its
    # container is not the consent, and not even a look is given to what is in it.
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    fall(docker)
    docker.machine.calls.clear()

    body = overview(docker)

    assert rows_of(body, ACME) == []
    assert not [call for call in docker.machine.calls if call[:1] == ("docker",)]
