# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console with Docker on the machine, for the tests of the Docker zone's API.

:func:`build` is :func:`tests.support.teams_support.build_world` (two teams, an administrator, an operator,
an auditor, people of each team) plus a simulated machine with Docker
(:class:`tests.support.docker_machine.DockerMachine`) installed as the process-wide runner: a stack of
each team's application, what nobody of Noust made, a loop and a container stopped on purpose. What the
routes answer is what the gate asked that machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core.docker import gate as gate_module
from noust.core.docker import names
from noust.core.runner import set_runner
from tests.support.docker_machine import RELEASE, DockerMachine
from tests.support.teams_support import Person, World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@dataclass
class DockerWorld:
    """
    Attributes:
        world: The console with its people.
        machine: The simulated machine with Docker.
    """

    world: World
    machine: DockerMachine

    def get(self, who: str, path: str, **params: Any) -> Any:
        """
        Args:
            who: One of the people of the world.
            path: The path under ``/api/docker``.
            **params: The query.

        Returns:
            The response.
        """
        return self.world.client.get(
            f"/api/docker{path}", headers=self.world.as_(who), params=params
        )

    def send(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        """
        Args:
            who: One of the people of the world.
            method: The HTTP method.
            path: The path under ``/api/docker``.
            **kwargs: What the request carries.

        Returns:
            The response.
        """
        return self.world.client.request(
            method, f"/api/docker{path}", headers=self.world.as_(who), **kwargs
        )


def build(tmp_path: Path, monkeypatch: Any) -> DockerWorld:
    """
    Build the console and the machine.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Patching helper.

    Returns:
        The world and its machine.
    """
    from noust.web.api import docker as docker_api

    world = build_world(tmp_path, monkeypatch)
    # The master token acts as the instance, which sees every team and holds every permission.
    world.people["master"] = Person("master", world.master["Authorization"].split()[1])
    machine = DockerMachine()
    acme, globex = str(tmp_path / ACME), str(tmp_path / GLOBEX)
    acme_project, globex_project = (
        names.project_of_directory(ACME),
        names.project_of_directory(GLOBEX),
    )
    machine.box(
        "acme-web-1",
        project=acme_project,
        service="web",
        working_dir=acme,
        health="healthy",
        status="Up 2 days (healthy)",
        policy="unless-stopped",
        mounts=f"{acme_project}_data",
        networks=f"{acme_project}_default",
        image=f"{names.repository_name(acme_project, 'web')}:noust-{RELEASE}",
        started_at="2026-11-10T12:00:00Z",
    )
    machine.box(
        "globex-web-1",
        project=globex_project,
        service="web",
        working_dir=globex,
        policy="unless-stopped",
        started_at="2026-11-10T12:00:00Z",
    )
    machine.box(
        "portainer",
        image="portainer/portainer-ce:latest",
        policy="always",
        mounts="portainer_data",
        networks="bridge",
        ports="0.0.0.0:9000->9000/tcp",
        started_at="2026-10-10T12:00:00Z",
        log="level=info msg=started\npassword=hunter2hunter2 leaked",
    )
    machine.box(
        "api-loop",
        image="example/api:1",
        state="restarting",
        status="Restarting (137) 40 seconds ago",
        exit_code=137,
        oom=True,
        restart_count=31,
        restarts_recent=6,
        policy="unless-stopped",
        memory=256 * 2**20,
        started_at="2026-11-12T11:59:20Z",
        finished_at="2026-11-12T11:59:40Z",
        log="FATAL ERROR: JavaScript heap out of memory",
    )
    machine.box(
        "adminer",
        image="adminer:latest",
        state="exited",
        status="Exited (143) 3 days ago",
        exit_code=143,
        policy="unless-stopped",
        finished_at="2026-11-09T09:00:00Z",
        log="Received SIGTERM, shutting down",
    )
    machine.image("1", "portainer/portainer-ce", "latest", "300MB")
    machine.image("2", "adminer", "latest", "148MB")
    machine.image("3", "<none>", "<none>", "380MB")
    machine.image("4", names.repository_name(acme_project, "web"), f"noust-{RELEASE}", "300MB")
    machine.volume("portainer_data", size="48MB")
    machine.volume("wiki_data", size="3.4GB")
    machine.volume(f"{acme_project}_data", project=acme_project, volume="data", size="2GB")
    machine.network("bridge")
    machine.network("scratch")
    machine.network(f"{acme_project}_default", project=acme_project)
    set_runner(machine)
    # Nothing here measures the real disk of the machine the tests run on.
    monkeypatch.setattr(
        gate_module, "_free_space", lambda path: (160 * 2**30, 60 * 2**30, 100 * 2**30)
    )
    docker_api.reset_engine_cache()
    return DockerWorld(world, machine)
