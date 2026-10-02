# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The relay: a Compose service is recreated while a twin of it serves.

``docker compose up -d`` recreates a web service by stopping its container
and starting a new one: for as long as the new one takes to listen, nginx
has nobody on the port (in Proggest, some fifteen seconds of 502 on the
backend). Blue/green (:mod:`noust.deployers.bluegreen`) is for systemd units
on releases; a stack gets this sibling engine instead, built from the same
pieces: the atomic upstream write, ``config_errors`` before ``reload``, the
:class:`~noust.deployers.helpers.health_gate.HealthGate`, a free port and the
check that what answered on it is who should, and the drain.

nginx reaches each relayed service through a servers file of Noust's,
``/etc/nginx/noust-upstreams/<app>/<service>.servers``, which holds only
``server 127.0.0.1:<port>;`` lines and which the site includes inside an
``upstream`` block: Noust's own site as ``upstream wasm_bg_<app>_<service> {
include ...; keepalive 64; }``, an operator's site inside a block of its own
names. For each web service, in ``depends_on`` order (a backend before the
front that calls it, so a new front never talks to an old backend):

1. ``docker compose run -d --no-deps --use-aliases --name <project>-<service>-relay
   --publish 127.0.0.1:<free>:<container port> <service>``: the new image, on
   a free loopback port, answering to the service's name inside the network;
2. the health gate against that port. One that fails stops here, with the
   service's own container never touched: the new image does not start, and
   that is the proof;
3. the servers file names the relay; ``nginx -t`` and reload; the drain;
4. ``docker compose up -d --no-deps --force-recreate <service>`` and the gate
   against the service's own port;
5. the servers file names the service again; reload; the drain; the relay is
   stopped (``docker stop -t 20``) and removed.

When the recreated container does not answer, the relay stays serving and the
update fails saying so. A relay left by an attempt that did not finish (the
process killed between steps 3 and 5) is resolved before anything else: when
the servers file still names it and the service's own container answers, the
traffic goes back and the relay is removed; when the service does not answer,
nothing is touched and the error says why.

Services that are not relayed (databases, queues, workers, a web service the
site does not reach through a servers file) are recreated by the deployer with
``up -d`` afterwards, as before. That both versions of a relayed service run
side by side for a few seconds is the project's to tolerate (a queue consumer
in the web process, a scheduled job): the mode is opt-in for that reason.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import DeploymentError, DockerError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import DEFAULT_DRAIN_SECONDS, App, AppType, NoustStore
from noust.core.utils import domain_to_app_name
from noust.deployers.bluegreen import Listener, drain_of, listeners_on
from noust.deployers.helpers.compose_ports import parse_ports, web_root_service
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate, Probe
from noust.deployers.helpers.site import is_operator_site
from noust.managers.service_manager import ServiceManager
from noust.managers.webserver import (
    UPSTREAM_PREFIX,
    read_servers,
    remove_servers,
    servers_file,
    site_includes_servers,
    write_servers,
)
from noust.validators.port import find_available_port

if TYPE_CHECKING:
    from noust.core.runner import CommandRunner
    from noust.deployers.docker_compose import DockerComposeDeployer, DockerComposeService
    from noust.managers.webserver import WebServerManager

__all__ = [
    "ComposeRelay",
    "LeftoverRelay",
    "RelayTarget",
    "advanced_relay_context",
    "check_app_relay_eligible",
    "check_relay_eligible",
    "disable_relay",
    "enable_relay",
    "include_line",
    "leftover_relays",
    "proxy_relay_context",
    "relay_container_name",
    "relay_enabled",
    "relay_order",
    "relay_status",
    "relay_targets",
    "relay_upstream_name",
    "relayed_services",
]

_logger = logging.getLogger(__name__)

#: What a relay container's name ends with, after ``<project>-<service>``.
RELAY_SUFFIX = "-relay"

#: Where a relay's loopback port is looked for: away from where applications
#: are given theirs (3000 up) and below the kernel's ephemeral range, so a
#: port found free is not one an outgoing connection takes a moment later.
RELAY_PORT_RANGE = (20000, 30000)

#: Seconds ``docker stop`` gives a relay to finish before it is killed.
RELAY_STOP_SECONDS = 20

#: Lines of a relay's own output attached to a failed gate.
RELAY_LOG_LINES = 40

#: Bringing a container up can pull an image named rather than built.
RELAY_COMMAND_TIMEOUT = 1800

#: ``docker stop`` waits RELAY_STOP_SECONDS, then needs a moment to kill.
_STOP_TIMEOUT = RELAY_STOP_SECONDS + 40

#: Asking Docker about one container.
_INSPECT_TIMEOUT = 60

#: How the service's own container is asked whether it still answers, before a
#: leftover relay is retired: a few quick probes, not a whole gate.
_STILL_ANSWERS_ATTEMPTS = 3
_STILL_ANSWERS_DELAY = 1.0

#: Processes that hold a port Docker publishes. With the userland proxy off
#: nothing listens at all, which :func:`listeners_on` reports as no listener.
_PUBLISHERS = frozenset({"docker-proxy", "rootlesskit", "rootlessport"})

#: Characters an nginx upstream name is kept to.
_UPSTREAM_INVALID = re.compile(r"[^A-Za-z0-9_]")


# ---------------------------------------------------------------------------
# Names and the services relayed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayTarget:
    """
    One web service a relay can stand in for.

    Attributes:
        service: The Compose service.
        host_port: The host port its own container publishes, which the
            servers file names when nothing is being recreated.
        container_port: The port inside the container that host port maps to,
            which the relay publishes on a free host port.
    """

    service: str
    host_port: int
    container_port: int


def relay_targets(services: Sequence[DockerComposeService]) -> dict[str, RelayTarget]:
    """
    Find the services that publish a TCP port at a known host port.

    Args:
        services: The stack's services, in file order.

    Returns:
        One target per such service, by name, with its first such mapping:
        the one :func:`~noust.deployers.helpers.compose_ports.published_port`
        names and the site proxies to.
    """
    targets: dict[str, RelayTarget] = {}
    for service in services:
        for mapping in parse_ports(list(service.ports)):
            if mapping.protocol == "tcp" and mapping.host_port is not None:
                targets[service.name] = RelayTarget(
                    service.name, mapping.host_port, mapping.container_port
                )
                break
    return targets


def relay_order(services: Sequence[DockerComposeService], names: Iterable[str]) -> list[str]:
    """
    Order the services to relay so that each comes after what it depends on.

    Only ``depends_on`` among the named services counts (a backend's database
    is not relayed). Services with no order between them keep file order; a
    cycle, which Compose itself refuses, falls back to file order for the
    services in it.

    Args:
        services: The stack's services, in file order.
        names: The services to order.

    Returns:
        The same names, dependencies first.
    """
    wanted = set(names)
    in_file = [service for service in services if service.name in wanted]
    depends = {
        service.name: [dep for dep in service.depends_on if dep in wanted and dep != service.name]
        for service in in_file
    }
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = [service.name for service in in_file]
    while remaining:
        ready = [name for name in remaining if all(dep in placed for dep in depends[name])]
        # A cycle: take the first left in file order, so the loop ends.
        chosen = ready[0] if ready else remaining[0]
        ordered.append(chosen)
        placed.add(chosen)
        remaining.remove(chosen)
    # Names the file does not have keep their place at the end: the caller
    # reports them, nothing here invents an order for them.
    ordered.extend(name for name in names if name not in placed and name not in ordered)
    return ordered


def relay_container_name(project: str, service: str) -> str:
    """
    Name the relay of one service.

    Args:
        project: The Compose project.
        service: The service.

    Returns:
        ``<project>-<service>-relay``: one per service, so a leftover is found
        by name.
    """
    return f"{project}-{service}{RELAY_SUFFIX}"


def relay_upstream_name(app_name: str, service: str) -> str:
    """
    Name the upstream Noust's own site gives one service.

    Args:
        app_name: The application's name.
        service: The service.

    Returns:
        ``wasm_bg_<app>_<service>``, with every character nginx would not take
        in a name as an underscore.
    """
    return UPSTREAM_PREFIX + _UPSTREAM_INVALID.sub("_", f"{app_name}_{service}")


def include_line(app_name: str, service: str) -> str:
    """
    Say the line a site needs to reach one service through the relay.

    Args:
        app_name: The application's name.
        service: The service.

    Returns:
        ``include <servers file>;``, exactly as it goes inside the upstream block.
    """
    return f"include {servers_file(app_name, service)};"


def _app_of(deployer: DockerComposeDeployer) -> App | None:
    """
    Read the store row of the stack a deployer addresses.

    Args:
        deployer: The deployer.

    Returns:
        The row, or None for a stack not registered yet.
    """
    return deployer.store.get_app(deployer.domain) if deployer.domain else None


def relay_enabled(deployer: DockerComposeDeployer) -> bool:
    """
    Tell whether the stack a deployer addresses is updated through relays.

    Args:
        deployer: The deployer.

    Returns:
        True when its row is a Compose stack in zero-downtime mode.
    """
    app = _app_of(deployer)
    return bool(
        app is not None and app.zero_downtime and app.app_type == AppType.DOCKER_COMPOSE.value
    )


def _servers_in_place(app_name: str, service: str) -> bool:
    """
    Tell whether a service's servers file exists as a plain file.

    A site including a file that is not there is one nginx refuses, so a site
    is only rendered with the include once the file is in place.

    Args:
        app_name: The application's name.
        service: The service.

    Returns:
        True when the file is there and is not a link.
    """
    path = servers_file(app_name, service)
    return path.is_file() and not path.is_symlink() and not path.parent.is_symlink()


def _site_text(web: WebServerManager, domain: str) -> str:
    """
    Read a stack's site as it is on disk.

    Args:
        web: The web server manager.
        domain: The stack's domain.

    Returns:
        Its text, or empty when it has none.
    """
    return (web.get_site_config(domain) or "") if web.site_exists(domain) else ""


def relayed_services(
    deployer: DockerComposeDeployer, *, web: WebServerManager | None = None
) -> list[str]:
    """
    Name the services an update of this stack relays.

    Those whose servers file is in place and that the site includes: what
    the site does not reach through the file would go on being served by the
    old container however the file changes.

    Args:
        deployer: The deployer, its services parsed.
        web: The web server manager. Defaults to the deployer's.

    Returns:
        The services, in file order; empty when the mode is off.
    """
    if not relay_enabled(deployer):
        return []
    manager = web if web is not None else deployer.webserver_manager()
    site = _site_text(manager, deployer.domain)
    names = [
        name
        for name in relay_targets(deployer.services)
        if _servers_in_place(deployer.app_name, name)
        and site_includes_servers(site, servers_file(deployer.app_name, name))
    ]
    if not names:
        deployer.logger.warning(
            f"{deployer.domain} is in zero-downtime mode, but its site includes no servers file "
            "of Noust's: the containers are recreated with a cut. See what is missing with: "
            f"noust app zero-downtime {deployer.domain}"
        )
    return names


# ---------------------------------------------------------------------------
# The site Noust renders
# ---------------------------------------------------------------------------


def _upstream_entry(deployer: DockerComposeDeployer, target: RelayTarget) -> dict[str, str]:
    """
    Describe the upstream block Noust's site gives a relayed service.

    Args:
        deployer: The deployer.
        target: The service.

    Returns:
        Its name, its servers file and the service, for the templates.
    """
    return {
        "name": relay_upstream_name(deployer.app_name, target.service),
        "file": str(servers_file(deployer.app_name, target.service)),
        "service": target.service,
    }


def _relayed_by_port(deployer: DockerComposeDeployer) -> dict[int, RelayTarget]:
    """
    Map the host ports of the services Noust's site can relay.

    Args:
        deployer: The deployer, its services parsed.

    Returns:
        Each service whose servers file is in place, by its host port; empty
        when the mode is off.
    """
    if not relay_enabled(deployer):
        return {}
    return {
        target.host_port: target
        for target in relay_targets(deployer.services).values()
        if _servers_in_place(deployer.app_name, target.service)
    }


def proxy_relay_context(deployer: DockerComposeDeployer, port: int) -> dict[str, Any]:
    """
    Add the relay to the context of the plain proxy site, when it applies.

    Args:
        deployer: The deployer, its services parsed.
        port: The port the site proxies ``/`` to.

    Returns:
        ``upstream_name`` and ``relay_upstreams`` when the service on that
        port is relayed and its servers file is in place; empty otherwise, and
        the site proxies straight to the port as it always did.
    """
    target = _relayed_by_port(deployer).get(port)
    if target is None:
        return {}
    entry = _upstream_entry(deployer, target)
    return {"upstream_name": entry["name"], "relay_upstreams": [entry]}


def advanced_relay_context(
    deployer: DockerComposeDeployer, context: dict[str, Any]
) -> dict[str, Any]:
    """
    Make the routes of a ``noust.nginx.yaml`` site reach relayed services through their files.

    The upstreams keep the names the file gave them; the ones whose port is a
    relayed service's include its servers file instead of naming the port.

    Args:
        deployer: The deployer, its services parsed.
        context: The advanced template's context.

    Returns:
        The same context, its upstreams annotated with ``servers_file``.
    """
    relayed = _relayed_by_port(deployer)
    if not relayed:
        return context
    for upstream in (context.get("upstreams") or {}).values():
        target = relayed.get(int(upstream.get("port") or 0))
        if target is not None:
            upstream["servers_file"] = str(servers_file(deployer.app_name, target.service))
    return context


def _routed_ports(deployer: DockerComposeDeployer) -> set[int]:
    """
    Name the ports Noust's own site for this stack proxies to.

    Args:
        deployer: The deployer, its services parsed.

    Returns:
        The port of ``/`` for the plain proxy site, every route's port for a
        ``noust.nginx.yaml`` one.
    """
    template, context = deployer._site_template(with_ssl=False)
    if template == "advanced":
        return {int(up["port"]) for up in (context.get("upstreams") or {}).values() if up["port"]}
    return {int(context["port"])}


# ---------------------------------------------------------------------------
# Who may use it
# ---------------------------------------------------------------------------


def _load_services(deployer: DockerComposeDeployer) -> None:
    """
    Read the stack's compose file into the deployer, when nothing has yet.

    Args:
        deployer: The deployer.

    Raises:
        DeploymentError: No compose file is found.
    """
    if deployer.services:
        return
    # Imported here: the deployer imports this module's callers.
    from noust.deployers.docker_compose import parse_services

    if deployer.compose_path is None:
        deployer._discover_compose_file()
    deployer.services = parse_services(deployer._load_compose_document())


def _stack_problems(deployer: DockerComposeDeployer) -> list[str]:
    """
    Say what in the compose file keeps the relay from working.

    Args:
        deployer: The deployer, its services parsed.

    Returns:
        One actionable sentence per problem; empty when there is none.
    """
    domain = deployer.domain
    if not deployer.services or deployer._is_headless():
        return [
            f"{domain} publishes no port: there is no web service to relay. A stack of "
            "workers is judged by its containers and recreated with up -d."
        ]
    problems: list[str] = []
    targets = relay_targets(deployer.services)
    for service in deployer.services:
        if service.name in targets or not service.ports:
            continue
        tcp = [m for m in parse_ports(list(service.ports)) if m.protocol == "tcp"]
        if tcp:
            container = tcp[0].container_port
            problems.append(
                f"{service.name} publishes container port {container} at a host port Docker "
                f"picks: the site cannot reach it at a known port. Publish it at a fixed one in "
                f'the compose file, such as "127.0.0.1:<port>:{container}".'
            )
    if not targets and not problems:
        problems.append(
            f"No service of {domain} publishes a TCP port at a fixed host port. Publish the "
            'one the site proxies to as "127.0.0.1:<port>:<container port>".'
        )
    return problems


def _referenced(site: str, port: int) -> bool:
    """
    Tell whether a site's text names a loopback port.

    Args:
        site: The site's configuration.
        port: The port.

    Returns:
        True when ``127.0.0.1:<port>``, ``localhost:<port>`` or ``[::1]:<port>``
        appears in it.
    """
    return re.search(rf"(?:127\.0\.0\.1|localhost|\[::1\]):{port}(?!\d)", site) is not None


def _site_problems(deployer: DockerComposeDeployer, web: WebServerManager) -> list[str]:
    """
    Say what in the stack's site keeps the relay from reaching its services.

    Args:
        deployer: The deployer, its services parsed.
        web: The web server manager.

    Returns:
        One actionable sentence per problem; empty when there is none.
    """
    domain = deployer.domain
    targets = relay_targets(deployer.services)
    if not targets:
        return []
    if not web.site_exists(domain):
        return [
            f"{domain} has no site for nginx to proxy through. Redeploy it so Noust writes "
            f"one: noust update {domain}"
        ]
    if not is_operator_site(web, domain):
        ports = _routed_ports(deployer)
        if any(target.host_port in ports for target in targets.values()):
            return []
        listed = ", ".join(f"{t.service} ({t.host_port})" for t in targets.values())
        return [
            f"The site of {domain} proxies to port {', '.join(map(str, sorted(ports)))}, which "
            f"no service publishes ({listed}). Redeploy with the port of the service in front: "
            f"noust update {domain}"
        ]

    site = _site_text(web, domain)
    path = web.config_path(domain)
    problems: list[str] = []
    included = []
    for target in targets.values():
        file = servers_file(deployer.app_name, target.service)
        if site_includes_servers(site, file):
            included.append(target)
        elif _referenced(site, target.host_port):
            problems.append(_missing_include(deployer, target, path))
    if not included and not problems:
        root = web_root_service(deployer.services) or next(iter(targets))
        problems.append(_missing_include(deployer, targets[root], path))
    return problems


def _missing_include(deployer: DockerComposeDeployer, target: RelayTarget, site: Path) -> str:
    """
    Say which line an operator's site needs for one service.

    Args:
        deployer: The deployer.
        target: The service.
        site: The site's file.

    Returns:
        What is missing and the exact line, with where it goes.
    """
    return (
        f"{site} is your own site, and it does not include the servers file of "
        f"{target.service}: nginx would keep sending its requests to the container being "
        f"recreated. Inside the upstream block that proxies to 127.0.0.1:{target.host_port}, "
        f"replace its server line with this one, then reload nginx: "
        f"{include_line(deployer.app_name, target.service)} "
        f"(the file names 127.0.0.1:{target.host_port} whenever nothing is being recreated; "
        f"turning the mode on writes it, so the line loads)"
    )


def check_relay_eligible(deployer: DockerComposeDeployer, *, site: bool = True) -> list[str]:
    """
    Say what keeps a stack from being updated without a cut. Changes nothing.

    Args:
        deployer: The deployer of the stack, from
            :func:`~noust.deployers.docker_compose.stack_deployer`.
        site: False leaves the site out: turning the relay on writes the
            servers files an operator's site must include before checking it.

    Returns:
        One actionable sentence per problem: a web service without a fixed
        host port, a site that does not reach the stack, an operator's site
        without the ``include`` (with the exact line). Empty when the relay
        can be turned on.
    """
    try:
        _load_services(deployer)
    except DeploymentError as exc:
        return [
            f"{deployer.domain} has no compose file Noust can read: {exc.message}. "
            f"{exc.details or ''}".strip()
        ]
    problems = _stack_problems(deployer)
    if problems or not site:
        return problems
    return _site_problems(deployer, deployer.webserver_manager())


def _stack_deployer(app: App) -> DockerComposeDeployer:
    """
    Build the deployer that addresses a deployed stack.

    Args:
        app: The application's row.

    Returns:
        The deployer, from the one place that builds it.
    """
    # Imported here: the deployer imports this module's callers.
    from noust.deployers.docker_compose import stack_deployer

    return stack_deployer(app)


def check_app_relay_eligible(app: App, *, site: bool = True) -> list[str]:
    """
    Say what keeps a deployed stack from being updated without a cut. Changes nothing.

    Args:
        app: The application's row.
        site: False leaves the site out (see :func:`check_relay_eligible`).

    Returns:
        The problems, as :func:`check_relay_eligible` says them.
    """
    return check_relay_eligible(_stack_deployer(app), site=site)


def _refusal(domain: str, problems: Sequence[str]) -> ValidationError:
    """
    Build the error that refuses the relay.

    Args:
        domain: The stack's domain.
        problems: What keeps it from working.

    Returns:
        The error: the first problem as the message, every one in the details.
    """
    return ValidationError(
        f"{domain} cannot be updated without a cut yet: {problems[0]}",
        details="\n".join(f"- {problem}" for problem in problems),
        field="enabled",
    )


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class ComposeRelay:
    """
    Recreate a stack's web services one at a time, each behind a relay.

    Expects the caller to hold the application's lock, as every update does.
    """

    def __init__(
        self,
        deployer: DockerComposeDeployer,
        *,
        sleep: Callable[[float], None] | None = None,
        probe: Probe | None = None,
        web: WebServerManager | None = None,
        listeners: Callable[[int], list[Listener] | None] | None = None,
        port_finder: Callable[..., int | None] | None = None,
    ) -> None:
        """
        Initialize the engine.

        Args:
            deployer: The deployer of the stack, its compose file found and
                its services parsed.
            sleep: How the drain waits. Defaults to :func:`time.sleep`.
            probe: The HTTP probe the gates run. Defaults to
                :func:`~noust.deployers.helpers.health.wait_until_healthy`.
            web: The web server manager. Defaults to the deployer's.
            listeners: Names who listens on a port. Defaults to
                :func:`~noust.deployers.bluegreen.listeners_on` through the
                deployer's runner.
            port_finder: Finds a free port, as
                :func:`~noust.validators.port.find_available_port` does.
        """
        self.deployer = deployer
        self.log = deployer.logger
        self._sleep = sleep if sleep is not None else time.sleep
        self._probe = probe if probe is not None else wait_until_healthy
        self._web = web
        self._listeners = (
            listeners
            if listeners is not None
            else (lambda port: listeners_on(port, runner=deployer.runner))
        )
        self._find_port = port_finder if port_finder is not None else find_available_port
        self._app: App | None = None

    # -- What it works on -------------------------------------------------

    @property
    def web(self) -> WebServerManager:
        """The web server manager that tests and reloads nginx."""
        if self._web is None:
            self._web = self.deployer.webserver_manager()
        return self._web

    @property
    def app(self) -> App | None:
        """The stack's store row, read once."""
        if self._app is None:
            self._app = _app_of(self.deployer)
        return self._app

    def project(self) -> str:
        """
        Name the Compose project the stack runs as.

        Returns:
            The project the store pins, or the one Compose derives.
        """
        from noust.deployers.docker_compose import compose_project_name

        pinned = self.deployer._pinned_project()
        derived = compose_project_name(self.deployer.app_path, self.deployer.compose_path)
        return pinned or derived or self.deployer.app_name

    def container(self, service: str) -> str:
        """
        Name one service's relay.

        Args:
            service: The service.

        Returns:
            Its container name.
        """
        return relay_container_name(self.project(), service)

    def _check(self, target: RelayTarget) -> HealthCheck:
        """
        Build the question the gate asks one service.

        The application's own check is about the service the site serves
        ``/`` from (its port is the application's): its path means nothing to
        a backend behind it, which is asked the default question within the
        application's time.

        Args:
            target: The service.

        Returns:
            The check.
        """
        app = self.app
        if app is not None and app.port == target.host_port:
            return HealthCheck.for_app(app)
        return HealthCheck(timeout=app.health_timeout if app is not None else None)

    # -- The relay ----------------------------------------------------------

    def recreate(self, web_services: list[str]) -> tuple[bool, str]:
        """
        Recreate each web service behind a relay, dependencies first.

        Args:
            web_services: The services to relay: the ones the site reaches
                through a servers file (:func:`relayed_services`).

        Returns:
            Whether every one was recreated and serves from its own container
            again, and when not, why, verbatim: which service, whether its
            own container was touched and, when a relay is left serving, which
            one and on which port.
        """
        targets = relay_targets(self.deployer.services)
        unknown = [name for name in web_services if name not in targets]
        if unknown:
            return False, (
                f"{', '.join(unknown)} publish no TCP port at a fixed host port, so no relay can "
                "stand in for them. Publish one in the compose file, or turn the mode off: "
                f"noust app zero-downtime {self.deployer.domain} off"
            )
        order = relay_order(self.deployer.services, web_services)
        if self.deployer._rehearsing():
            for name in order:
                self.log.substep(
                    f"Would start {self.container(name)} with the new image, move {name}'s "
                    "traffic to it, recreate the service and move the traffic back"
                )
            return True, ""

        problem = self.resolve_leftovers(order)
        if problem is not None:
            return False, problem
        for name in order:
            healthy, evidence = self._relay(targets[name])
            if not healthy:
                return False, evidence
        return True, ""

    def _relay(self, target: RelayTarget) -> tuple[bool, str]:
        """
        Recreate one service while its relay serves.

        Args:
            target: The service.

        Returns:
            Whether its own container serves the new image now, and the
            evidence when it does not.
        """
        service = target.service
        name = self.container(service)
        spare = self._spare_port()
        if spare is None:
            self.log.warning(
                f"No free port in {RELAY_PORT_RANGE[0]}-{RELAY_PORT_RANGE[1] - 1} for a relay of "
                f"{service}; it is recreated with a cut"
            )
            return self._recreate_own(target, relay=None)

        self.log.substep(f"Starting {name} with the new image of {service} on 127.0.0.1:{spare}")
        check = self._check(target)
        gate = HealthGate(
            unit=None,
            url=check.url(spare),
            check=check,
            services=ServiceManager(runner=self.deployer.runner),
            logger=self.log,
            probe=self._probe,
            restart=lambda: self._start(name, spare, target),
        )
        healthy, evidence = gate.restart_and_probe()
        if healthy:
            stranger = self._stranger(name, spare)
            if stranger is not None:
                healthy, evidence = False, stranger
        if not healthy:
            output = self._output(name)
            self._remove(name)
            return False, _paragraphs(
                f"The new image of {service} did not answer in its relay ({name}, "
                f"127.0.0.1:{spare}); {service} kept serving from its own container, which was "
                "not touched",
                evidence,
                f"Last lines of {name} (docker logs --tail {RELAY_LOG_LINES}):\n{output}",
            )

        problem = self._point(service, [spare])
        if problem is not None:
            self._remove(name)
            self.log.warning(
                f"nginx would not send {service}'s requests to its relay, so {service} is "
                f"recreated with a cut. nginx said:\n{problem}"
            )
            return self._recreate_own(target, relay=None)

        drain = self._drain()
        self.log.substep(
            f"nginx sends {service}'s requests to {name}; its own container finishes what it "
            f"has for {drain}s"
        )
        self._wait(drain)
        healthy, evidence = self._recreate_own(target, relay=(name, spare))
        if not healthy:
            return False, evidence

        problem = self._point(service, [target.host_port])
        if problem is not None:
            return False, _paragraphs(
                f"{service}'s own container answers on 127.0.0.1:{target.host_port}, but nginx "
                f"would not move its requests back; {name} keeps serving them on "
                f"127.0.0.1:{spare}. Fix what nginx says and update again: the update gives "
                f"{service} back to its own container first",
                problem,
            )
        self.log.substep(f"{service} serves from its own container; {name} finishes for {drain}s")
        self._wait(drain)
        self._remove(name)
        return True, ""

    def _recreate_own(
        self, target: RelayTarget, *, relay: tuple[str, int] | None
    ) -> tuple[bool, str]:
        """
        Recreate a service's own container and ask it, on its own port, if it is up.

        Args:
            target: The service.
            relay: The relay serving meanwhile, by name and port, or None when
                the service is recreated with a cut.

        Returns:
            Whether it answers, and the evidence when it does not.
        """
        service = target.service
        check = self._check(target)
        self.log.substep(f"Recreating {service}")
        gate = HealthGate(
            unit=None,
            url=check.url(target.host_port),
            check=check,
            services=ServiceManager(runner=self.deployer.runner),
            logger=self.log,
            probe=self._probe,
            restart=lambda: self._up(service),
        )
        healthy, evidence = gate.restart_and_probe()
        if healthy:
            return True, ""
        if relay is None:
            return False, _paragraphs(
                f"{service} was recreated and does not answer on 127.0.0.1:{target.host_port}",
                evidence,
            )
        name, spare = relay
        return False, _paragraphs(
            f"{service}'s recreated container does not answer on 127.0.0.1:{target.host_port}; "
            f"{name}, with the new image, keeps serving it on 127.0.0.1:{spare}. See why with: "
            f"docker compose logs {service}. The next update gives {service} back to its own "
            "container once it answers",
            evidence,
        )

    # -- A relay left behind ------------------------------------------------

    def resolve_leftovers(self, services: Sequence[str]) -> str | None:
        """
        Deal with relays an earlier update left, before anything is recreated.

        A servers file that names a relay (see :func:`_names_a_relay`) instead
        of the service's own port is a relay that still serves: when the
        service's own container answers, the traffic goes back to it and the
        relay is removed; when it does not, nothing is touched. A file on the
        service's previous own port is no relay and is left for the update to
        move. A relay container nobody is sent to is removed.

        Args:
            services: The services to look at.

        Returns:
            None when nothing is left, or why something has to stay, with what
            to do about it.
        """
        targets = relay_targets(self.deployer.services)
        app_name = self.deployer.app_name
        for service in services:
            target = targets.get(service)
            if target is None:
                continue
            name = self.container(service)
            exists = self._exists(name)
            pointed = read_servers(app_name, service)
            if pointed and pointed != [target.host_port] and _names_a_relay(pointed, exists):
                where = ", ".join(f"127.0.0.1:{port}" for port in pointed)
                left = f"{name}, left by an update that did not finish" if exists else "a relay"
                if not self._answers(target):
                    return (
                        f"nginx sends {service}'s requests to {where} ({left}), and {service}'s "
                        f"own container does not answer on 127.0.0.1:{target.host_port}; nothing "
                        f"was changed. Bring it up (docker compose up -d {service}) or see why "
                        f"(docker compose logs {service}), then update again"
                    )
                problem = self._point(service, [target.host_port])
                if problem is not None:
                    return _paragraphs(
                        f"nginx sends {service}'s requests to {where} ({left}) and would not "
                        f"move them back to 127.0.0.1:{target.host_port}; nothing else was "
                        "changed",
                        problem,
                    )
                self.log.substep(
                    f"{service} serves from its own container again, instead of {where}"
                )
                if exists:
                    self._wait(self._drain())
            if exists:
                self.log.substep(f"Removing {name}, left by an earlier update")
                self._remove(name)
        return None

    def leftovers(self, services: Sequence[str]) -> list[str]:
        """
        Say what :meth:`resolve_leftovers` would act on. Changes nothing.

        Args:
            services: The services to look at.

        Returns:
            One sentence per relay an earlier update left: a servers file
            naming something other than the service's own port, or a relay
            container nobody is sent to.
        """
        targets = relay_targets(self.deployer.services)
        found: list[str] = []
        for service in services:
            target = targets.get(service)
            if target is None:
                continue
            name = self.container(service)
            exists = self._exists(name)
            pointed = read_servers(self.deployer.app_name, service)
            if pointed and pointed != [target.host_port] and _names_a_relay(pointed, exists):
                where = ", ".join(f"127.0.0.1:{port}" for port in pointed)
                left = f"{name}, left by an update that did not finish" if exists else "a relay"
                found.append(
                    f"nginx sends {service}'s requests to {where} ({left}), not to its own "
                    f"container on 127.0.0.1:{target.host_port}"
                )
            elif exists:
                found.append(
                    f"{name}, left by an update that did not finish, still exists; nothing is "
                    "sent to it"
                )
        return found

    # -- Steps --------------------------------------------------------------

    def _spare_port(self) -> int | None:
        """
        Find a free loopback port for a relay.

        Returns:
            A port nothing listens on, that no application owns and that the
            stack does not publish; None when there is none in range.
        """
        owned = set(self.deployer.store.ports_owned_by_apps())
        owned.update(target.host_port for target in relay_targets(self.deployer.services).values())
        start, end = RELAY_PORT_RANGE
        return self._find_port(start=start, end=end, exclude=owned)

    def _start(self, name: str, port: int, target: RelayTarget) -> None:
        """
        Start a relay of a service from the image just built.

        Args:
            name: The relay's container name.
            port: The free loopback port it publishes.
            target: The service.

        Raises:
            DockerError: Compose did not start it, with its own output.
        """
        command = self.deployer._compose(
            "run",
            "-d",
            "--no-deps",
            "--use-aliases",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:{target.container_port}",
            target.service,
        )
        result = self.deployer._run(command, timeout=RELAY_COMMAND_TIMEOUT)
        if not result.success:
            raise DockerError(
                f"Could not start {name}",
                details=(result.stderr.strip() or result.stdout.strip()),
            )

    def _up(self, service: str) -> None:
        """
        Recreate a service's own container, and only it.

        Args:
            service: The service.

        Raises:
            DockerError: Compose failed, with its own output.
        """
        command = self.deployer._compose("up", "-d", "--no-deps", "--force-recreate", service)
        result = self.deployer._run(command, timeout=RELAY_COMMAND_TIMEOUT)
        if not result.success:
            raise DockerError(
                f"Failed to recreate {service}",
                details=(result.stderr.strip() or result.stdout.strip()),
            )

    def _point(self, service: str, ports: list[int]) -> str | None:
        """
        Point a service's servers file at ports, test nginx and reload.

        Args:
            service: The service.
            ports: The ports to name.

        Returns:
            None when nginx serves from them now; otherwise what nginx said,
            with the previous file back and loaded.
        """
        app_name = self.deployer.app_name
        previous = read_servers(app_name, service)
        try:
            write_servers(app_name, service, ports)
        except NoustError as exc:
            return str(exc)
        problem = self.web.config_errors()
        if problem is None and self.web.reload():
            return None
        if previous:
            try:
                write_servers(app_name, service, previous)
            except NoustError as exc:
                self.log.warning(f"Could not put the servers of {service} back: {exc}")
        if problem is None:
            # Valid and still not reloaded: load what is on disk again so
            # what nginx holds matches it.
            self.web.reload()
            return "The configuration is valid but nginx did not reload; see why with: " + (
                "systemctl status nginx"
            )
        return problem

    def _stranger(self, name: str, port: int) -> str | None:
        """
        Tell whether what answered on a relay's port was not the relay.

        Args:
            name: The relay.
            port: Its port.

        Returns:
            Why it was not, or None when nothing says so. Where nothing can be
            told (no ``ss``, the userland proxy off), the relay having started
            on that port is what is left to go on.
        """
        listeners = self._listeners(port)
        if not listeners:
            return None
        strangers = [item for item in listeners if item.process not in _PUBLISHERS]
        if not strangers:
            return None
        who = ", ".join(item.describe() for item in strangers)
        return (
            f"Port {port} is held by {who}, which is not Docker publishing {name}: what answered "
            f"the health check was not the new image. See it with: ss -ltnp 'sport = :{port}'"
        )

    def _answers(self, target: RelayTarget) -> bool:
        """
        Ask a service's own container whether it answers, a few times.

        Args:
            target: The service.

        Returns:
            True when it does.
        """
        check = self._check(target)
        return self._probe(
            check.url(target.host_port),
            retries=_STILL_ANSWERS_ATTEMPTS,
            delay=_STILL_ANSWERS_DELAY,
            on_attempt=self.log.debug,
            accept=check.accepts,
        )

    def _exists(self, name: str) -> bool:
        """
        Tell whether a relay container exists, running or not.

        Args:
            name: The container.

        Returns:
            True when Docker knows it.
        """
        result = self.deployer.runner.run(
            ["docker", "container", "inspect", "--format", "{{.State.Status}}", name],
            timeout=_INSPECT_TIMEOUT,
        )
        return result.success

    def _output(self, name: str) -> str:
        """
        Read the last lines a relay printed.

        Args:
            name: The container.

        Returns:
            Docker's output verbatim, or why it could not be read.
        """
        result = self.deployer.runner.run(
            ["docker", "logs", "--tail", str(RELAY_LOG_LINES), name], timeout=_INSPECT_TIMEOUT
        )
        text = "\n".join(part for part in (result.stdout, result.stderr) if part.strip())
        if result.success:
            return text.strip() or "(it printed nothing)"
        return f"(its output could not be read: {text.strip()})"

    def _remove(self, name: str) -> None:
        """
        Stop and remove a relay, reporting rather than raising a failure.

        Args:
            name: The container.
        """
        runner = self.deployer.runner
        stopped = runner.run(
            ["docker", "stop", "-t", str(RELAY_STOP_SECONDS), name], timeout=_STOP_TIMEOUT
        )
        if not stopped.success:
            self.log.warning(f"docker stop {name}: {stopped.stderr.strip()}")
        removed = runner.run(["docker", "rm", "-f", name], timeout=_INSPECT_TIMEOUT)
        if not removed.success:
            self.log.warning(
                f"{name} was not removed: {removed.stderr.strip()}. Remove it with: "
                f"docker rm -f {name}"
            )

    def _drain(self) -> int:
        """Seconds the container that stops serving keeps finishing its requests."""
        return drain_of(self.app) if self.app is not None else DEFAULT_DRAIN_SECONDS

    def _wait(self, seconds: int) -> None:
        """
        Let in-flight requests finish.

        Args:
            seconds: How long.
        """
        if seconds:
            self._sleep(seconds)


# ---------------------------------------------------------------------------
# Turning the mode on and off
# ---------------------------------------------------------------------------


def _refresh_site(app: App) -> None:
    """
    Render a stack's site again from the store, and reload nginx.

    Args:
        app: The application.
    """
    # Imported here: the domains module builds deployers, which build this.
    from noust.deployers.domains import refresh_site

    refresh_site(app)


def enable_relay(
    app: App,
    *,
    drain_seconds: int | None,
    logger: Logger,
    deployer: DockerComposeDeployer | None = None,
    refresh: Callable[[App], None] | None = None,
) -> None:
    """
    Turn the relay on for a stack: its servers files, the mode, and Noust's site.

    The servers files are written first, naming each service's own port, so
    that an operator's site can include them (and ``nginx -t`` passes once it
    does). Noust's own site is then rendered with the upstream blocks that
    include them; an operator's site is checked, and refused with the exact
    line it lacks. Nothing is recreated: the next update is the first relayed.

    Args:
        app: The application's row; updated in place.
        drain_seconds: Seconds a container keeps finishing its requests after
            traffic moved away from it; None for the default.
        logger: Where the steps are reported.
        deployer: The stack's deployer. Defaults to the one built from the row.
        refresh: Renders the site again and reloads nginx, putting the old
            file back when nginx refuses the new one.

    Raises:
        ValidationError: The stack or its site cannot use the relay; what is
            wrong and how to fix it is the message.
        DeploymentError: nginx did not take the site; the mode is off again.
    """
    deployer = deployer if deployer is not None else _stack_deployer(app)
    refresh = refresh if refresh is not None else _refresh_site
    store = deployer.store
    domain = app.domain
    try:
        _load_services(deployer)
    except DeploymentError as exc:
        raise _refusal(domain, [check_relay_eligible(deployer)[0]]) from exc
    problems = _stack_problems(deployer)
    if problems:
        raise _refusal(domain, problems)

    web = deployer.webserver_manager()
    operator = web.site_exists(domain) and is_operator_site(web, domain)
    targets = relay_targets(deployer.services)
    if not operator:
        ports = _routed_ports(deployer) if web.site_exists(domain) else set()
        wanted = [target for target in targets.values() if target.host_port in ports]
    else:
        wanted = list(targets.values())
    written: list[str] = []
    for target in wanted:
        # A file already there may name a relay an update left serving: it
        # stays as it is, and the next update resolves it.
        if not read_servers(deployer.app_name, target.service):
            write_servers(deployer.app_name, target.service, [target.host_port])
            written.append(target.service)
            logger.substep(
                f"{servers_file(deployer.app_name, target.service)} names "
                f"127.0.0.1:{target.host_port}"
            )

    def remove_written() -> None:
        for service in written:
            try:
                remove_servers(deployer.app_name, service)
            except NoustError as exc:
                logger.warning(f"The servers file of {service} was not removed: {exc}")

    problems = _site_problems(deployer, web)
    if problems:
        if not operator:
            remove_written()
        raise _refusal(domain, problems)

    store.set_zero_downtime(domain, True, drain_seconds=drain_seconds)
    app.zero_downtime, app.drain_seconds = True, drain_seconds
    if operator:
        logger.substep(f"Your site of {domain} already includes the servers files; it is unchanged")
        return
    try:
        logger.substep("Switching the site to the servers files")
        refresh(app)
    except NoustError:
        store.set_zero_downtime(domain, False)
        app.zero_downtime = False
        try:
            # Rendered again rather than trusted: a reload that failed leaves
            # the new site on disk, and it includes the files about to go.
            refresh(app)
        except NoustError as exc:
            logger.warning(f"The site of {domain} was not rendered again: {exc}")
        remove_written()
        raise


def disable_relay(
    app: App,
    *,
    logger: Logger,
    deployer: DockerComposeDeployer | None = None,
    refresh: Callable[[App], None] | None = None,
    relay: ComposeRelay | None = None,
) -> None:
    """
    Turn the relay off for a stack: the mode, Noust's site, and its servers files.

    A relay still serving from an update that did not finish is given back
    first. Noust's own site goes back to proxying to the ports directly, and
    the files go once nothing includes them; an operator's site keeps
    including them, so they stay, naming each service's own port.

    Args:
        app: The application's row; updated in place.
        logger: Where the steps are reported.
        deployer: The stack's deployer. Defaults to the one built from the row.
        refresh: Renders the site again and reloads nginx.
        relay: The engine that resolves a leftover relay. Defaults to one
            over ``deployer``.

    Raises:
        DeploymentError: A relay still serves and its service does not answer,
            or nginx did not take the site; the mode is on as it was.
    """
    deployer = deployer if deployer is not None else _stack_deployer(app)
    refresh = refresh if refresh is not None else _refresh_site
    store = deployer.store
    domain = app.domain
    _load_services(deployer)
    engine = relay if relay is not None else ComposeRelay(deployer)
    problem = engine.resolve_leftovers(list(relay_targets(deployer.services)))
    if problem is not None:
        raise DeploymentError(f"The relay of {domain} was not turned off", details=problem)

    web = deployer.webserver_manager()
    operator = web.site_exists(domain) and is_operator_site(web, domain)
    drain = app.drain_seconds
    store.set_zero_downtime(domain, False)
    app.zero_downtime = False
    if operator:
        logger.substep(
            f"Your site of {domain} includes the servers files; they stay, each naming its "
            "service's own port"
        )
        return
    try:
        logger.substep("Switching the site back to the containers' ports")
        refresh(app)
    except NoustError:
        store.set_zero_downtime(domain, True, drain_seconds=drain)
        app.zero_downtime = True
        raise
    try:
        remove_servers(deployer.app_name)
    except NoustError as exc:
        logger.warning(f"The servers files of {domain} were not removed: {exc}")


@dataclass(frozen=True)
class LeftoverRelay:
    """
    A relay an update that did not finish left behind, found by ``noust doctor``.

    Attributes:
        domain: The stack's domain.
        what: What was left, in one sentence.
        fix: The command that resolves it.
    """

    domain: str
    what: str
    fix: str


def leftover_relays(
    store: NoustStore | None = None, *, runner: CommandRunner | None = None
) -> list[LeftoverRelay]:
    """
    Find, across every stack, the relays an interrupted update left. Changes nothing.

    An update killed between pointing a servers file at a relay and pointing
    it back leaves the site served by a container nothing manages; the next
    update resolves it before anything else (:meth:`ComposeRelay.resolve_leftovers`),
    and so does turning the mode off. This says so before that, and how.

    Args:
        store: The store; the process-wide one when omitted.
        runner: Where ``docker`` is asked; the process-wide runner when omitted.

    Returns:
        What each stack has left, with the command that resolves it.
    """
    from noust.core.store import get_store
    from noust.deployers.docker_compose import stack_deployer

    store = store if store is not None else get_store()
    found: list[LeftoverRelay] = []
    for app in store.list_apps():
        if app.app_type != AppType.DOCKER_COMPOSE.value:
            continue
        app_name = domain_to_app_name(app.domain)
        has_files = os.path.lexists(_upstreams_dir() / app_name)
        if not app.zero_downtime and not has_files:
            continue
        deployer = stack_deployer(app, runner=runner)
        try:
            _load_services(deployer)
        except DeploymentError as exc:
            _logger.warning("Could not read the compose file of %s: %s", app.domain, exc)
            continue
        fix = (
            f"noust update {app.domain} (the relay is resolved before anything is rebuilt), or "
            f"noust app zero-downtime {app.domain} off"
        )
        relay = ComposeRelay(deployer)
        for what in relay.leftovers(list(relay_targets(deployer.services))):
            found.append(LeftoverRelay(domain=app.domain, what=what, fix=fix))
    return found


def _names_a_relay(pointed: Sequence[int], exists: bool) -> bool:
    """
    Tell whether a servers file that does not name a service's own port names a relay.

    A port the compose file moved (3000 to 3001) leaves the file on the old
    own port until the update moves it; taking that for a relay refused every
    update and ``zero-downtime off``. A relay is only ever given a port in
    :data:`RELAY_PORT_RANGE`, and is a container of a known name.

    Args:
        pointed: The ports the servers file names.
        exists: Whether the service's relay container exists.

    Returns:
        True when the file sends the traffic to a relay.
    """
    start, end = RELAY_PORT_RANGE
    return exists or any(start <= port < end for port in pointed)


def _upstreams_dir() -> Path:
    """The servers files' directory, read when asked so a test can point it elsewhere."""
    from noust.managers import webserver

    return webserver.NGINX_UPSTREAMS_DIR


def relay_status(app: App) -> tuple[int | None, str | None, str | None]:
    """
    Describe the relay of a stack in zero-downtime mode. Changes nothing.

    Only files are read: the servers file of the service in front, and
    whether any names something other than its service's own port.

    Args:
        app: The application's row.

    Returns:
        The port nginx sends ``/`` to, and when a relay left by an update
        that did not finish still serves, what and how to resolve it.
    """
    from noust.deployers.docker_compose import DockerComposeDeployer
    from noust.deployers.helpers.layout import app_root

    deployer = DockerComposeDeployer()
    deployer.app_path = app_root(app)
    deployer.domain = app.domain
    deployer.app_name = domain_to_app_name(app.domain)
    try:
        _load_services(deployer)
    except DeploymentError:
        return None, None, None
    targets = relay_targets(deployer.services)
    root = web_root_service(deployer.services)
    front = read_servers(deployer.app_name, root) if root in targets and root else []
    for target in targets.values():
        pointed = read_servers(deployer.app_name, target.service)
        if pointed and pointed != [target.host_port]:
            return (
                front[0] if front else None,
                f"A relay left by an update that did not finish serves {target.service} on "
                f"127.0.0.1:{pointed[0]}",
                f"The next update gives it back to its own container once that answers: "
                f"noust update {app.domain}",
            )
    return (front[0] if front else None), None, None


def _paragraphs(*parts: str | None) -> str:
    """
    Join the non-empty parts of an error's details with blank lines.

    Args:
        parts: The parts.

    Returns:
        The details.
    """
    return "\n\n".join(part.strip() for part in parts if part and part.strip())
