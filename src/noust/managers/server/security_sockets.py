# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What listens on this server, and who is connected to its SSH right now.

Both come from ``ss``, the tool an operator would run: ``ss -Hltnup`` for the
sockets that listen (whatever the firewall says, these are the doors), and
``ss -Htnp state established`` for the SSH sessions open now - the operator's
and the central's tunnel - which are exactly what a firewall change or a key
removal must not cut. ``ss`` has no JSON output; the columns are stable and
parsed here once.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.host import PROBE_TIMEOUT

#: Environment of every probe.
PROBE_ENV = {"LC_ALL": "C", "TERM": "dumb"}

#: The addresses ``ss`` prints for a socket bound to every interface. Noust
#: binds nothing here; it recognises what others bound.
ANY_ADDRESSES = frozenset({"0.0.0.0", "::", "*", ""})  # noqa: S104

#: ``users:(("nginx",pid=1234,fd=6),...)``: the first process holding a socket.
_PROCESS = re.compile(r'\(\("(?P<name>[^"]*)",pid=(?P<pid>\d+)')


@dataclass(frozen=True)
class Listener:
    """
    One socket that accepts connections.

    Attributes:
        proto: ``tcp`` or ``udp``.
        address: The address it is bound to, without brackets or interface.
        port: The port.
        process: The program holding it, when ``ss`` could see it (root).
        pid: Its process id.
    """

    proto: str
    address: str
    port: int
    process: str | None = None
    pid: int | None = None

    @property
    def exposure(self) -> str:
        """``local`` (loopback only), ``all`` (every interface) or ``interface`` (one address)."""
        if self.address in ANY_ADDRESSES:
            return "all"
        try:
            return "local" if ipaddress.ip_address(self.address).is_loopback else "interface"
        except ValueError:
            return "interface"


@dataclass(frozen=True)
class Connection:
    """
    One established TCP connection.

    Attributes:
        local_address: This server's address.
        local_port: This server's port.
        peer_address: The other end.
        peer_port: The other end's port.
        process: The program holding it.
    """

    local_address: str
    local_port: int
    peer_address: str
    peer_port: int
    process: str | None = None


def _split_endpoint(text: str) -> tuple[str, int] | None:
    """
    Split ``address:port`` as ``ss`` prints it.

    Args:
        text: ``0.0.0.0:22``, ``[::]:22``, ``127.0.0.53%lo:53``, ``*:80``.

    Returns:
        The bare address and the port, or None when the port is not a number
        (``*`` for a peer that is not connected).
    """
    host, _, port = text.rpartition(":")
    if not port.isdigit():
        return None
    host = host.strip("[]")
    host = host.split("%", 1)[0]
    return host, int(port)


def parse_listeners(text: str) -> list[Listener]:
    """
    Read ``ss -Hltnup``.

    Args:
        text: Its output: ``Netid State Recv-Q Send-Q Local Peer [Process]``.

    Returns:
        One entry per socket, TCP and UDP.
    """
    found: list[Listener] = []
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) < 6 or fields[0] not in ("tcp", "udp"):
            continue
        endpoint = _split_endpoint(fields[4])
        if endpoint is None:
            continue
        process = _PROCESS.search(" ".join(fields[6:]))
        found.append(
            Listener(
                proto=fields[0],
                address=endpoint[0],
                port=endpoint[1],
                process=process.group("name") if process else None,
                pid=int(process.group("pid")) if process else None,
            )
        )
    return found


def parse_established(text: str) -> list[Connection]:
    """
    Read ``ss -Htnp state established``, which omits the State column.

    Args:
        text: Its output: ``Recv-Q Send-Q Local Peer [Process]``.

    Returns:
        One entry per connection.
    """
    found: list[Connection] = []
    for raw in text.splitlines():
        endpoints = [
            parsed
            for parsed in (_split_endpoint(field) for field in raw.split() if ":" in field)
            if parsed is not None
        ]
        if len(endpoints) < 2:
            continue
        process = _PROCESS.search(raw)
        (local, local_port), (peer, peer_port) = endpoints[0], endpoints[1]
        found.append(
            Connection(
                local, local_port, peer, peer_port, process.group("name") if process else None
            )
        )
    return found


def read_listeners(runner: CommandRunner | None = None) -> tuple[list[Listener], str]:
    """
    Ask ``ss`` what listens.

    Args:
        runner: The command runner.

    Returns:
        The sockets, and the command's error output when it failed (empty
        when it worked), so a caller can say "unknown" with the reason.
    """
    result = (runner or get_runner()).run(["ss", "-Hltnup"], timeout=PROBE_TIMEOUT, env=PROBE_ENV)
    if not result.success:
        return [], (result.stderr or result.stdout).strip() or f"ss exited {result.exit_code}"
    return parse_listeners(result.stdout), ""


def read_established(runner: CommandRunner | None = None) -> list[Connection]:
    """
    Ask ``ss`` which TCP connections are open now.

    Args:
        runner: The command runner.

    Returns:
        The connections; empty when ``ss`` failed.
    """
    result = (runner or get_runner()).run(
        ["ss", "-Htnp", "state", "established"], timeout=PROBE_TIMEOUT, env=PROBE_ENV
    )
    return parse_established(result.stdout) if result.success else []
