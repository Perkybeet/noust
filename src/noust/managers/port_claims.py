# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one answer to "may this port be taken" (checklist E-44).

A port nobody checks is a silent outage: two things are told to listen on one number and the
second to start loses, at three in the morning, with an error from a tool that does not know
who has the first. Before this there were three checks that did not know of each other: an
application's port against the other applications' (stopped ones too), a published stream's
against the web server's, SSH's and the databases made reachable, and a database engine's
public port against whatever listened. A Compose stack's ``ports:`` were in none of them, so
Docker's own error was the only guard.

Every holder is one source here, and every picker and validator asks this module:

- ``system``: the web server's ports, SSH's and the console's, over both protocols;
- ``app``: an application's port, the one after it in zero-downtime mode, and the ports of
  its extra copies, stopped or not;
- ``stack``: the host ports a Compose stack's file publishes, read by the deployer's own
  reader (:func:`~noust.deployers.docker_compose.stack_published_ports`), TCP or UDP and bound
  to the address the file names;
- ``stream``: a TCP or UDP port published through nginx;
- ``database``: the public port of a database made reachable;
- ``instance``: the loopback port of an engine Noust runs itself;
- ``process``: whatever the kernel says listens (``ss``), with the process holding it.

Two claims conflict when they are on the same port and protocol and their addresses can
overlap: a socket on ``127.0.0.1`` and a publication on ``192.168.1.5`` do not, any address and
a wildcard do. The caller's own claims never count against it (``owner``), so an application
asking for the port it already has is not in its own way.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.config import Config
from noust.core.database_exposures import ExposureStore
from noust.core.db_instance_store import DbInstanceStore
from noust.core.exceptions import NoustError, PortConflictError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore, get_store, is_compose_backed
from noust.core.stream_store import StreamStore
from noust.core.teams.scope import current_scope
from noust.deployers.helpers.compose_ports import PortMapping, parse_ports
from noust.managers.database.exposure import listening_sockets, ssh_port
from noust.validators.port import validate_port

if TYPE_CHECKING:
    from noust.core.store import App

__all__ = [
    "WEB_PORTS",
    "PortClaim",
    "PortClaims",
    "claimed_ports",
    "parse_ports",
    "reserved_ports",
    "stack_published_ports",
]

log = logging.getLogger(__name__)

#: Ports that belong to the web server itself.
WEB_PORTS = frozenset({80, 443})

TCP = "tcp"
UDP = "udp"

SYSTEM = "system"
APP = "app"
STACK = "stack"
STREAM = "stream"
DATABASE = "database"
INSTANCE = "instance"
PROCESS = "process"

#: The addresses that mean "every interface", as ``ss`` and Compose spell them.
_ANY = frozenset({"", "*", "0.0.0.0", "::"})  # noqa: S104 - recognised, never bound

#: What reading a stack or a store row can fail with: the stack then claims nothing and Docker's
#: own error stays the last guard, as it was before there was a registry.
_UNREADABLE = (NoustError, OSError, ValueError)


@dataclass(frozen=True)
class PortClaim:
    """
    One thing that holds a port.

    Attributes:
        port: The port.
        protocol: ``tcp`` or ``udp``.
        kind: ``system``, ``app``, ``stack``, ``stream``, ``database``, ``instance`` or
            ``process``.
        owner: Whose it is: the application's domain, the stream's name, the process.
        address: The address it is bound to; None for every address.
        detail: Where the claim comes from, for the sentence ("its copy", "zero-downtime").
        processes: For a socket the kernel reports, the processes holding it.
    """

    port: int
    protocol: str
    kind: str
    owner: str
    address: str | None = None
    detail: str = ""
    processes: tuple[str, ...] = ()

    def describe(self, *, reveal: bool = False) -> str:
        """
        Say what holds the port.

        Args:
            reveal: Name an application. A caller that may not see every application leaves
                it False, so that asking for a port is not a way to list them.

        Returns:
            A predicate that completes "Port 3000 ...".
        """
        if self.kind == SYSTEM:
            return f"is {self.owner}"
        if self.kind == APP:
            if not reveal:
                return "belongs to another application"
            return f"is the port of {self.owner}" + (f" ({self.detail})" if self.detail else "")
        if self.kind == STACK:
            whose = self.owner if reveal else "another application"
            return f"is published by the Compose stack of {whose}"
        if self.kind == STREAM:
            return f"is already published as {self.owner}"
        if self.kind == DATABASE:
            return f"is the public port of the database {self.owner}"
        if self.kind == INSTANCE:
            return f"is the port of the database engine {self.owner}"
        return f"is already in use ({self.owner})"


def reserved_ports(runner: CommandRunner) -> dict[int, str]:
    """
    Name the ports that belong to the machine's own services.

    The one answer to "may a port be published, or proxied to": the web server's, SSH's and the
    console's are never for a service an operator adds.

    Args:
        runner: Reads SSH's configured port.

    Returns:
        Port to whose it is.
    """
    reserved = dict.fromkeys(WEB_PORTS, "the web server's")
    reserved[ssh_port(runner)] = "SSH's"
    reserved[int(Config().get("web.port", 8080) or 8080)] = "the console's"
    return reserved


def stack_published_ports(app: App, runner: CommandRunner | None = None) -> list[PortMapping]:
    """
    Read the host ports a Compose application's file publishes.

    Imported where it is used because the deployers import the managers, and this module is
    imported by the managers.

    Args:
        app: The application's row.
        runner: The runner the unit is read through.

    Returns:
        The mappings that name a host port; see
        :func:`~noust.deployers.docker_compose.stack_published_ports`.
    """
    from noust.deployers.docker_compose import stack_published_ports as read

    return read(app, runner=runner)


def _overlap(first: str | None, second: str | None) -> bool:
    """
    Tell whether two bind addresses can hold the same port at once.

    Args:
        first: An address, or None for every address.
        second: Another.

    Returns:
        True when either is a wildcard or they are the same address.
    """
    if first is None or second is None:
        return True
    if first in _ANY or second in _ANY:
        return True
    try:
        return ipaddress.ip_address(first) == ipaddress.ip_address(second)
    except ValueError:
        return first == second


class PortClaims:
    """
    The registry of who holds which port, read when asked.

    Args:
        runner: Runs ``ss``; the process-wide runner by default.
        store: The store; the process-wide one by default.
    """

    def __init__(self, runner: CommandRunner | None = None, store: NoustStore | None = None):
        self._runner = runner
        self._store = store

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # -- Sources ---------------------------------------------------------------------------

    def _system(self) -> list[PortClaim]:
        """Returns: The machine's own services, over both protocols."""
        return [
            PortClaim(port, protocol, SYSTEM, whose)
            for port, whose in reserved_ports(self.runner).items()
            for protocol in (TCP, UDP)
        ]

    def _apps(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            What the applications hold: nothing over UDP, an application listens on TCP.
        """
        if protocol != TCP:
            return []
        return [
            PortClaim(port, TCP, APP, domain, detail=detail)
            for port, domain, detail in self.store.app_port_claims()
        ]

    def _stacks(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            The host ports each Compose stack's file publishes, in this protocol.
        """
        found: list[PortClaim] = []
        for app in self.store.list_apps():
            if not is_compose_backed(app.app_type):
                continue
            try:
                mappings = stack_published_ports(app, self.runner)
            except _UNREADABLE as exc:
                log.debug("The ports of %s were not read: %s", app.domain, exc)
                continue
            found.extend(
                PortClaim(mapping.host_port, protocol, STACK, app.domain, address=mapping.host_ip)
                for mapping in mappings
                if mapping.host_port is not None and mapping.protocol == protocol
            )
        return found

    def _streams(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            The ports published through nginx.
        """
        return [
            PortClaim(row.listen_port, protocol, STREAM, row.name)
            for row in StreamStore(self.store).all()
            if row.protocol == protocol
        ]

    def _databases(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            The public ports of the databases made reachable (always TCP).
        """
        if protocol != TCP:
            return []
        return [
            PortClaim(row.port, TCP, DATABASE, f"{row.engine}/{row.db_name}")
            for row in ExposureStore(self.store).all()
        ]

    def _instances(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            The loopback ports of the engines Noust runs itself.
        """
        if protocol != TCP:
            return []
        return [
            PortClaim(row.host_port, TCP, INSTANCE, row.name, address="127.0.0.1")
            for row in DbInstanceStore(self.store).all()
        ]

    def _listeners(self, protocol: str) -> list[PortClaim]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.

        Returns:
            What the kernel says is bound, with the process.
        """
        return [
            PortClaim(
                socket.port,
                protocol,
                PROCESS,
                ", ".join(socket.processes) or "another process",
                address=socket.address,
                processes=socket.processes,
            )
            for socket in listening_sockets(self.runner, protocol)
        ]

    # -- Questions ---------------------------------------------------------------------------

    def claims(self, protocol: str = TCP, *, listening: bool = True) -> list[PortClaim]:
        """
        List every claim on a protocol's ports.

        Args:
            protocol: ``tcp`` or ``udp``.
            listening: Include what the kernel says listens. Left out for a check that must
                not read a socket, or when the caller probes the port itself.

        Returns:
            The claims, declared ones first so that a sentence names the holder Noust knows
            before a process.
        """
        found = [
            *[claim for claim in self._system() if claim.protocol == protocol],
            *self._apps(protocol),
            *self._stacks(protocol),
            *self._streams(protocol),
            *self._databases(protocol),
            *self._instances(protocol),
        ]
        if listening:
            found.extend(self._listeners(protocol))
        return found

    def taken_ports(self, protocol: str = TCP, *, listening: bool = True) -> set[int]:
        """
        Args:
            protocol: ``tcp`` or ``udp``.
            listening: Include what the kernel says listens.

        Returns:
            Every port something holds, for a picker that skips them.
        """
        return {claim.port for claim in self.claims(protocol, listening=listening)}

    def holder(
        self,
        port: int,
        protocol: str = TCP,
        *,
        address: str | None = None,
        owner: str | None = None,
        listening: bool = True,
        ignore_processes: Sequence[str] = (),
    ) -> PortClaim | None:
        """
        Find what holds a port.

        Args:
            port: The port.
            protocol: ``tcp`` or ``udp``.
            address: The address the caller would bind; None for every address.
            owner: The caller's own name (an application's domain, a stream's name), whose
                claims do not count.
            listening: Include what the kernel says listens.
            ignore_processes: Processes whose sockets are the caller's own, such as the web
                server holding the port of an opening that was interrupted.

        Returns:
            The first claim in the way, or None.
        """
        for claim in self.claims(protocol, listening=listening):
            if claim.port != port or not _overlap(claim.address, address):
                continue
            if owner is not None and claim.owner == owner and claim.kind != PROCESS:
                continue
            if claim.kind == PROCESS and any(name in ignore_processes for name in claim.processes):
                continue
            return claim
        return None

    def require_free(
        self,
        port: int,
        protocol: str = TCP,
        *,
        address: str | None = None,
        owner: str | None = None,
        field: str = "port",
        listening: bool = True,
        ignore_processes: Sequence[str] = (),
        reveal: bool | None = None,
    ) -> int:
        """
        Refuse a port something else holds, and say what.

        Args:
            port: The port, already known to be one.
            protocol: ``tcp`` or ``udp``.
            address: The address the caller would bind; None for every address.
            owner: The caller's own name, whose claims do not count.
            field: The request field the error names.
            listening: Include what the kernel says listens.
            ignore_processes: Processes whose sockets are the caller's own.
            reveal: Name an application that holds it. None (the default) names it unless the
                request is a team's, whose members may not see every application: asking for a
                port must not be a way to list them.

        Returns:
            The port.

        Raises:
            PortConflictError: Something else holds it.
        """
        held = self.holder(
            port,
            protocol,
            address=address,
            owner=owner,
            listening=listening,
            ignore_processes=ignore_processes,
        )
        if held is None:
            return port
        label = (
            f"{protocol.upper()} {port}"
            if held.kind in (STREAM, DATABASE, PROCESS)
            else f"Port {port}"
        )
        named = not current_scope().restricted if reveal is None else reveal
        raise PortConflictError(
            f"{label} {held.describe(reveal=named)}", details=_advice(held), field=field
        )

    def validated(
        self,
        port: int | str,
        protocol: str = TCP,
        *,
        owner: str | None = None,
        field: str = "port",
    ) -> int:
        """
        Read a port an operator typed and refuse it when it is not a port or is held.

        The one door for an explicit port on a new application, a preview or an import: the
        number must be one (and not a privileged one), and nothing may hold it.

        Args:
            port: What was typed.
            protocol: ``tcp`` or ``udp``.
            owner: The caller's own name, whose claims do not count.
            field: The request field the error names.

        Returns:
            The port.

        Raises:
            PortError: It is not a port, or something holds it (a
                :class:`~noust.core.exceptions.PortConflictError`).
        """
        return self.require_free(
            validate_port(port, check_available=False), protocol, owner=owner, field=field
        )

    def require_stack_free(
        self,
        owner: str,
        mappings: list[PortMapping],
        *,
        own: set[tuple[int, str]] | None = None,
    ) -> None:
        """
        Refuse a Compose stack that would publish a port something else holds.

        Docker's own error for this says only that the address is in use; this one says who
        holds it, before anything is built or stopped.

        Args:
            owner: The stack's application (its domain), whose claims do not count.
            mappings: What its file publishes.
            own: ``(port, protocol)`` pairs its containers hold now, which the kernel reports
                as listening and are not in its way.

        Raises:
            PortConflictError: A published port is held by something else.
        """
        mine = own or set()
        for mapping in mappings:
            if mapping.host_port is None:
                continue
            listening = (mapping.host_port, mapping.protocol) not in mine
            self.require_free(
                mapping.host_port,
                mapping.protocol,
                address=mapping.host_ip,
                owner=owner,
                field="ports",
                listening=listening,
            )

    def choose(
        self,
        preferred: int | None = None,
        *,
        start: int = 3000,
        end: int = 9000,
        protocol: str = TCP,
        owner: str | None = None,
    ) -> int | None:
        """
        Pick a free port.

        Args:
            preferred: A port to take if it is free.
            start: First port of the range to search.
            end: End of the range (exclusive).
            protocol: ``tcp`` or ``udp``.
            owner: The caller's own name, whose claims do not count.

        Returns:
            The port, or None when the range is full.
        """
        taken = {
            claim.port
            for claim in self.claims(protocol)
            if owner is None or claim.owner != owner or claim.kind == PROCESS
        }
        if preferred and preferred not in taken:
            return preferred
        return next((port for port in range(start, end) if port not in taken), None)


def claimed_ports(
    store: NoustStore | None = None, runner: CommandRunner | None = None, protocol: str = TCP
) -> set[int]:
    """
    List every port something holds, for a picker that skips them.

    Args:
        store: The store; the process-wide one by default.
        runner: Runs ``ss``; the process-wide runner by default.
        protocol: ``tcp`` or ``udp``.

    Returns:
        The ports.
    """
    return PortClaims(runner, store).taken_ports(protocol)


def _advice(held: PortClaim) -> str:
    """
    Args:
        held: What holds the port.

    Returns:
        What to do about it.
    """
    if held.kind == SYSTEM:
        return "Publishing it would take that service down. Choose another port."
    if held.kind in (APP, STACK):
        return (
            "Two applications cannot listen on one port, even when one is stopped. "
            "Choose another port, or leave it out and Noust picks a free one."
        )
    if held.kind == PROCESS:
        return "Choose another port, or stop what holds it."
    return "Choose another port."
