# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker the console's Docker zone looks at: an Ubuntu server's engine with a few stacks on it.

The zone reads Docker through the one door every module uses, so what answers here is
:class:`tests.support.docker_machine.DockerMachine`, the same simulated machine the tests build,
filled with what the zone has to draw: two applications of Noust with their containers (one of them
a container that keeps falling because memory runs out), a stack nobody of Noust made with a job that
ended well, a container somebody stopped, one Noust started for itself, images with no name, volumes
that nothing uses and the disk that comes with it all. An application is the one the store already
has, found by its directory, because a container's owner is decided from what the store registered
and never from what the container says of itself. Nothing is executed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.support.docker_machine import DockerMachine

#: The machine the runner consults for every ``docker`` command, once :func:`model_docker_host` has built it.
_DOCKER: DockerMachine | None = None

#: A compose file's name in the folder of an application and of a stack nobody of Noust made.
_COMPOSE = "compose.yaml"

_LOOP_LOG = (
    "2026-11-12T11:40:21.100000000Z [api] listening on :3000\n"
    "2026-11-12T11:40:21.900000000Z [api] connected to redis://redis:6379\n"
    "2026-11-12T11:40:26.300000000Z [api] warming the product cache (48212 rows)\n"
    "2026-11-12T11:40:43.000000000Z <--- Last few GCs --->\n"
    "2026-11-12T11:40:43.400000000Z FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\n"
)

_WEB_LOG = "".join(
    f"2026-11-12T11:{minute:02d}:{second:02d}.000000000Z GET /{path} 200 {3 + (second % 17)}ms\n"
    for minute, second, path in (
        (30, 1, "health"),
        (30, 14, "api/products"),
        (31, 2, "api/cart"),
        (31, 40, "static/app.js"),
        (32, 7, "api/orders"),
    )
)


def model_docker_host(apps: Mapping[str, str]) -> DockerMachine:
    """
    Build the engine the Docker zone reads, over the applications the store has.

    Args:
        apps: Each application's domain and its directory, as the store records them. The containers
            of an application run from its directory, which is how Noust knows they are its own.

    Returns:
        The machine, which :func:`answer` consults for every ``docker`` command.
    """
    # Here and not at the top: the repository reaches the path when the sandbox is prepared, after this module
    # has loaded, and `python scripts/console_server.py` has to start from anywhere.
    from tests.support.docker_machine import DockerMachine

    global _DOCKER
    machine = DockerMachine()
    machine.engine["version"] = "29.1.3"
    machine.engine["compose"] = "2.40.3"

    def project_of(domain: str) -> str:
        return domain.replace(".", "-")

    # The three applications of the seed that run as stacks: a shop with a worker and a database, a plain one, and one whose
    # service keeps falling because its memory runs out.
    shop = "shop.example.net" if "shop.example.net" in apps else None
    blog = "example.net" if "example.net" in apps else None
    api = "tools.example.net" if "tools.example.net" in apps else None

    if shop is not None:
        project = project_of(shop)
        directory = apps[shop]
        base = {
            "project": project,
            "working_dir": directory,
            "config_files": f"{directory}/{_COMPOSE}",
            "networks": f"{project}_default",
        }
        machine.image("a", f"{project}-web", "release-41", "412MB")
        machine.image("b", f"{project}-worker", "release-41", "398MB")
        machine.image("c", "postgres", "16", "431MB")
        machine.box(
            f"{project}-web-1",
            image=f"{project}-web:release-41",
            service="web",
            ports="127.0.0.1:3004->3000/tcp",
            mounts=f"{project}_uploads",
            log=_WEB_LOG,
            memory=805306368,
            policy="unless-stopped",
            **base,
        )
        machine.box(
            f"{project}-worker-1",
            image=f"{project}-worker:release-41",
            service="worker",
            policy="unless-stopped",
            log=_WEB_LOG,
            **base,
        )
        machine.box(
            f"{project}-db-1",
            image="postgres:16",
            service="db",
            mounts=f"{project}_db-data",
            policy="unless-stopped",
            **base,
        )
        machine.network(f"{project}_default", project=project)
        machine.volume(f"{project}_uploads", size="1.1GB", project=project, volume="uploads")
        machine.volume(f"{project}_db-data", size="2.3GB", project=project, volume="db-data")

    if blog is not None:
        project = project_of(blog)
        directory = apps[blog]
        machine.image("d", f"{project}-app", "release-12", "296MB")
        machine.box(
            f"{project}-app-1",
            image=f"{project}-app:release-12",
            service="app",
            project=project,
            working_dir=directory,
            config_files=f"{directory}/{_COMPOSE}",
            networks=f"{project}_default",
            policy="unless-stopped",
            log=_WEB_LOG,
        )
        machine.network(f"{project}_default", project=project)

    if api is not None:
        project = project_of(api)
        directory = apps[api]
        base = {
            "project": project,
            "working_dir": directory,
            "config_files": f"{directory}/{_COMPOSE}",
            "networks": f"{project}_default",
        }
        machine.image("e", f"{project}-api", "release-8", "355MB")
        machine.image("f", "redis", "7-alpine", "41MB")
        machine.box(
            f"{project}-api-1",
            image=f"{project}-api:release-8",
            service="api",
            state="restarting",
            status="Restarting (137) 8 seconds ago",
            exit_code=137,
            oom=True,
            memory=268435456,
            restart_count=14,
            restarts_recent=6,
            policy="unless-stopped",
            log=_LOOP_LOG,
            stats={
                "CPUPerc": "0.00%",
                "MemUsage": "0B / 256MiB",
                "MemPerc": "0.00%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
                "PIDs": "0",
            },
            **base,
        )
        machine.box(
            f"{project}-redis-1",
            image="redis:7-alpine",
            service="redis",
            policy="unless-stopped",
            **base,
        )
        machine.network(f"{project}_default", project=project)

    # A stack somebody started with docker compose, in a folder of its own: nobody of Noust made it.
    nextcloud = {
        "project": "nextcloud",
        "working_dir": "/srv/nextcloud",
        "config_files": f"/srv/nextcloud/{_COMPOSE}",
        "networks": "nextcloud_default",
    }
    machine.image("1", "nextcloud", "29-apache", "1.3GB")
    machine.image("2", "mariadb", "11", "401MB")
    machine.image("3", "redis", "7", "117MB")
    machine.box(
        "nextcloud-app-1",
        image="nextcloud:29-apache",
        service="app",
        mounts="nextcloud_data",
        policy="unless-stopped",
        **nextcloud,
    )
    machine.box(
        "nextcloud-db-1",
        image="mariadb:11",
        service="db",
        mounts="nextcloud_db",
        policy="unless-stopped",
        **nextcloud,
    )
    machine.box(
        "nextcloud-redis-1", image="redis:7", service="redis", policy="unless-stopped", **nextcloud
    )
    machine.box(
        "nextcloud-setup-1",
        image="nextcloud:29-apache",
        service="setup",
        oneoff=True,
        state="exited",
        status="Exited (0) 3 days ago",
        exit_code=0,
        finished_at="2026-11-09T10:00:00.000000000Z",
        **nextcloud,
    )
    machine.network("nextcloud_default", project="nextcloud")
    machine.volume("nextcloud_data", size="9.1GB", project="nextcloud", volume="data")
    machine.volume("nextcloud_db", size="1.4GB", project="nextcloud", volume="db")

    # Loose containers: one somebody stopped that its policy says should run, one a test left behind, and Noust's own helper.
    machine.image("4", "adminer", "4", "98MB")
    machine.box(
        "adminer",
        image="adminer:4",
        state="exited",
        status="Exited (143) 3 days ago",
        exit_code=143,
        policy="unless-stopped",
        finished_at="2026-11-09T08:12:41.000000000Z",
    )
    machine.box(
        "old-test",
        image="adminer:4",
        state="exited",
        status="Exited (0) 41 days ago",
        exit_code=0,
        finished_at="2026-10-02T09:00:00.000000000Z",
    )
    machine.image("5", "moby/buildkit", "buildx-stable-1", "187MB")
    machine.box(
        "noust-buildkit",
        image="moby/buildkit:buildx-stable-1",
        helper="buildkit",
        policy="unless-stopped",
    )

    # What nothing uses: two images that lost their name, a volume of a wiki that no longer runs, one Docker made for a container.
    machine.image("6", "<none>", "<none>", "380MB")
    machine.image("7", "<none>", "<none>", "260MB")
    machine.volume("wiki_data", size="3.4GB")
    machine.volume("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", size="12MB")
    machine.network("bridge", driver="bridge")
    machine.network("host", driver="host")
    machine.network("none", driver="null")
    machine.df = [
        {
            "Type": "Images",
            "TotalCount": str(len(machine.images)),
            "Size": "9.5GB",
            "Reclaimable": "1.8GB (18%)",
        },
        {
            "Type": "Containers",
            "TotalCount": str(len(machine.boxes)),
            "Size": "120MB",
            "Reclaimable": "40MB (33%)",
        },
        {
            "Type": "Local Volumes",
            "TotalCount": str(len(machine.volumes)),
            "Size": "17.3GB",
            "Reclaimable": "3.4GB (19%)",
        },
        {"Type": "Build Cache", "TotalCount": "0", "Size": "1.2GB", "Reclaimable": "1.2GB"},
    ]
    # The Software page and the machine's own `which` agree that Docker is installed.
    from . import server_host

    if server_host._SERVER_HOST is not None:
        packages = server_host._SERVER_HOST.packages.versions
        packages.setdefault("docker.io", "24.0.7-0ubuntu4.1")
        packages.setdefault("docker-compose-v2", "2.24.6-0ubuntu1~24.04.1")
    _DOCKER = machine
    return machine


def answer(args: Sequence[str]) -> tuple[int, str, str] | None:
    """
    Answer one ``docker`` command the way the engine does.

    Args:
        args: The command.

    Returns:
        ``(exit code, stdout, stderr)``, or None when no engine was modelled (the sandbox has no Docker)
        or the command is not one.
    """
    if _DOCKER is None:
        return None
    return _DOCKER.answer(args)
