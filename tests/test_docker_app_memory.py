# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the containers of an application ended, asked through the door of Docker (item 175).

The diagnosis of an application that runs in containers needs to know whether the kernel killed one
of them for running out of memory, and what limit it had. ``docker inspect`` says it in ``State``
(``OOMKilled``) and ``HostConfig`` (``Memory``): the gate looks at the containers once, keeps those the
store says are the application's, and inspects them in one process. Nothing about another
application's containers, or about one nobody owns, leaves it.
"""

from __future__ import annotations

import pytest

from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.teams import Scope
from tests.support.docker_machine import DockerMachine

MIB = 1024 * 1024
SHOP = "/var/www/apps/shop-example-com"
BLOG = "/var/www/apps/blog-example-com"


@pytest.fixture
def machine() -> DockerMachine:
    made = DockerMachine()
    made.box(
        "shop-web-1",
        project="shop-example-com",
        service="web",
        working_dir=SHOP,
        state="exited",
        oom=True,
        exit_code=137,
        memory=512 * MIB,
        started_at="2026-11-20T08:00:00.000000000Z",
    )
    made.box("shop-worker-1", project="shop-example-com", service="worker", working_dir=SHOP)
    made.box("blog-web-1", project="blog-example-com", service="web", working_dir=BLOG, oom=True)
    made.box("portainer", oom=True)
    return made


def gate(machine: DockerMachine, *, sees: set[str] | None = None) -> DockerGate:
    registry = Registry(
        apps=(AppRecord("shop.example.com", SHOP), AppRecord("blog.example.com", BLOG))
    )
    return DockerGate(
        machine,
        registry=lambda: registry,
        scope=Scope.everything(),
        sees_app=(lambda domain: domain in sees) if sees is not None else None,
    )


class TestAppMemory:
    def test_each_container_of_the_application_says_how_it_ended_and_its_limit(
        self, machine: DockerMachine
    ) -> None:
        found = gate(machine).app_memory("shop.example.com")

        by_name = {item.name: item for item in found}
        assert sorted(by_name) == ["shop-web-1", "shop-worker-1"]
        web = by_name["shop-web-1"]
        assert web.oom_killed is True
        assert web.running is False
        assert web.limit_bytes == 512 * MIB
        assert web.started_at is not None and web.started_at.year == 2026
        worker = by_name["shop-worker-1"]
        assert worker.oom_killed is False and worker.running is True
        assert worker.limit_bytes is None

    def test_what_belongs_to_another_application_or_to_nobody_is_not_the_applications(
        self, machine: DockerMachine
    ) -> None:
        names = {item.name for item in gate(machine).app_memory("blog.example.com")}

        assert names == {"blog-web-1"}
        assert gate(machine).app_memory("nobody.example.com") == ()

    def test_it_is_one_look_and_one_inspection_however_many_containers_there_are(
        self, machine: DockerMachine
    ) -> None:
        gate(machine).app_memory("shop.example.com")

        asked = [call[:2] for call in machine.calls]
        assert asked.count(("docker", "ps")) == 1
        assert asked.count(("docker", "inspect")) == 1

    def test_nothing_is_inspected_when_the_application_has_no_container(
        self, machine: DockerMachine
    ) -> None:
        gate(machine).app_memory("nobody.example.com")

        assert not any(call[:2] == ("docker", "inspect") for call in machine.calls)

    def test_a_principal_who_does_not_see_the_application_does_not_see_its_containers(
        self, machine: DockerMachine
    ) -> None:
        registry = Registry(apps=(AppRecord("shop.example.com", SHOP),))
        blind = DockerGate(
            machine, registry=lambda: registry, scope=Scope.nothing(), sees_app=lambda _d: False
        )
        sighted = DockerGate(
            machine,
            registry=lambda: registry,
            scope=Scope.nothing(),
            sees_app=lambda domain: domain == "shop.example.com",
        )

        assert blind.app_memory("shop.example.com") == ()
        assert len(sighted.app_memory("shop.example.com")) == 2

    def test_a_docker_that_does_not_answer_raises_what_every_look_at_docker_raises(
        self, machine: DockerMachine
    ) -> None:
        from noust.core.docker.errors import DockerUnavailableError

        machine.down = True

        with pytest.raises(DockerUnavailableError):
            gate(machine).app_memory("shop.example.com")
