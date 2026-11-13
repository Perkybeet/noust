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
from typing import TYPE_CHECKING, TypedDict

from noust.core.docker.reach import machine_name
from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.managers.supervision import keeps_alive_with_docker

if TYPE_CHECKING:
    from noust.core.store import App

__all__ = [
    "PORT",
    "PROXY_HEADERS",
    "SITE",
    "WEBSOCKET_HEADERS",
    "Edge",
    "ProxyHeader",
    "ProxyHint",
    "Reached",
    "ReadinessOptions",
    "Skipped",
    "address",
    "edge",
    "port_taken",
    "propose_port",
    "proxy_hint",
    "publish_web_port",
    "reached",
    "readiness_options",
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


#: The web port of a recipe's Compose document as it is written for a server: bound to loopback, because the
#: web server is the only way in. ``${PORT}`` or ``${PORT:-8080}`` is the variable Noust owns in the stack's
#: ``.env``; any other port the document binds to loopback (a database, an extra service) stays as it is.
_LOOPBACK_WEB_PORT = re.compile(
    r"""(?P<lead>["'\s-])127\.0\.0\.1:(?P<port>\$\{PORT(?::?-[^}]*)?\}):"""
)


def publish_web_port(document: str) -> str:
    """
    Say a recipe's Compose document the way this machine reaches it.

    A recipe's document publishes its web port on loopback, which is how a server's web server reaches it.
    Where there is no web server of Noust's (the central of a NAS) nothing in front of the application would
    ever reach loopback, and the address it is told to open, ``http://<the NAS>:<port>``, would answer only
    from the NAS itself and not from the network it is on. The document is Noust's own (rendered from the
    recipe, never an operator's file), so on such a machine its web port is published on every address of
    it, which is what the edge says (:data:`PORT`); only that port, and nothing else the document binds to
    loopback.

    Args:
        document: The recipe's Compose document.

    Returns:
        The document, with the web port's loopback address taken out on a container host; the same text on a
        server.
    """
    if edge().has_site:
        return document
    return _LOOPBACK_WEB_PORT.sub(r"\g<lead>\g<port>:", document)


class ReadinessOptions(TypedDict, total=False):
    """The keyword arguments of the readiness check that a machine that is not a server changes."""

    server_tools: bool


def readiness_options() -> ReadinessOptions:
    """
    Say what the readiness check is to be told about this machine.

    A server is asked everything a server needs (a web server, certbot, curl), as it always was, so it is
    told nothing; the central of a NAS has none of them, its image has no use for them, and it is told so.

    Returns:
        The keyword arguments of :func:`noust.core.dependencies.check_deployment_ready` that differ from
        a server's: none on a server.
    """
    return {} if edge().has_site else ReadinessOptions(server_tools=False)


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


@dataclass(frozen=True)
class ProxyHeader:
    """
    A header a reverse proxy sets on the request it passes to an application.

    Attributes:
        name: The header.
        meaning: What goes in it, in a closed list of words a person (or a console) can map onto the
            settings of any proxy: ``public_host`` (the name the visitor typed), ``client_address``,
            ``client_chain`` (the addresses of the proxies the request already went through, then the
            client's), ``public_scheme`` (``http`` or ``https``), ``public_port``, ``upgrade_request``
            (what the client asked to upgrade to) and ``upgrade`` (the word itself).
        nginx: How nginx writes it, which is what a site of Noust's does and what Nginx Proxy Manager
            and the proxy of Synology's DSM take as written.
    """

    name: str
    meaning: str
    nginx: str


#: What a site of Noust's passes to an application, in the order its template writes it
#: (``templates/nginx/proxy.conf.j2``, which a test holds this table to).
PROXY_HEADERS: tuple[ProxyHeader, ...] = (
    ProxyHeader("Host", "public_host", "$host"),
    ProxyHeader("X-Real-IP", "client_address", "$remote_addr"),
    ProxyHeader("X-Forwarded-For", "client_chain", "$proxy_add_x_forwarded_for"),
    ProxyHeader("X-Forwarded-Proto", "public_scheme", "$scheme"),
    ProxyHeader("X-Forwarded-Host", "public_host", "$host"),
    ProxyHeader("X-Forwarded-Port", "public_port", "$server_port"),
)

#: What lets a WebSocket through. A site of Noust's always does, because nothing says which application
#: uses one.
WEBSOCKET_HEADERS: tuple[ProxyHeader, ...] = (
    ProxyHeader("Upgrade", "upgrade_request", "$http_upgrade"),
    ProxyHeader("Connection", "upgrade", '"upgrade"'),
)

#: How long a site of Noust's waits for an application's answer. Many proxies wait less by default.
READ_TIMEOUT_SECONDS = 60

#: Where a published port is reached from the machine itself: loopback.
_LOCAL = "127.0.0.1"

#: What a mapping bound to "every address" or to loopback says, both reached on loopback.
_REACHED_ON_LOOPBACK = frozenset(
    {None, "", "0.0.0.0", "::", "127.0.0.1", "::1", "localhost"}  # noqa: S104 - read, never bound
)


@dataclass(frozen=True)
class ProxyHint:
    """
    What the reverse proxy of the NAS itself needs to reach an application, as data.

    Noust does not touch the NAS's proxy: it says what to put in it.

    Attributes:
        target: Where the proxy sends the request: ``http://127.0.0.1:<port>``, or the address of the
            machine the stack's file binds the port to.
        scheme: ``http``.
        host: The address part of ``target``.
        port: The port of the machine the application is published on.
        headers: What the proxy sets on the request, WebSocket included when ``websocket``.
        websocket: Whether the proxy must let a WebSocket through. Always, as a site of Noust's does.
        read_timeout_seconds: How long to wait for the application's answer.
    """

    target: str
    scheme: str
    host: str
    port: int
    headers: tuple[ProxyHeader, ...]
    websocket: bool
    read_timeout_seconds: int


def _proxy_host(app: App, runner: CommandRunner | None) -> str:
    """
    Find the address of the machine an application's port is reached at from the NAS's own proxy.

    A proxy on the NAS reaches a port Docker publishes on loopback or on every address through
    ``127.0.0.1``; a stack whose file binds it to one address of the machine is reached only there.

    Args:
        app: The application.
        runner: Reads its stack's file; the process-wide one by default.

    Returns:
        The address, bracketed when it is IPv6.
    """
    from noust.deployers.docker_compose import stack_published_ports

    try:
        mappings = stack_published_ports(app, runner=runner)
    except NoustError:
        # A file that cannot be read says nothing of a binding; the port is then the machine's.
        return _LOCAL
    for mapping in mappings:
        if mapping.protocol != "tcp" or mapping.host_port != app.port:
            continue
        if mapping.host_ip in _REACHED_ON_LOOPBACK:
            return _LOCAL
        return f"[{mapping.host_ip}]" if ":" in str(mapping.host_ip) else str(mapping.host_ip)
    return _LOCAL


def proxy_hint(app: App, *, runner: CommandRunner | None = None) -> ProxyHint | None:
    """
    Say what the NAS's own reverse proxy needs to reach an application.

    Args:
        app: The application.
        runner: Reads its stack's file; the process-wide one by default.

    Returns:
        The hint; None where a site of Noust's serves the application (nothing to paste) and for one
        that serves no web.
    """
    if reached(app, runner=runner).kind != "port" or app.port is None:
        return None
    host = _proxy_host(app, runner)
    return ProxyHint(
        target=f"http://{host}:{app.port}",
        scheme="http",
        host=host,
        port=app.port,
        headers=(*PROXY_HEADERS, *WEBSOCKET_HEADERS),
        websocket=True,
        read_timeout_seconds=READ_TIMEOUT_SECONDS,
    )
