# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker of a NAS, for the central whose container was told to deploy on it (``--deploy-here containers``).

The owner's central runs in a container on a NAS. Whether it deploys there is decided outside the console (the
socket mounted and ``NOUST_DEPLOY_HERE`` in its compose file), so the console has two states to be seen in: before,
with nothing the console can do about it, and after, with the machine's Docker at hand. This models the second: an
engine as old as the ones NAS vendors still ship (Docker 24.0, API 1.43, Compose 2.20, the classic image store), the
stacks a NAS already runs, and one application Noust deployed there. It is the same simulated machine the tests build
(:class:`tests.support.docker_machine.DockerMachine`), so what the console reads is what the tests assert.

Two things only a NAS has are modelled besides the engine:

- the witness of the deploy-here check: a container the daemon starts with the applications' folder mounted from the
  machine's side, which prints the file the check wrote. ``--docker-fault folder`` makes the machine see another
  folder, as a NAS does when the container's volume is not mounted at the path it has outside;
- the socket's group: ``--docker-fault group`` leaves the account Noust runs as outside the group that owns Docker's
  socket, which is what a compose file without ``group_add`` does.

Nothing is executed.
"""

from __future__ import annotations

import csv
import functools
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.support.docker_machine import DockerMachine

#: The machine's name, which the address of an application is made of (``http://nas-casa:8081``).
MACHINE = "nas-casa"

#: The group that owns Docker's socket on the modelled NAS, and the one Noust is given with ``group_add``.
SOCKET_GROUP = ("administrators", 101)

#: What the application Noust deployed there is called, and what it runs.
APP_DOMAIN = "vault.casa.example.org"
APP_PROJECT = "vault-casa-example-org"
APP_IMAGE = "vaultwarden/server:1.32"
APP_PORT = 8081

_COMPOSE = "compose.yaml"

#: The faults a person can ask the model for, to see what the check says of each.
FAULTS = ("folder", "group")


@functools.cache
def _nas_docker_class() -> type[DockerMachine]:
    """
    Build the NAS's Docker class: the engine of a DockerMachine with what a NAS's central asks of it that the zone does not.

    Built on first use and not at import, because the tests' simulated machine imports Noust, and this package imports
    nothing of Noust until the sandbox has set the environment Noust reads.

    Returns:
        The class.
    """
    from tests.support.docker_machine import DockerMachine

    class NasDocker(DockerMachine):
        """
        A NAS's Docker: the engine of a DockerMachine with what a NAS's central asks of it that the zone does not.

        Attributes:
            fault: What is wrong with the machine, one of :data:`FAULTS`, or None.
        """

        def __init__(self, fault: str | None = None) -> None:
            super().__init__()
            self.fault = fault
            self.engine.update(
                {"version": "24.0.2", "api": "1.43", "min_api": "1.12", "compose": "2.20.2"}
            )
            self.engine["root_dir"] = "/volume1/@docker"

        def answer(self, argv: Sequence[str]) -> tuple[int, str, str] | None:
            """
            Answer a ``docker`` command for the console's runner, which has its own and asks this one only about Docker.

            Args:
                argv: The command.

            Returns:
                ``(exit code, stdout, stderr)``, or None for a command that is not Docker's.
            """
            recorded = tuple(argv)
            if recorded[:1] != ("docker",):
                return None
            return self._docker(recorded)

        def _info(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
            if "--format" in argv:
                # What the central asks to say the machine's name in an address.
                return 0, f"{MACHINE}\n", ""
            body = {
                "Name": MACHINE,
                "Driver": "overlay2",
                "DockerRootDir": self.engine["root_dir"],
                "OperatingSystem": "Synology DSM 7.2",
                "SecurityOptions": ["name=seccomp,profile=builtin"],
                # The classic image store: containerd's is what Railpack needs and a NAS of this age does not use.
                "DriverStatus": [["Backing Filesystem", "extfs"]],
            }
            return 0, json.dumps(body), ""

        def _compose_verb(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
            """What Compose says of a project that the engine's own model does not: the containers it lists."""
            if "-p" in argv and "ps" in argv:
                project = argv[argv.index("-p") + 1]
                members = [box for box in self.boxes.values() if box.project == project]
                if "-q" in argv:
                    return 0, "\n".join(box.id for box in members), ""
                rows = [
                    {
                        "Name": box.name,
                        "Service": box.service,
                        "State": "running" if box.state == "running" else box.state,
                        "Health": box.health,
                        "ExitCode": box.exit_code,
                        "Status": box.status,
                    }
                    for box in members
                ]
                return 0, "\n".join(json.dumps(row) for row in rows), ""
            return super()._compose_verb(argv)

        def _run(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
            """``docker run``: the deploy-here check's witness reads a file through the machine's own mount."""
            if "--entrypoint" in argv and argv[
                argv.index("--entrypoint") + 1 : argv.index("--entrypoint") + 2
            ] == ("cat",):
                return self._witness(argv)
            return super()._run(argv)

        def _witness(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
            """
            Read the witness file the way the daemon would: from the folder the machine has at that path.

            Args:
                argv: The ``docker run`` the check made.

            Returns:
                The file's content, or ``cat``'s own words for a file the machine's folder does not hold.
            """
            mount = argv[argv.index("--mount") + 1]
            options = dict(
                item.split("=", 1) if "=" in item else (item, "")
                for item in next(csv.reader(io.StringIO(mount)))
            )
            wanted = argv[-1]
            name = wanted.rsplit("/", 1)[-1]
            found = Path(options.get("source", "")) / name
            if self.fault == "folder" or not found.is_file():
                return 1, "", f"cat: can't open '{wanted}': No such file or directory"
            return 0, found.read_text(encoding="utf-8"), ""

    return NasDocker


def model_nas_docker(apps_dir: Path, *, fault: str | None = None) -> Any:
    """
    Build the Docker of the NAS: the stacks it already ran, Noust's own container and the application Noust deployed.

    Args:
        apps_dir: The folder of Noust's applications, which the application Noust deployed runs from (that is how the
            zone knows whose a container is: from the store, never from the container's own labels).
        fault: Something to be wrong with the machine (one of :data:`FAULTS`).

    Returns:
        The machine, which the runner consults for every ``docker`` command.
    """
    machine = _nas_docker_class()(fault)  # type: ignore[call-arg]
    directory = str(apps_dir / APP_PROJECT)

    machine.image("a", "vaultwarden/server", "1.32", "216MB")
    machine.image("b", "nextcloud", "29-apache", "1.3GB")
    machine.image("c", "mariadb", "11", "401MB")
    machine.image("d", "ghcr.io/perkybeet/noust", "latest", "312MB")
    machine.image("e", "jellyfin/jellyfin", "10.9", "684MB")
    machine.box(
        f"{APP_PROJECT}-app-1",
        image=APP_IMAGE,
        service="app",
        project=APP_PROJECT,
        working_dir=directory,
        config_files=f"{directory}/{_COMPOSE}",
        networks=f"{APP_PROJECT}_default",
        ports=f"0.0.0.0:{APP_PORT}->80/tcp",
        mounts=f"{APP_PROJECT}_data",
        policy="unless-stopped",
    )
    nextcloud = {
        "project": "nextcloud",
        "working_dir": "/volume1/docker/nextcloud",
        "config_files": f"/volume1/docker/nextcloud/{_COMPOSE}",
        "networks": "nextcloud_default",
        "policy": "unless-stopped",
    }
    machine.box(
        "nextcloud-app-1",
        image="nextcloud:29-apache",
        service="app",
        ports="0.0.0.0:8082->80/tcp",
        mounts="nextcloud_data",
        **nextcloud,
    )
    machine.box(
        "nextcloud-db-1", image="mariadb:11", service="db", mounts="nextcloud_db", **nextcloud
    )
    noust = {
        "project": "noust",
        "working_dir": "/volume1/docker/noust",
        "config_files": f"/volume1/docker/noust/{_COMPOSE}",
        "networks": "noust_default",
        "policy": "unless-stopped",
    }
    machine.box(
        "noust",
        image="ghcr.io/perkybeet/noust:latest",
        service="noust",
        ports="0.0.0.0:8443->8443/tcp",
        **noust,
    )
    machine.box(
        "jellyfin",
        image="jellyfin/jellyfin:10.9",
        ports="0.0.0.0:8096->8096/tcp",
        policy="unless-stopped",
    )
    machine.network(f"{APP_PROJECT}_default", project=APP_PROJECT)
    machine.network("nextcloud_default", project="nextcloud")
    machine.network("noust_default", project="noust")
    machine.network("bridge", driver="bridge")
    machine.volume(f"{APP_PROJECT}_data", size="48MB", project=APP_PROJECT, volume="data")
    machine.volume("nextcloud_data", size="311GB", project="nextcloud", volume="data")
    machine.volume("nextcloud_db", size="2.1GB", project="nextcloud", volume="db")
    machine.df = [
        {
            "Type": "Images",
            "TotalCount": str(len(machine.images)),
            "Size": "2.9GB",
            "Reclaimable": "0B (0%)",
        },
        {
            "Type": "Containers",
            "TotalCount": str(len(machine.boxes)),
            "Size": "120MB",
            "Reclaimable": "0B (0%)",
        },
        {
            "Type": "Local Volumes",
            "TotalCount": str(len(machine.volumes)),
            "Size": "313GB",
            "Reclaimable": "0B (0%)",
        },
        {"Type": "Build Cache", "TotalCount": "0", "Size": "0B", "Reclaimable": "0B"},
    ]
    return machine


def seed_deployed_app(apps_dir: Path) -> None:
    """
    Record the application Noust deployed on the NAS: a container from a registry image, published on a port of the machine.

    The rows are what a deployment leaves (the application, how its container is made, one successful deployment) and
    the folder is what it keeps (its Compose file and its environment), so that every page of the application has
    something true to read.

    Args:
        apps_dir: The folder of Noust's applications.
    """
    from noust.core.container_store import ContainerSettings, ContainerStore
    from noust.core.store import App, BackupScheduleRecord, get_store

    store = get_store()
    folder = apps_dir / APP_PROJECT
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _COMPOSE).write_text(
        "services:\n"
        "  app:\n"
        f"    image: {APP_IMAGE}\n"
        "    restart: unless-stopped\n"
        "    ports:\n"
        f'      - "{APP_PORT}:80"\n'
        "    volumes:\n"
        "      - data:/data\n"
        "volumes:\n"
        "  data:\n",
        encoding="utf-8",
    )
    (folder / ".env").write_text(f"PORT={APP_PORT}\n", encoding="utf-8")
    store.create_app(
        App(
            domain=APP_DOMAIN,
            app_type="container",
            source=APP_IMAGE,
            port=APP_PORT,
            app_path=str(folder),
            status="running",
            ssl_enabled=False,
            layout="inplace",
        )
    )
    ContainerStore(store).save(
        APP_DOMAIN,
        ContainerSettings(method="image", image=APP_IMAGE, container_port=80),
        actor=None,
    )
    # A copy of its data every night, which a NAS's central runs itself: there are no timers to do it.
    store.save_backup_schedule(BackupScheduleRecord(app_domain=APP_DOMAIN, schedule="daily"))
    first = store.record_deployment_start(APP_DOMAIN, "panel", git_branch="main")
    store.annotate_deployment(first, commit_message=f"Run {APP_IMAGE}")
    store.finish_deployment(first, "success")


#: The projects the new-application wizard is pointed at on the NAS: one with a Dockerfile, one that is a Compose stack, and one that
#: nothing here can run (a Node project with no Dockerfile and no Compose file), which the wizard says and offers no way forward from.
SOURCES = ("container-api", "compose-wiki", "node-shop")


def seed_sources(root: Path) -> None:
    """
    Write the projects the wizard can be pointed at, in the sandbox's stand-in for ``/var/www/src``.

    Args:
        root: That folder (``sandbox.var / "www" / "src"``).
    """
    container = root / SOURCES[0]
    container.mkdir(parents=True, exist_ok=True)
    (container / "Dockerfile").write_text(
        "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nRUN npm ci\nEXPOSE 3000\n"
        'CMD ["node", "server.js"]\n',
        encoding="utf-8",
    )
    (container / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(3000);\n", encoding="utf-8"
    )
    stack = root / SOURCES[1]
    stack.mkdir(parents=True, exist_ok=True)
    (stack / "compose.yaml").write_text(
        "services:\n  wiki:\n    image: ghcr.io/example/wiki:2\n    ports:\n"
        '      - "8085:3000"\n    volumes:\n      - wiki-data:/data\nvolumes:\n  wiki-data:\n',
        encoding="utf-8",
    )
    shop = root / SOURCES[2]
    shop.mkdir(parents=True, exist_ok=True)
    (shop / "package.json").write_text(
        '{\n  "name": "node-shop",\n  "version": "1.0.0",\n  "scripts": {"start": "node server.js"},\n'
        '  "dependencies": {"express": "4.21.0"}\n}\n',
        encoding="utf-8",
    )
    (shop / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT || 3000);\n",
        encoding="utf-8",
    )


#: The machine the runner consults, once :func:`install` has built it.
_MACHINE: Any = None


def install(apps_dir: Path, *, fault: str | None = None) -> Any:
    """
    Make the modelled machine a NAS with Docker: the engine answers, the socket is there, and the check can prove a folder.

    Args:
        apps_dir: The folder of Noust's applications.
        fault: Something to be wrong with it (one of :data:`FAULTS`).

    Returns:
        The machine.
    """
    from noust.central import deploy_check

    global _MACHINE
    _MACHINE = model_nas_docker(apps_dir, fault=fault)

    def socket_access(path: str) -> deploy_check.SocketAccess | None:
        group, gid = SOCKET_GROUP
        # The central's account is in the group the socket belongs to, unless the model says it was left out.
        return deploy_check.SocketAccess(
            path=path, is_socket=True, gid=gid, group=group, accessible=fault != "group"
        )

    # The check asks the machine through this one function, so replacing it is how a test hands it another machine.
    deploy_check.default_seams = lambda: deploy_check.Seams(socket_access=socket_access)
    return _MACHINE


def give_the_client() -> None:
    """
    Put Docker's command line on the modelled machine, where the central's image has it.

    The machine's own ``which docker`` follows its package list, and a container profile has none of Docker's.
    """
    from . import server_host

    host = server_host._SERVER_HOST
    if host is not None:
        host.packages.versions.setdefault("docker.io", "28.5.2")


def answer(args: Sequence[str]) -> tuple[int, str, str] | None:
    """
    Answer one ``docker`` command the way the NAS's engine does.

    Args:
        args: The command.

    Returns:
        ``(exit code, stdout, stderr)``, or None when no NAS was modelled or the command is not Docker's.
    """
    if _MACHINE is None:
        return None
    return _MACHINE.answer(args)
