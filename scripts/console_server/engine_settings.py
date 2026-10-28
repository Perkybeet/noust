# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Engine settings and database containers (3.3): the files an engine's settings page reads and
the Compose services a database container is."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import server_host
from .server_host import _PG_CONFIG_DIR

# ---------------------------------------------------------------------------
# Console: engine settings and database containers (3.3). An engine's settings page
# reads the cluster and the engine's own answers (pg_settings, SHOW GLOBAL VARIABLES)
# and writes Noust's file under the host tree; the model reads that same file back, so a
# change made through the API is what the next read reports, as after a real restart.
# A database container is a Compose service of the modelled machine: Docker's listing,
# inspect, start/stop and the client Noust runs inside it, answered from one model.
# ---------------------------------------------------------------------------

#: Debian's ``pg_lsclusters --no-header`` for the one cluster the modelled machine has:
#: version, name, port, status, owner, data directory, log file.
_PG_CLUSTER_LINE = (
    "16  main    5432 online postgres /var/lib/postgresql/16/main "
    "/var/log/postgresql/postgresql-16-main.log\n"
)

#: The cluster's own systemd unit, which a settings change restarts. systemd runs it as a
#: template instance; the model answers it as the machine's ``postgresql`` unit.
_PG_CLUSTER_UNIT = "postgresql@16-main"

#: What ``pg_settings`` reports for each setting Noust edits before it writes anything:
#: the value as ``current_setting`` prints it, the ``context`` (``postmaster`` needs a
#: restart) and the file that sets it ("" for the compiled-in default).
_PG_SETTING_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "listen_addresses": ("localhost", "postmaster", ""),
    "port": ("5432", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "max_connections": ("100", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "shared_buffers": ("128MB", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "effective_cache_size": ("4GB", "user", ""),
    "work_mem": ("4MB", "user", ""),
    "maintenance_work_mem": ("64MB", "user", ""),
    "log_min_duration_statement": ("-1", "superuser", ""),
    "timezone": ("Europe/Madrid", "user", f"{_PG_CONFIG_DIR}/postgresql.conf"),
}

#: What ``SHOW GLOBAL VARIABLES`` reports for each variable Noust edits on a stock MySQL 8.0
#: (sizes in bytes, ``long_query_time`` with its six decimals).
_MYSQL_VARIABLE_DEFAULTS: dict[str, str] = {
    "bind_address": "127.0.0.1",
    "port": "3306",
    "max_connections": "151",
    "innodb_buffer_pool_size": "134217728",
    "innodb_log_file_size": "50331648",
    "slow_query_log": "OFF",
    "long_query_time": "10.000000",
    "character_set_server": "utf8mb4",
    "time_zone": "SYSTEM",
}

#: The file each of Noust's settings writes, below the host tree.
_PG_NOUST_FILE = f"{_PG_CONFIG_DIR}/conf.d/90-noust.conf"
_MYSQL_NOUST_FILE = "/etc/mysql/mysql.conf.d/99-noust.cnf"


def _file_assignments(path: Path) -> dict[str, str]:
    """
    Read the ``key = value`` lines of a file the modelled engine reads.

    Args:
        path: The file; a missing one sets nothing.

    Returns:
        The last value of each key, without quotes or a trailing comment.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "[")):
            continue
        key, separator, value = stripped.partition("=")
        if separator:
            found[key.strip()] = value.split(" #", 1)[0].strip().strip("'\"")
    return found


def _postgres_settings(host: Path | None) -> dict[str, tuple[str, str, str]]:
    """
    What the modelled PostgreSQL reports: its defaults, with Noust's file over them.

    Args:
        host: The host tree's root; None answers the defaults alone.

    Returns:
        ``(value, context, source file)`` by setting.
    """
    settings = dict(_PG_SETTING_DEFAULTS)
    written = _file_assignments(host / _PG_NOUST_FILE.lstrip("/")) if host else {}
    for key, value in written.items():
        if key in settings:
            settings[key] = (value, settings[key][1], _PG_NOUST_FILE)
    return settings


def _mysql_bytes(value: str) -> str:
    """
    Turn an option file's size (``256M``) into the bytes a variable reports.

    Args:
        value: The option as written.

    Returns:
        The decimal bytes, or the text itself when it is not a size.
    """
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    if value[-1:].upper() in units and value[:-1].isdigit():
        return str(int(value[:-1]) * units[value[-1].upper()])
    return value


def _mysql_variables(host: Path | None) -> dict[str, str]:
    """
    What the modelled MySQL reports: its defaults, with Noust's file over them.

    Args:
        host: The host tree's root; None answers the defaults alone.

    Returns:
        The value of each variable, as ``SHOW GLOBAL VARIABLES`` prints it.
    """
    variables = dict(_MYSQL_VARIABLE_DEFAULTS)
    written = _file_assignments(host / _MYSQL_NOUST_FILE.lstrip("/")) if host else {}
    for option, value in written.items():
        name = "time_zone" if option == "default-time-zone" else option.replace("-", "_")
        if name not in variables:
            continue
        if name in ("innodb_buffer_pool_size", "innodb_log_file_size"):
            value = _mysql_bytes(value)
        elif name == "long_query_time":
            value = f"{float(value):.6f}"
        variables[name] = value
    return variables


def _host_tree() -> Path | None:
    """
    Returns:
        The modelled server's host tree, once the Server area's model is built.
    """
    return server_host._SERVER_HOST.root if server_host._SERVER_HOST is not None else None


#: The Compose project of the modelled machine's one database container. It is the name of
#: a seeded application's directory (``catalogo.example.org``), which is how the console
#: tells whose database it is: ``assign_apps`` matches a project to ``app_name``.
DB_CONTAINER_PROJECT = "catalogo-example-org"

#: The Compose service and the container Compose names after it.
DB_CONTAINER_SERVICE = "postgres"
DB_CONTAINER_NAME = f"{DB_CONTAINER_PROJECT}-{DB_CONTAINER_SERVICE}-1"

#: The database that container's Postgres holds, and the account that owns it (the image's
#: ``POSTGRES_USER`` is its superuser).
DB_CONTAINER_DATABASE = "catalogo"

#: The host port the container publishes its 5432 on: the host's own PostgreSQL has 5432.
DB_CONTAINER_HOST_PORT = 5433


@dataclass
class _DatabaseContainer:
    """
    One container of the modelled machine that runs a database.

    Attributes:
        name: The container's name.
        image: The image it was created from.
        project: Its Compose project.
        service: Its Compose service.
        env: Its environment, as ``docker inspect`` lists it.
        container_port: The port its engine listens on inside it.
        host_port: The host port that is published to it.
        databases: The databases its engine holds: owner and size in bytes.
        users: What its role listing answers.
        state: ``running`` or ``exited``; ``docker start`` and ``stop`` change it.
    """

    name: str
    image: str
    project: str
    service: str
    env: tuple[str, ...]
    container_port: int
    host_port: int
    databases: dict[str, tuple[str, int]]
    users: str
    state: str = "running"

    @property
    def container_id(self) -> str:
        """The 64 hex digits Docker would give it, fixed by its name."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def inspect(self) -> dict[str, Any]:
        """
        Returns:
            What ``docker inspect`` prints for it: only the fields Noust reads.
        """
        port = f"{self.container_port}/tcp"
        binding = [{"HostIp": "127.0.0.1", "HostPort": str(self.host_port)}]
        return {
            "Id": self.container_id,
            "Name": f"/{self.name}",
            "State": {"Status": self.state, "Running": self.state == "running"},
            "Config": {
                "Image": self.image,
                "Env": list(self.env),
                "ExposedPorts": {port: {}},
                "Labels": {
                    "com.docker.compose.project": self.project,
                    "com.docker.compose.service": self.service,
                    "com.docker.compose.container-number": "1",
                    "com.docker.compose.project.working_dir": f"/var/www/apps/{self.project}",
                },
            },
            "NetworkSettings": {"Ports": {port: binding} if self.state == "running" else {}},
            "HostConfig": {"PortBindings": {port: binding}},
        }


class _DatabaseContainers:
    """Docker's answers about the machine's database containers, and what runs in them."""

    @property
    def listing_argv(self) -> tuple[str, ...]:
        """
        Returns:
            How discovery asks ``docker ps``: every container, with the id, the image and the
            label of an instance Noust made (the modelled container carries none).
        """
        from noust.managers.database.instance_layout import MANAGED_LABEL

        return (
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--format",
            '{{.ID}}\t{{.Image}}\t{{.Label "' + MANAGED_LABEL + '"}}',
        )

    def __init__(self) -> None:
        """Model the one container: Postgres 16 of ``catalogo.example.org``'s Compose project."""
        self.lock = threading.Lock()
        catalogo = _DatabaseContainer(
            name=DB_CONTAINER_NAME,
            image="postgres:16-alpine",
            project=DB_CONTAINER_PROJECT,
            service=DB_CONTAINER_SERVICE,
            env=(
                "POSTGRES_USER=catalogo",
                f"POSTGRES_DB={DB_CONTAINER_DATABASE}",
                "POSTGRES_PASSWORD=Cat-demo-4Rn8",
                "PGDATA=/var/lib/postgresql/data",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ),
            container_port=5432,
            host_port=DB_CONTAINER_HOST_PORT,
            databases={
                "postgres": ("catalogo", 7_553_827),
                DB_CONTAINER_DATABASE: ("catalogo", 31_457_280),
            },
            users=f"catalogo|t|t|t|postgres,{DB_CONTAINER_DATABASE}\n",
        )
        self.containers = {catalogo.name: catalogo}

    def listing(self) -> str:
        """
        Returns:
            What ``docker ps`` prints for :attr:`listing_argv`: an empty label column is left out.
        """
        with self.lock:
            return "".join(f"{c.container_id}\t{c.image}\n" for c in self.containers.values())

    def inspect(self, ids: Sequence[str]) -> str | None:
        """
        Args:
            ids: The arguments of ``docker inspect``.

        Returns:
            Its JSON for the containers named, or None when an argument names none of the
            modelled ones (another caller's question, left to the rest of the runner).
        """
        with self.lock:
            found = [c for c in self.containers.values() if c.container_id in ids or c.name in ids]
            if not ids or len(found) != len(ids):
                return None
            return json.dumps([c.inspect() for c in found])

    def named(self, name: str) -> _DatabaseContainer | None:
        """
        Args:
            name: A container's name or id.

        Returns:
            The modelled container, or None.
        """
        with self.lock:
            return next(
                (c for c in self.containers.values() if name in (c.name, c.container_id)), None
            )

    def exec_target(
        self, args: tuple[str, ...]
    ) -> tuple[_DatabaseContainer, tuple[str, ...]] | None:
        """
        Read the ``docker exec`` Noust builds for a client inside a database container.

        Args:
            args: The argv.

        Returns:
            The container and the command that runs in it (program first), or None when
            this is not such an exec.
        """
        from noust.managers.database.instances import CLIENT_SCRIPT

        if args[:2] != ("docker", "exec") or CLIENT_SCRIPT not in args:
            return None
        position = args.index(CLIENT_SCRIPT)
        container = self.named(args[position - 3]) if position >= 3 else None
        # After the script: "sh", the mode, the variable, the sources, then the programs to
        # try (comma separated, the first preferred) and the program's own arguments.
        if container is None or len(args) < position + 6:
            return None
        return container, (args[position + 5].split(",")[0], *args[position + 6 :])

    def answer(self, args: tuple[str, ...]) -> tuple[int, str, str] | None:
        """
        Answer a ``docker`` command about a modelled container.

        Args:
            args: The argv.

        Returns:
            ``(exit code, stdout, stderr)``, or None for any other docker command.
        """
        verb = args[1] if len(args) > 1 else ""
        if args == self.listing_argv:
            return 0, self.listing(), ""
        if verb == "inspect" and not any(a.startswith("-") for a in args[2:]):
            printed = self.inspect(args[2:])
            return (0, printed, "") if printed is not None else None
        container = self.named(args[-1]) if len(args) > 2 else None
        if container is None:
            return None
        if verb in ("start", "restart"):
            container.state = "running"
            return 0, f"{container.name}\n", ""
        if verb == "stop":
            container.state = "exited"
            return 0, f"{container.name}\n", ""
        if verb == "logs":
            started = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            return (
                0,
                "",
                f"{started}.318 UTC [1] LOG:  starting PostgreSQL 16.4 on x86_64-pc-linux-musl\n"
                f'{started}.318 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432\n'
                f"{started}.331 UTC [1] LOG:  database system is ready to accept connections\n",
            )
        return None
