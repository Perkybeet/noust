# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database ports open to the network, and how to reach a database without one.

:func:`find_exposed_database_ports` is the one check for "a database is
listening beyond this machine". The databases page shows it, and the
server's security checks (``noust health``, Server > Security) call the same
function rather than a second copy. It asks the kernel (``ss -ltnpH``) what
listens where, which is the truth whatever the configuration files say, and
Docker what it publishes: a compose file that maps ``5435:5432`` publishes the
container's PostgreSQL on every address of the host, and nothing in
PostgreSQL's own configuration shows it (owner feedback 8).

A port Docker publishes is not an exposure when the operator refuses it in the
``DOCKER-USER`` chain on the public interface, or when it is an IPv6 publication
on a server nothing reaches over IPv6. That is the server security check's own
reading (:class:`~noust.managers.server.security_docker_user.DockerUserReader`,
used here as is, never a second parser), and such a port is still listed, flagged
``firewalled`` with the rule that closes it, so what the operator did stays
visible and does not raise an alarm (item 72).

It never needs a database engine to be installed or running, and never asks
one anything, so it also runs on a central (``hub``) that has none.

:func:`tunnel_instructions` is the other half: the SSH tunnel an operator
opens from their own computer, built from the server's facts. Noust never
opens a database port to offer the same thing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from noust.core.runner import CommandRunner, get_runner
from noust.managers.database.base import is_loopback
from noust.managers.server.security_docker_user import DockerUserReader
from noust.managers.server.security_sockets import ANY_ADDRESSES

#: Deadline for ``ss`` and ``docker ps``.
PROBE_TIMEOUT = 30

#: The ports each engine listens on by default, and MySQL's X protocol.
DEFAULT_PORTS: dict[int, str] = {
    5432: "postgresql",
    3306: "mysql",
    33060: "mysql",
    6379: "redis",
    27017: "mongodb",
}

#: Server processes, by the name ``ss`` shows, and the engine they are.
PROCESS_ENGINES: dict[str, str] = {
    "postgres": "postgresql",
    "postmaster": "postgresql",
    "mysqld": "mysql",
    "mariadbd": "mysql",
    "redis-server": "redis",
    "valkey-server": "redis",
    "mongod": "mongodb",
}

#: Image names that are a database, and which one. Matched against the image
#: without its registry and tag: ``docker.io/library/postgres:16`` is postgres.
IMAGE_ENGINES: tuple[tuple[str, str], ...] = (
    ("postgis", "postgresql"),
    ("postgres", "postgresql"),
    ("timescaledb", "postgresql"),
    ("mariadb", "mysql"),
    ("mysql", "mysql"),
    ("valkey", "redis"),
    ("redis", "redis"),
    ("mongo", "mongodb"),
)

#: How to close each engine's port, in the engine's own configuration.
ENGINE_ADVICE: dict[str, str] = {
    "postgresql": (
        "Set listen_addresses = 'localhost' in postgresql.conf and restart PostgreSQL "
        "(systemctl restart postgresql). Reach it from your computer through an SSH tunnel."
    ),
    "mysql": (
        "Set bind-address = 127.0.0.1 (and mysqlx-bind-address = 127.0.0.1 on MySQL) in "
        "the server's configuration and restart it. Reach it through an SSH tunnel."
    ),
    "redis": (
        "Set bind 127.0.0.1 -::1 and protected-mode yes in redis.conf and restart the "
        "server. Reach it through an SSH tunnel."
    ),
    "mongodb": (
        "Set net.bindIp: 127.0.0.1 in /etc/mongod.conf and restart mongod. Reach it "
        "through an SSH tunnel."
    ),
}

#: How to close a port Docker publishes.
DOCKER_ADVICE = (
    "Publish the port on the loopback only ('127.0.0.1:{host_port}:{container_port}' in the "
    "compose file's ports), or remove the ports entry and let the application reach the "
    "database over the Docker network. Docker writes its own firewall rules, so a host "
    "firewall does not close a published port."
)

#: What to say about a published port the firewall already keeps the Internet out of.
FIREWALLED_ADVICE = (
    "The Internet does not reach it. To close it to every other address as well, publish it "
    "on the loopback only ('127.0.0.1:{host_port}:{container_port}' in the compose file's "
    "ports), which does not depend on a firewall rule surviving a reboot."
)

#: One published port in ``docker ps``: ``0.0.0.0:5435->5432/tcp`` or ``[::]:5435->5432/tcp``.
_PUBLISHED = re.compile(r"(?P<address>\[[^\]]*\]|[^,\s\[]*?):(?P<host>\d+)->(?P<container>\d+)/tcp")

#: A process name in ``ss``'s users column: ``users:(("postgres",pid=812,fd=6))``.
_PROCESS = re.compile(r'\("([^"]+)"')


@dataclass(frozen=True)
class ExposedPort:
    """
    A database port that accepts connections from beyond this machine.

    Attributes:
        engine: The engine it belongs to, as far as can be told.
        port: The port on this host.
        address: The address it is bound to (``0.0.0.0``, ``::``, ``*`` or a
            public address).
        process: The process ``ss`` names, when it names one.
        source: ``engine`` for a server running on the host, ``docker`` for a
            container's port Docker publishes.
        container: The container's name, for ``docker``.
        image: The container's image, for ``docker``.
        advice: How to close it, in English.
        container_port: The port inside the container, for ``docker``: it is
            what a ``DOCKER-USER`` rule that names a port sees.
        firewalled: Something the server can prove keeps the Internet out of
            it: a ``DOCKER-USER`` rule refusing it on the public interface, or
            an IPv6 publication with no IPv6 route to the server. The port is
            still listed so the operator sees it, but it is not an exposure.
        closed_by: What closes it, when ``firewalled`` (``the DOCKER-USER chain
            on ens6``).
        rule: The ``DOCKER-USER`` rule that refuses it, verbatim, when there is
            one.
    """

    engine: str
    port: int
    address: str
    process: str | None
    source: str
    container: str | None = None
    image: str | None = None
    advice: str = ""
    container_port: int | None = None
    firewalled: bool = False
    closed_by: str = ""
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Render the finding as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass(frozen=True)
class Listener:
    """
    One listening TCP socket, as ``ss`` reports it.

    Attributes:
        address: The local address, without brackets.
        port: The local port.
        processes: The processes holding it.
    """

    address: str
    port: int
    processes: tuple[str, ...] = ()


def listening_sockets(runner: CommandRunner | None = None) -> list[Listener]:
    """
    Ask the kernel which TCP sockets listen, and who holds them.

    Args:
        runner: The runner to ask through. Defaults to the process-wide one.

    Returns:
        Every listening socket; empty when ``ss`` is missing or fails.
    """
    active = runner or get_runner()
    result = active.run(["ss", "-ltnpH"], timeout=PROBE_TIMEOUT)
    if not result.success:
        return []
    sockets: list[Listener] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        address, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        # ss prints an interface scope as "0.0.0.0%eth0" and IPv6 in brackets.
        address = address.strip("[]").split("%", 1)[0]
        sockets.append(
            Listener(
                address=address,
                port=int(port),
                processes=tuple(_PROCESS.findall(" ".join(fields[5:]))),
            )
        )
    return sockets


def _image_engine(image: str) -> str | None:
    """
    Tell which database an image runs, from its name.

    Args:
        image: The image, as ``docker ps`` prints it.

    Returns:
        The engine, or None for an image that is not a database.
    """
    name = image.rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0].lower()
    for fragment, engine in IMAGE_ENGINES:
        if fragment in name:
            return engine
    return None


def published_database_ports(runner: CommandRunner | None = None) -> list[ExposedPort]:
    """
    List the database containers' ports Docker publishes beyond the loopback.

    Args:
        runner: The runner to ask through. Defaults to the process-wide one.

    Returns:
        One entry per published port; empty without Docker.
    """
    active = runner or get_runner()
    if not active.exists("docker"):
        return []
    result = active.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}"],
        timeout=PROBE_TIMEOUT,
    )
    if not result.success:
        return []
    found: list[ExposedPort] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, image, ports = parts[0], parts[1], parts[2]
        engine = _image_engine(image)
        if engine is None:
            continue
        for match in _PUBLISHED.finditer(ports):
            address = match.group("address").strip("[]") or "0.0.0.0"  # noqa: S104 - reporting a binding
            if is_loopback(address):
                continue
            found.append(
                ExposedPort(
                    engine=engine,
                    port=int(match.group("host")),
                    address=address,
                    process="docker-proxy",
                    source="docker",
                    container=name,
                    image=image,
                    advice=DOCKER_ADVICE.format(
                        host_port=match.group("host"), container_port=match.group("container")
                    ),
                    container_port=int(match.group("container")),
                )
            )
    return found


def _closed(entry: ExposedPort, closed_by: str, rule: str) -> ExposedPort:
    """
    Flag a published port as closed to the Internet.

    Args:
        entry: The published port.
        closed_by: What closes it.
        rule: The ``DOCKER-USER`` rule that does, verbatim; empty when none.

    Returns:
        The port, ``firewalled``, with advice that no longer says it is open.
    """
    return replace(
        entry,
        firewalled=True,
        closed_by=closed_by,
        rule=rule,
        advice=FIREWALLED_ADVICE.format(host_port=entry.port, container_port=entry.container_port),
    )


def _judged_by_the_firewall(entries: list[ExposedPort], runner: CommandRunner) -> list[ExposedPort]:
    """
    Flag the published ports the firewall already keeps the Internet out of.

    The same question, answered by the same code, as the server's security check
    (``fw.docker_bypass``): a ``DROP`` or ``REJECT`` in ``DOCKER-USER`` for the
    port on the public interface, or an IPv6 publication on a server with no
    IPv6 route. Only a publication on every address can be proven closed that
    way; one on a specific address stays as it was. A chain that cannot be read
    proves nothing, and says so in the port's advice.

    Args:
        entries: The ports Docker publishes.
        runner: The runner to read the chain through.

    Returns:
        The same ports, in the same order, the closed ones flagged.
    """
    if not any(e.container_port is not None and e.address in ANY_ADDRESSES for e in entries):
        return entries  # nothing to judge: do not even read the chain
    reader = DockerUserReader(runner)
    judged: list[ExposedPort] = []
    for entry in entries:
        if entry.container_port is None or entry.address not in ANY_ADDRESSES:
            judged.append(entry)
            continue
        unrouted = reader.unrouted(entry.address)
        if unrouted:
            judged.append(_closed(entry, unrouted, ""))
            continue
        coverage = reader.covering(entry.address, entry.port, entry.container_port, "tcp")
        if coverage is not None:
            judged.append(
                _closed(entry, f"the DOCKER-USER chain on {coverage.interfaces}", coverage.rule)
            )
            continue
        error = reader.read_error(entry.address)
        if error:
            entry = replace(
                entry,
                advice=f"{entry.advice} The DOCKER-USER chain could not be read ({error}), "
                "so a rule there is not taken into account.",
            )
        judged.append(entry)
    return judged


def find_exposed_database_ports(
    runner: CommandRunner | None = None,
    *,
    extra_ports: dict[int, str] | None = None,
    include_firewalled: bool = False,
) -> list[ExposedPort]:
    """
    Find every database port reachable from beyond this machine.

    A socket is a database's when a database server holds it, or when it is
    one of the engines' ports and nothing says otherwise; Docker's published
    ports are added from ``docker ps``, where the image says which database
    it is. Only sockets bound to a non-loopback address are reported: a
    database on ``127.0.0.1`` is reachable through an SSH tunnel and nothing
    else. A published port the firewall refuses on the public interface is not
    reachable either: it is left out, or kept and flagged ``firewalled`` with
    ``include_firewalled``.

    Args:
        runner: The runner to ask through. Defaults to the process-wide one.
        extra_ports: Ports a caller knows an engine listens on besides the
            defaults, such as a PostgreSQL moved to 5433.
        include_firewalled: Also return the ports the firewall keeps the
            Internet out of, flagged, for a caller that shows them.

    Returns:
        The exposed ports, one per port and address, sorted by port.
    """
    active = runner or get_runner()
    known = {**DEFAULT_PORTS, **(extra_ports or {})}
    published = published_database_ports(active)
    docker_ports = {entry.port for entry in published}

    found: dict[tuple[int, str], ExposedPort] = {}
    for socket in listening_sockets(active):
        if is_loopback(socket.address):
            continue
        engine = next(
            (PROCESS_ENGINES[p] for p in socket.processes if p in PROCESS_ENGINES),
            None,
        )
        if engine is None:
            if "docker-proxy" in socket.processes and socket.port in docker_ports:
                continue  # reported from docker ps, with the container's name
            engine = known.get(socket.port)
        if engine is None:
            continue
        found[(socket.port, socket.address)] = ExposedPort(
            engine=engine,
            port=socket.port,
            address=socket.address or "*",
            process=socket.processes[0] if socket.processes else None,
            source="engine",
            advice=ENGINE_ADVICE.get(engine, ""),
        )
    for entry in _judged_by_the_firewall(published, active):
        found.setdefault((entry.port, entry.address), entry)
    return sorted(
        (entry for entry in found.values() if include_firewalled or not entry.firewalled),
        key=lambda entry: (entry.port, entry.address),
    )


@dataclass(frozen=True)
class TunnelInstructions:
    """
    How to reach a database from the operator's own computer.

    Attributes:
        server: The address the operator's SSH client connects to.
        ssh_port: The server's SSH port.
        ssh_user: The account the command signs in as.
        local_port: The port the tunnel opens on the operator's computer.
        remote_port: The engine's port on the server.
        command: The complete ``ssh -L`` command.
        url: The connection string through the tunnel, password masked.
        clients: Ready lines for the engine's own client and for JDBC.
    """

    server: str
    ssh_port: int
    ssh_user: str
    local_port: int
    remote_port: int
    command: str
    url: str
    clients: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the instructions as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


def ssh_port(runner: CommandRunner | None = None) -> int:
    """
    Find the port sshd listens on.

    Args:
        runner: The runner to ask through.

    Returns:
        The first port an ``sshd`` process listens on, or 22.
    """
    for socket in listening_sockets(runner):
        if "sshd" in socket.processes:
            return socket.port
    return 22


def public_address(runner: CommandRunner | None = None) -> str | None:
    """
    Find this server's first global IPv4 address.

    Args:
        runner: The runner to ask through.

    Returns:
        The address, or None when there is none.
    """
    active = runner or get_runner()
    result = active.run(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"], timeout=PROBE_TIMEOUT
    )
    if not result.success:
        return None
    for line in result.stdout.splitlines():
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/", line)
        if match:
            return match.group(1)
    return None


def tunnel_instructions(
    engine: str,
    *,
    database: str,
    username: str | None,
    remote_port: int,
    server: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
    local_port: int | None = None,
    masked_url: str,
) -> TunnelInstructions:
    """
    Build the SSH tunnel an operator opens to reach a database.

    The tunnel's far end is ``127.0.0.1`` on the server, never the server's
    public name: the engine only listens on the loopback, and PostgreSQL's
    own documentation insists on it for the same reason.

    Args:
        engine: Canonical engine name.
        database: The database.
        username: The account the clients sign in as, if known.
        remote_port: The engine's port on the server.
        server: The address the operator reaches the server at.
        ssh_port: The server's SSH port.
        ssh_user: The account to sign in to the server as.
        local_port: The port to open locally; the engine's port plus 10000
            when not given, so a database running on the operator's own
            computer is not shadowed.
        masked_url: The connection string with the tunnel's local port and
            the password masked, from the caller that knows the engine.

    Returns:
        The instructions.
    """
    local = local_port or (remote_port + 10000 if remote_port + 10000 < 65536 else remote_port)
    port_flag = f" -p {ssh_port}" if ssh_port != 22 else ""
    command = f"ssh -N -L {local}:127.0.0.1:{remote_port}{port_flag} {ssh_user}@{server}"
    user = username or "<user>"
    clients: dict[str, str] = {}
    if engine == "postgresql":
        clients["psql"] = f"psql -h 127.0.0.1 -p {local} -U {user} -d {database}"
        clients["jdbc"] = f"jdbc:postgresql://127.0.0.1:{local}/{database}"
    elif engine == "mysql":
        clients["mysql"] = f"mysql -h 127.0.0.1 -P {local} -u {user} -p {database}"
        clients["jdbc"] = f"jdbc:mysql://127.0.0.1:{local}/{database}"
    elif engine == "redis":
        clients["redis-cli"] = f"redis-cli -h 127.0.0.1 -p {local} -n {database}" + (
            f" --user {user} --askpass" if username and username != "default" else " --askpass"
        )
    elif engine == "mongodb":
        clients["mongosh"] = (
            f'mongosh "mongodb://127.0.0.1:{local}/{database}" --username {user} --password'
        )
    return TunnelInstructions(
        server=server,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        local_port=local,
        remote_port=remote_port,
        command=command,
        url=masked_url,
        clients=clients,
    )
