# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server with Docker and systemd, in memory, for the instances Noust runs.

:class:`DockerHost` stands in for the machine, and it is faithful where the
code under test depends on it being: starting a unit reads the Compose file
Noust wrote *from disk* and makes the container that file says (its image, its
labels, its published ports, its environment from the env file), stopping the
unit removes the container the way ``compose down`` does, ``docker pull`` of a
tag answers with what the registry serves for it, and a container's health
follows a script. So what is asserted is what the files and the commands
really do together, not what one function calls another.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database import instance_layout as layout
from noust.managers.database import managed
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.service import DatabaseService
from noust.managers.database.settings import Resources
from noust.managers.service_manager import ServiceManager

GIB = 1024**3


class DockerHost(FakeRunner):
    """
    Docker and systemd on one machine.

    Attributes:
        containers: By name: what ``docker inspect`` reports.
        volumes: The Docker volumes that exist.
        images: The image references pulled.
        served: What the registry serves a tag as: ``"postgres:16"`` to a digest.
        health: The health statuses a container reports, in order, per name;
            the last one repeats. ``healthy`` when none is scripted.
        exec_stdout: What ``docker exec`` prints.
        exec_fails: Whether ``docker exec`` fails.
        listening: Ports something else holds.
        fail: argv prefixes that fail, with their stderr.
        units: The unit files written, by name.
        enabled: The units enabled.
        started: Every unit started or restarted, in order.
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.containers: dict[str, dict[str, Any]] = {}
        self.volumes: set[str] = set()
        self.images: set[str] = set()
        self.served: dict[str, str] = {}
        self.health: dict[str, list[str]] = {}
        #: The PostgreSQL databases ``psql`` finds, with their owner.
        self.databases: dict[str, str] = {"appdb": "app"}
        #: What any other ``docker exec`` prints (a MySQL or Redis client, a dump).
        self.exec_stdout = ""
        self.exec_fails = False
        self.listening: set[int] = {22, 5432}
        self.fail: dict[tuple[str, ...], str] = {}
        self.enabled: set[str] = set()
        self.started: list[str] = []
        self.log = "database system is ready to accept connections\n"

    # ------------------------------------------------------------- helpers

    def unit_file(self, unit: str) -> Path:
        """
        Args:
            unit: A unit name.

        Returns:
            Where its file is written (the test's managed directory).
        """
        from noust.managers.service_manager import ServiceManager

        return Path(ServiceManager.SYSTEMD_DIR) / f"{unit}.service"

    def _compose_of(self, unit: str) -> dict[str, Any] | None:
        """
        Read the Compose file the unit would run.

        Args:
            unit: The unit.

        Returns:
            The document, or None when the unit has no file or no project.
        """
        name = unit.removeprefix("noust-db-")
        path = layout.compose_path(name)
        if not path.exists():
            return None
        document = yaml.safe_load(path.read_text())
        return document if isinstance(document, dict) else None

    def _create_container(self, unit: str) -> None:
        """
        Do what ``docker compose up -d`` does with the file a unit points at.

        Args:
            unit: The unit.
        """
        document = self._compose_of(unit)
        if document is None:
            return
        for service in document["services"].values():
            name = service["container_name"]
            env: list[str] = []
            for env_file in service.get("env_file", []):
                path = layout.compose_path(unit.removeprefix("noust-db-")).parent / env_file
                env += [
                    line
                    for line in path.read_text().splitlines()
                    if line and not line.startswith("#")
                ]
            ports: dict[str, list[dict[str, str]]] = {}
            for spec in service.get("ports", []):
                host_ip, host_port, container_port = spec.split(":")
                ports[f"{container_port}/tcp"] = [{"HostIp": host_ip, "HostPort": host_port}]
            self.containers[name] = {
                "Id": f"{name}-id",
                "Name": f"/{name}",
                "Config": {
                    "Image": service["image"],
                    "Env": [*env, "PATH=/usr/local/bin:/usr/bin"],
                    "Labels": dict(service.get("labels", {})),
                    "ExposedPorts": {port: {} for port in ports},
                    "Cmd": service.get("command"),
                    "Entrypoint": None,
                },
                "State": {"Status": "running", "Running": True},
                "NetworkSettings": {"Ports": ports},
                "HostConfig": {"PortBindings": {}},
                "_limits": service.get("deploy", {}).get("resources", {}).get("limits", {}),
            }
            for volume in document.get("volumes", {}).values():
                self.volumes.add(volume["name"])
            self.images.add(service["image"])

    def _health_of(self, name: str) -> str:
        """
        Args:
            name: A container.

        Returns:
            Its next scripted health status.
        """
        scripted = self.health.get(name)
        if not scripted:
            return "healthy"
        return scripted.pop(0) if len(scripted) > 1 else scripted[0]

    # ----------------------------------------------------------- the runner

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        command = tuple(argv)
        for prefix, error in self.fail.items():
            if command[: len(prefix)] == prefix:
                return CommandResult(argv=result.argv, exit_code=1, stderr=error)
        if command[:1] == ("docker",):
            return self._docker(command, result, str(kwargs.get("input") or ""))
        if command[:1] == ("systemctl",):
            self._systemctl(command)
        if command[:1] == ("ss",):
            lines = "".join(
                f'LISTEN 0 128 127.0.0.1:{port} 0.0.0.0:* users:(("x",pid=1,fd=3))\n'
                for port in sorted(self.listening)
            )
            return CommandResult(argv=result.argv, exit_code=0, stdout=lines)
        return result

    def _ok(self, result: CommandResult, stdout: str = "") -> CommandResult:
        return CommandResult(argv=result.argv, exit_code=0, stdout=stdout)

    def _no(self, result: CommandResult, why: str) -> CommandResult:
        return CommandResult(argv=result.argv, exit_code=1, stderr=why)

    def _docker(
        self, command: tuple[str, ...], result: CommandResult, sql: str = ""
    ) -> CommandResult:
        """
        Answer a docker command from what this host holds.

        Args:
            command: The argv.
            result: What the base runner scripted.

        Returns:
            The answer.
        """
        verb = command[1] if len(command) > 1 else ""
        if verb == "compose":
            return self._ok(result, "Docker Compose version v2.40.0\n")
        if verb == "ps":
            return self._ok(
                result,
                "".join(
                    f"{entry['Id']}\t{entry['Config']['Image']}\n"
                    for entry in self.containers.values()
                ),
            )
        if verb == "pull":
            reference = command[2]
            self.images.add(reference)
            return self._ok(result)
        if verb == "inspect":
            return self._inspect(command, result)
        if verb == "rm":
            self.containers.pop(command[-1], None)
            return self._ok(result)
        if verb == "volume" and command[2:3] == ("rm",):
            self.volumes.discard(command[3])
            return self._ok(result)
        if verb == "logs":
            return self._ok(result, self.log)
        if verb == "exec":
            if self.exec_fails:
                return self._no(result, "FATAL: the database system is starting up")
            return self._ok(result, self._psql(sql) or self.exec_stdout)
        return result

    def _psql(self, sql: str) -> str:
        """
        Answer the two questions Noust asks a PostgreSQL about its databases.

        Args:
            sql: The statement sent on stdin.

        Returns:
            What ``psql -t -A`` would print, or an empty string for any other
            statement.
        """
        if "FROM pg_database WHERE datname" in sql:
            found = re.search(r"datname = '([^']+)'", sql)
            return "1\n" if found and found.group(1) in self.databases else ""
        dropped = re.search(r'DROP DATABASE (?:IF EXISTS )?"?([\w-]+)"?', sql)
        if dropped:
            self.databases.pop(dropped.group(1), None)
            return ""
        created = re.search(r'CREATE DATABASE "?([\w-]+)"?', sql)
        if created:
            self.databases[created.group(1)] = "postgres"
            return ""
        if "FROM pg_database d JOIN pg_roles" in sql:
            return "".join(f"{name}|UTF8|8192|{owner}\n" for name, owner in self.databases.items())
        return ""

    def _inspect(self, command: tuple[str, ...], result: CommandResult) -> CommandResult:
        """
        Answer ``docker inspect`` in each of the forms Noust uses.

        Args:
            command: The argv.
            result: What the base runner scripted.

        Returns:
            The answer.
        """
        target = command[-1]
        if "--type" in command:
            kind = command[command.index("--type") + 1]
            if kind == "container":
                return self._ok(result) if target in self.containers else self._no(result, "none")
            if kind == "volume":
                return self._ok(result) if target in self.volumes else self._no(result, "none")
            if kind == "image":
                if target not in self.served and target not in self.images:
                    return self._no(result, "No such image")
                repository = target.split(":")[0].split("@")[0]
                digest = self.served.get(target)
                shown = [f"{repository}@{digest}"] if digest else []
                return self._ok(result, json.dumps(shown))
        if "--format" in command:
            template = command[command.index("--format") + 1]
            if target not in self.containers:
                return self._no(result, "No such object")
            if template == "{{.State.Health.Status}}":
                return self._ok(result, self._health_of(target) + "\n")
            if template == "{{json .Config.Env}}":
                return self._ok(result, json.dumps(self.containers[target]["Config"]["Env"]))
        ids = [part for part in command[2:] if not part.startswith("-")]
        found = [
            {key: value for key, value in entry.items() if not key.startswith("_")}
            for entry in self.containers.values()
            if entry["Id"] in ids
        ]
        return self._ok(result, json.dumps(found))

    def _systemctl(self, command: tuple[str, ...]) -> None:
        """
        Do what a systemd verb does to the units and their containers.

        Args:
            command: The argv.
        """
        if len(command) < 3:
            return
        verb, unit = command[1], command[-1].removesuffix(".service")
        if not unit.startswith("noust-db-"):
            return
        if verb in ("start", "restart"):
            if verb == "restart":
                self._drop(unit)
            self._create_container(unit)
            self.started.append(unit)
        elif verb == "stop":
            self._drop(unit)
        elif verb == "enable":
            self.enabled.add(unit)
        elif verb == "disable":
            self.enabled.discard(unit)

    def _drop(self, unit: str) -> None:
        """
        Do what ``compose down`` does: remove the container, keep the volumes.

        Args:
            unit: The unit.
        """
        self.containers.pop(unit, None)


@dataclass
class World:
    """
    Everything a test of the instances touches.

    Attributes:
        host: Docker and systemd.
        store: The store, installed as the process-wide one.
        service: The database service over it.
        managed: The manager of instances under test.
        root: The test's temporary directory.
        units: Where the unit files are written.
    """

    host: DockerHost
    store: NoustStore
    service: DatabaseService
    managed: ManagedInstances
    root: Path
    units: Path

    def password(self, name: str) -> str:
        """
        Args:
            name: An instance.

        Returns:
            Its superuser's password, read where only root can.
        """
        text = layout.env_path(name).read_text()
        return next(line.split("=", 1)[1] for line in text.splitlines() if "PASSWORD=" in line)

    def fresh(self) -> ManagedInstances:
        """
        Returns:
            A second manager over the same machine, as a later request is.
        """
        return ManagedInstances(
            DatabaseService(store=self.store, secrets=self.service.secrets),
            resources=lambda: Resources(8 * GIB, 4),
        )


def build_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """
    Build a server with Docker and systemd, in a temporary root.

    Args:
        tmp_path: The test's temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The world; :func:`close_world` puts the process-wide state back.
    """
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", units)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (units,))
    monkeypatch.setattr(layout, "instances_dir", lambda: tmp_path / "state" / "db-instances")
    monkeypatch.setattr(managed, "_sleep", lambda _seconds: None)
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    host = DockerHost(tmp_path)
    set_runner(host)
    service = DatabaseService(store=store, secrets=SecretStore(root=tmp_path / "secrets"))
    return World(
        host=host,
        store=store,
        service=service,
        managed=ManagedInstances(service, resources=lambda: Resources(8 * GIB, 4)),
        root=tmp_path,
        units=units,
    )


def close_world() -> None:
    """Put the process-wide runner and store back."""
    set_runner(None)
    NoustStore.reset_instance()
