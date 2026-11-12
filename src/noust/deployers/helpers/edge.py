# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How what Noust deploys is reached from outside: a site on a server, a port of the machine on a NAS.

On a server an application gets a site in nginx and, when asked, a certificate, and a container it runs
in is published on 127.0.0.1 so that the web server is the only way in. The central of a NAS has no web
server of Noust's to put a site on, and the NAS keeps ports 80 and 443 for its own interface (Synology,
UGREEN and QNAP all do): an application there is published on a port of the machine, the first free one
from 8081 unless one was asked for, and is reached at ``http://<the machine>:<port>``, which on an office
network is what "it opens at http://nas:8081" means. Reaching it by a name and over HTTPS is the NAS's own
proxy's job, or a tunnel's.

That is one policy, :class:`Edge`, and the two deployers (the Compose stack's and the container
application's) read it instead of each deciding for itself. Neither builds a web server manager nor a
certificate manager on a container host, and the steps that would write a site and order a certificate say
why they are skipped and where the application is, in the words :func:`skipped` gives them (which for a
stack that serves no web are the words they always had).

A port the NAS already uses is not something a container can see: the container's sockets are its own. So
it is found when Docker refuses it, and said with the port and another to try (:func:`port_taken`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.docker.reach import machine_name
from noust.core.runner import CommandRunner, get_runner
from noust.managers.supervision import keeps_alive_with_docker

if TYPE_CHECKING:
    from noust.core.store import App

__all__ = [
    "PORT",
    "SITE",
    "Edge",
    "Reached",
    "Skipped",
    "address",
    "edge",
    "port_taken",
    "propose_port",
    "reached",
    "skipped",
]

#: The first port of the machine an application is offered when none was asked for: above the ports a NAS's
#: own interface and its services take (DSM's 5000 and 5001, UGOS's 9999 and 9443, the 80 and 443 of all).
FIRST_PORT = 8081

#: What a machine's name may be made of, as a URL's host.
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")

#: How Docker says that the machine's port is taken (``Bind for 0.0.0.0:8081 failed: port is already
#: allocated``) and how the userland proxy says it (``listen tcp4 0.0.0.0:443: bind: address already in
#: use``).
_TAKEN = re.compile(
    r"Bind for \S*?:(\d+) failed: port is already allocated"
    r"|listen tcp[46]? \S*?:(\d+): bind: address already in use"
)


@dataclass(frozen=True)
class Edge:
    """
    How an application is reached on this machine.

    Attributes:
        has_site: A site and a certificate are made for it, by the web server this machine has.
        bind: The address a container application publishes on: ``127.0.0.1`` behind a site, empty for
            every address of the machine (Docker's own spelling of "all", which also means none of
            127.0.0.1's special treatment).
        first_port: Where the search for a free port of the machine starts when none was asked for, or
            None for the range a server has always used.
    """

    has_site: bool
    bind: str
    first_port: int | None


#: A server: a site behind the web server, the container on loopback.
SITE = Edge(has_site=True, bind="127.0.0.1", first_port=None)

#: A container host: no site, the container on a port of the machine.
PORT = Edge(has_site=False, bind="", first_port=FIRST_PORT)


def edge() -> Edge:
    """
    Returns:
        The policy of this machine: a server's, or a container host's (a hub that was told to deploy
        containers on its machine's Docker).

    Raises:
        ConfigError: When ``central.role`` or ``central.deploy_here`` holds what is not one of their
            values.
    """
    return PORT if keeps_alive_with_docker() else SITE


@dataclass(frozen=True)
class Skipped:
    """
    Why the site and the certificate are not made, in the words of the steps that skip them.

    Attributes:
        reason: What goes in "Skipping Nginx (...)".
        site_note: What the first skipped step says beneath it.
        ssl_note: What the second says.
    """

    reason: str
    site_note: str
    ssl_note: str


def skipped(*, headless: bool, port: int, runner: CommandRunner | None = None) -> Skipped | None:
    """
    Say whether the steps that make a site and a certificate are skipped, and why.

    Args:
        headless: The stack publishes no port: nothing is reached over HTTP.
        port: The port of the machine the application is published on.
        runner: Asks Docker the machine's name; the process-wide one by default.

    Returns:
        The words of the skipped steps; None when the site is made. A stack that serves no web skips them
        everywhere, with the words it always used; a machine that has no site to make skips them and says
        where the application is.
    """
    if headless:
        return Skipped(
            "headless worker",
            "No services expose ports, web server not needed",
            "No web-facing services, certificate not needed",
        )
    if edge().has_site:
        return None
    where = address(port, runner=runner)
    return Skipped(
        "no web server on this machine",
        f"Reached at {where}: Docker publishes it on port {port} of the machine",
        "No certificate: it is reached over HTTP on the machine's own network",
    )


def address(port: int, *, runner: CommandRunner | None = None) -> str | None:
    """
    Say where an application is reached when it has no site.

    Args:
        port: The port of the machine it is published on.
        runner: Asks Docker the machine's name; the process-wide one by default.

    Returns:
        ``http://<machine>:<port>``, with the name the machine's Docker has (the NAS's own); None on a
        machine that has a site for it, which is reached by its domain.
    """
    if edge().has_site:
        return None
    name = machine_name(runner or get_runner())
    return f"http://{name if _HOST.fullmatch(name) else '<this machine>'}:{port}"


def port_taken(said: str) -> int | None:
    """
    Read a port of the machine out of what Docker said when it could not open one.

    Args:
        said: Docker's or Compose's error output.

    Returns:
        The port, or None when it was not about a port.
    """
    found = _TAKEN.search(said)
    if found is None:
        return None
    return int(found.group(1) or found.group(2))


def propose_port(taken: int, runner: CommandRunner | None = None) -> int | None:
    """
    Pick another port of the machine to try, above the one that was taken.

    Only what Noust can see is looked at (its own applications and what its own container listens on): a
    port the NAS uses is found when Docker refuses it, which is why this is a proposal.

    Args:
        taken: The port Docker could not open.
        runner: Asks the machine; the process-wide one by default.

    Returns:
        A port that is free as far as Noust can tell, or None when there is none to propose.
    """
    from noust.core.store import get_store
    from noust.managers.port_claims import PortClaims

    return PortClaims(runner or get_runner(), get_store()).choose(
        preferred=taken + 1, start=taken + 1, end=taken + 1000
    )


@dataclass(frozen=True)
class Reached:
    """
    Where an application is reached from outside.

    Attributes:
        kind: ``site`` (by its domain, behind the web server), ``port`` (on a port of the machine) or
            ``none`` (it serves no web: a worker).
        url: The address, or None for ``none``.
        port: The port of the machine it is published on, when it has one.
    """

    kind: str
    url: str | None
    port: int | None


def reached(app: App, *, runner: CommandRunner | None = None) -> Reached:
    """
    Say where an application is reached.

    Args:
        app: The application.
        runner: Asks Docker the machine's name; the process-wide one by default.

    Returns:
        Its address by this machine's edge: the domain behind a site (over HTTPS when it has a
        certificate), ``http://<machine>:<port>`` without one, and nothing for what serves no web.
    """
    if not app.port:
        return Reached("none", None, None)
    if edge().has_site:
        return Reached("site", f"{'https' if app.ssl_enabled else 'http'}://{app.domain}", app.port)
    return Reached("port", address(app.port, runner=runner), app.port)
