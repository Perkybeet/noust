# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site file reaches on this machine, live.

The analyzer (:mod:`noust.managers.siteconf`) reads text and knows that
``nestjs_upstream`` is ``127.0.0.1:3000``. The console's diagram has to say
what that port *is*: a Noust application, a Compose service, a systemd unit
somebody else runs, or nothing at all - and whether it answers, and when the
certificate the server presents runs out. This module answers that, read-only:

- **Who owns a port.** A container that publishes it (``docker ps`` and its
  Compose labels), else the Noust application the store records on it, else
  the unit whose cgroup holds the process ``ss -ltnpH`` names.
- **Whether it answers.** A TCP connection to the loopback with a one-second
  deadline (:func:`noust.core.app_state.port_answers`). Only the loopback: an
  address on another machine is listed but never probed, so the console
  cannot be used to scan a network from the server.
- **The certificate's expiry**, from :class:`~noust.managers.cert_manager.CertManager`.

Every probe is cached for :data:`CACHE_SECONDS`, so a console polling the
diagram does not run ``docker ps`` on every refresh. Commands go through the
command runner and are declared read-only probes (``docker ps``, ``ss
-ltnpH``, ``certbot certificates``, ``openssl x509``), so they also answer under
``--dry-run``.

:func:`include_reader` is the one way the site API resolves an ``include``:
the web server's own directory and Noust's files, nothing a link points to
outside them.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar

from noust.core import app_state
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, get_store
from noust.managers.siteconf.model import (
    IncludeReader,
    Location,
    Serializable,
    SiteStructure,
    split_url,
)
from noust.managers.webserver import include_glob_matches

#: How long a probe's answer is reused, in seconds.
CACHE_SECONDS = 10.0

#: Deadline of the TCP connection that says whether a port answers.
CONNECT_TIMEOUT = 1.0

#: Deadline of ``docker ps``; it answers from the daemon's memory.
DOCKER_TIMEOUT = 10

#: Largest file an include may bring in. A site's include is a snippet; a
#: bigger file is a mistake or something that is not configuration.
MAX_INCLUDE_BYTES = 1024 * 1024

#: Most files one include may expand to.
MAX_INCLUDE_FILES = 64

#: Host names that are this machine.
_LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost"})

_T = TypeVar("_T")

OwnerKind = Literal["app", "compose", "container", "unit", "process"]


# ---------------------------------------------------------------------------
# Includes
# ---------------------------------------------------------------------------


def include_reader(conf_root: Path, *, extra_roots: Sequence[Path] = ()) -> IncludeReader:
    """
    Build the reader the analyzer resolves ``include`` directives with.

    Args:
        conf_root: The web server's configuration directory (``/etc/nginx``);
            relative includes are resolved against it, as the web server does.
        extra_roots: Further directories Noust writes and a site may include
            (the servers files under ``/etc/nginx/noust-upstreams``).

    Returns:
        A reader that returns ``(path, text)`` for each file an include
        names, in name order, and raises ``ValueError`` for anything outside
        those directories - directly or through a symbolic link - for a
        wildcard in a directory, and for a file too large to be a snippet;
        ``OSError`` when a file cannot be read. The analyzer records either
        on the include instead of failing.
    """
    roots = [conf_root, *extra_roots]

    def inside(path: Path) -> bool:
        for root in roots:
            try:
                real_root = root.resolve()
            except OSError:
                continue
            if path == real_root or real_root in path.parents:
                return True
        return False

    def outside(pattern: str) -> ValueError:
        allowed = ", ".join(str(root) for root in roots)
        return ValueError(f"{pattern} is outside {allowed}; Noust does not read it")

    def read(pattern: str) -> list[tuple[str, str]]:
        if not pattern or "\x00" in pattern:
            raise ValueError("An include needs a file name")
        written = Path(pattern) if pattern.startswith("/") else conf_root / pattern
        directory, name = written.parent, written.name
        if any(char in str(directory) for char in "*?["):
            raise ValueError(f"{pattern}: a wildcard in a directory is not followed")
        real_directory = directory.resolve(strict=True)
        if not inside(real_directory):
            raise outside(pattern)
        if any(char in name for char in "*?["):
            candidates = [
                directory / entry.name
                for entry in sorted(real_directory.iterdir(), key=lambda entry: entry.name)
                if include_glob_matches(entry.name, name)
            ]
        else:
            candidates = [written]
        if len(candidates) > MAX_INCLUDE_FILES:
            raise ValueError(f"{pattern} matches more than {MAX_INCLUDE_FILES} files")
        found: list[tuple[str, str]] = []
        for candidate in candidates:
            real = candidate.resolve(strict=True)
            if not inside(real):
                raise outside(str(candidate))
            if not real.is_file():
                continue
            if real.stat().st_size > MAX_INCLUDE_BYTES:
                raise ValueError(f"{candidate} is too large to be a configuration snippet")
            found.append((str(candidate), real.read_text(encoding="utf-8", errors="replace")))
        return found

    return read


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


@dataclass
class PortOwner(Serializable):
    """
    Who holds a port.

    Attributes:
        kind: ``app`` (a Noust application records it), ``compose`` (a
            Compose service publishes it), ``container`` (a container outside
            Compose), ``unit`` (a systemd service) or ``process`` (a process
            outside any service).
        app: Domain of the Noust application it belongs to, when one does.
        project: Compose project.
        service: Compose service.
        container: Container name.
        unit: systemd unit.
        process: Process name, as ``ss`` reports it.
        pid: Process id.
    """

    kind: OwnerKind
    app: str | None = None
    project: str | None = None
    service: str | None = None
    container: str | None = None
    unit: str | None = None
    process: str | None = None
    pid: int | None = None


@dataclass
class Backend(Serializable):
    """
    One address a site hands requests to, with what is behind it now.

    Attributes:
        address: ``host:port`` (lowercased) or ``unix:/path``; the key.
        written: Every spelling the site uses for it (an upstream server's
            address, a proxy URL), to match the structure's elements.
        host: The host, None for a Unix socket.
        port: The port, None for a Unix socket or an upstream defined in
            another file.
        local: Whether it is this machine (and so was probed).
        upstreams: Names of the upstreams listing it.
        locations: Ids of the locations reaching it, directly or through an
            upstream.
        owner: Who holds the port; None when nobody does or it could not be
            told.
        listening: Whether a socket listens on the port (``ss``); None when
            not asked or not answerable.
        reachable: Whether a connection to it is accepted; None when it was
            not probed.
    """

    address: str
    host: str | None
    port: int | None
    local: bool
    written: list[str] = field(default_factory=list)
    upstreams: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    owner: PortOwner | None = None
    listening: bool | None = None
    reachable: bool | None = None


@dataclass
class CertificateFacts(Serializable):
    """
    The certificate a server presents.

    Attributes:
        server_id: The server's id.
        path: The certificate file the configuration names.
        name: certbot's name for it, None when certbot does not manage it.
        domains: Names it covers, per certbot.
        expiry: Expiry date, ``YYYY-MM-DD``; None when unknown.
        days_left: Days until it expires (negative once expired).
    """

    server_id: str
    path: str
    name: str | None = None
    domains: list[str] = field(default_factory=list)
    expiry: str | None = None
    days_left: int | None = None


@dataclass
class Topology(Serializable):
    """
    The live facts of a site.

    Attributes:
        backends: Every address the site reaches, in order of appearance.
        certificates: One entry per server presenting a certificate.
        docker: Whether ``docker ps`` answered; when False, a port a
            container publishes is told only by ``ss``.
    """

    backends: list[Backend] = field(default_factory=list)
    certificates: list[CertificateFacts] = field(default_factory=list)
    docker: bool = False


@dataclass(frozen=True)
class _Container:
    name: str
    project: str | None
    service: str | None
    working_dir: str | None


#: The TCP probe: ``(port, host, timeout) -> accepted``.
Connect = Callable[[int, str, float], bool]


def _default_connect(port: int, host: str, timeout: float) -> bool:
    # Looked up on each call so a sandbox (scripts/console_server.py) that
    # replaces the application state's probe is obeyed here too.
    return app_state.port_answers(port, host, timeout)


class TopologyProbe:
    """
    Read the live facts of a site, caching each probe for a while.

    Args:
        runner: Runs ``docker ps`` and ``ss``. The process-wide runner when None.
        store: Where applications and services are recorded. The store when None.
        certs: Lists certificates (``list_certificates()``). A
            :class:`~noust.managers.cert_manager.CertManager` when None.
        clock: Monotonic seconds, for the cache.
        today: Today's date, for the days left on a certificate.
        connect: The TCP probe.
        proc: Where ``/proc`` is, to name a process's unit.
        ttl: Seconds a probe's answer is reused.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        store: Any = None,
        certs: Any = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
        connect: Connect | None = None,
        proc: Path = Path("/proc"),
        ttl: float = CACHE_SECONDS,
    ) -> None:
        self._runner = runner
        self._store = store
        self._certs = certs
        self._clock = clock
        self._today = today
        self._connect = connect or _default_connect
        self._proc = proc
        self._ttl = ttl
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._lock = threading.Lock()

    # -- cache ---------------------------------------------------------------

    def _cached(self, key: tuple[Any, ...], compute: Callable[[], _T]) -> _T:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                value: _T = hit[1]
                return value
        value = compute()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def clear(self) -> None:
        """Forget every cached answer."""
        with self._lock:
            self._cache.clear()

    @property
    def runner(self) -> CommandRunner:
        """The runner probes go through."""
        return self._runner or get_runner()

    # -- probes --------------------------------------------------------------

    def reachable(self, port: int) -> bool:
        """
        Ask whether the loopback accepts a connection on a port.

        Args:
            port: The port.

        Returns:
            True when the connection was accepted within :data:`CONNECT_TIMEOUT`.
        """
        return self._cached(
            ("connect", port), lambda: self._connect(port, "127.0.0.1", CONNECT_TIMEOUT)
        )

    def _containers(self) -> dict[int, _Container] | None:
        """Containers by the host port they publish, None when Docker did not answer."""
        return self._cached(("docker",), self._read_containers)

    def _read_containers(self) -> dict[int, _Container] | None:
        from noust.managers.server.security_firewall import parse_docker_ps
        from noust.managers.server.security_sockets import ANY_ADDRESSES

        result = self.runner.run(["docker", "ps", "--format", "{{json .}}"], timeout=DOCKER_TIMEOUT)
        if not result.success:
            return None
        labels_of: dict[str, dict[str, str]] = {}
        for line in result.stdout.splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                labels_of[str(entry.get("Names", ""))] = dict(
                    item.split("=", 1)
                    for item in str(entry.get("Labels", "")).split(",")
                    if "=" in item
                )
        published: dict[int, _Container] = {}
        for port in parse_docker_ps(result.stdout):
            if port.proto != "tcp" or not (
                port.host_address in ANY_ADDRESSES or _is_loopback(port.host_address)
            ):
                continue
            labels = labels_of.get(port.container, {})
            published.setdefault(
                port.host_port,
                _Container(
                    name=port.container,
                    project=labels.get("com.docker.compose.project"),
                    service=labels.get("com.docker.compose.service"),
                    working_dir=labels.get("com.docker.compose.project.working_dir"),
                ),
            )
        return published

    def _listeners(self, port: int) -> list[Any] | None:
        from noust.deployers.bluegreen import listeners_on

        return self._cached(
            ("ss", port), lambda: listeners_on(port, runner=self.runner, proc=self._proc)
        )

    def _certificates(self) -> list[Any]:
        def read() -> list[Any]:
            if self._certs is None:
                from noust.managers.cert_manager import CertManager

                return list(CertManager(verbose=False).list_certificates())
            return list(self._certs.list_certificates())

        return self._cached(("certs",), read)

    # -- the topology --------------------------------------------------------

    def topology(self, model: SiteStructure) -> Topology:
        """
        Say what each address of a site is, and whether it answers.

        Args:
            model: The site's structure.

        Returns:
            The backends and certificates, live.
        """
        store = self._store if self._store is not None else get_store()
        apps = list(store.list_apps())
        services = list(store.list_services())
        containers = self._containers()
        topology = Topology(docker=containers is not None)
        for backend in _backends(model):
            if backend.local and backend.port is not None:
                self._describe(backend, backend.port, apps, services, containers or {})
            topology.backends.append(backend)
        topology.certificates = self._certificate_facts(model)
        return topology

    def _describe(
        self,
        backend: Backend,
        port: int,
        apps: list[App],
        services: list[Any],
        containers: dict[int, _Container],
    ) -> None:
        listeners = self._listeners(port)
        backend.listening = None if listeners is None else bool(listeners)
        backend.reachable = self.reachable(port)
        container = containers.get(port)
        if container is not None:
            backend.owner = PortOwner(
                kind="compose" if container.project else "container",
                app=_app_of_container(container, apps),
                project=container.project,
                service=container.service,
                container=container.name,
            )
            return
        by_id = {app.id: app for app in apps}
        for service in services:
            app = by_id.get(service.app_id)
            if service.port == port and app is not None:
                backend.owner = PortOwner(
                    kind="app", app=app.domain, unit=f"{service.name}.service"
                )
                return
        for app in apps:
            if app.port == port:
                backend.owner = PortOwner(kind="app", app=app.domain)
                return
        if listeners:
            first = listeners[0]
            unit = first.unit if first.unit and first.unit.endswith(".service") else None
            backend.owner = PortOwner(
                kind="unit" if unit else "process",
                unit=unit,
                process=first.process,
                pid=first.pid,
            )

    def _certificate_facts(self, model: SiteStructure) -> list[CertificateFacts]:
        wanted = [
            (server.id, server.tls.certificate)
            for server in model.servers
            if server.tls is not None and server.tls.certificate
        ]
        if not wanted:
            return []
        known = self._certificates()
        facts: list[CertificateFacts] = []
        for server_id, path in wanted:
            entry = CertificateFacts(server_id=server_id, path=str(path))
            match = next(
                (
                    cert
                    for cert in known
                    if cert.cert_path == path or (cert.name and f"/live/{cert.name}/" in str(path))
                ),
                None,
            )
            if match is not None:
                entry.name = match.name
                entry.domains = list(match.domains)
                entry.expiry = match.expiry
                if match.expiry:
                    try:
                        entry.days_left = (date.fromisoformat(match.expiry) - self._today()).days
                    except ValueError:
                        entry.days_left = None
            facts.append(entry)
        return facts


def _app_of_container(container: _Container, apps: Iterable[App]) -> str | None:
    """The Noust application a container belongs to, by project or directory."""
    for app in apps:
        if container.project and app.compose_project == container.project:
            return app.domain
        if container.working_dir and app.app_path:
            root = app.app_path.rstrip("/")
            if container.working_dir == root or container.working_dir.startswith(root + "/"):
                return app.domain
    return None


def _is_loopback(host: str) -> bool:
    """Whether a host names this machine."""
    bare = host.strip("[]").lower()
    if bare in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def _endpoint(written: str, *, url: bool) -> tuple[str, str | None, int | None] | None:
    """
    Normalise an upstream server or a backend URL.

    Args:
        written: ``127.0.0.1:3000``, ``http://localhost:3000/x``,
            ``unix:/run/x.sock``, ``backend.internal``...
        url: Whether it is a proxy URL rather than an upstream server.

    Returns:
        ``(address, host, port)``, or None when it names nothing addressable.
    """
    scheme, host, port = split_url(written)
    if host is None:
        rest = written.split("://", 1)[-1]
        return (rest, None, None) if rest.startswith("unix:") else None
    host = host.lower()
    if port is None:
        if not url:
            # nginx's default for an upstream server.
            port = 80
        elif "." in host or _is_loopback(host):
            port = 443 if scheme == "https" else 80
        # Otherwise a bare word: an upstream another file defines, not a host.
    address = f"{host}:{port}" if port is not None else host
    return address, host, port


def _backends(model: SiteStructure) -> list[Backend]:
    """Every address a site reaches, merged by address, in order of appearance."""
    found: dict[str, Backend] = {}

    def add(
        written: str, *, upstream: str | None, locations: Iterable[str], url: bool = False
    ) -> None:
        endpoint = _endpoint(written, url=url)
        if endpoint is None:
            return
        address, host, port = endpoint
        backend = found.get(address)
        if backend is None:
            backend = Backend(
                address=address,
                host=host,
                port=port,
                local=host is not None and port is not None and _is_loopback(host),
            )
            found[address] = backend
        if written not in backend.written:
            backend.written.append(written)
        if upstream is not None and upstream not in backend.upstreams:
            backend.upstreams.append(upstream)
        for location in locations:
            if location not in backend.locations:
                backend.locations.append(location)

    for upstream in model.upstreams:
        for member in upstream.servers:
            add(member.address, upstream=upstream.name, locations=upstream.used_by)

    def walk(locations: list[Location]) -> None:
        for location in locations:
            target = location.target
            if target.kind == "proxy" and target.upstream is None:
                add(
                    target.url or target.address or "",
                    upstream=None,
                    locations=[location.id],
                    url=target.url is not None,
                )
            elif target.kind == "fastcgi" and target.address:
                add(target.address, upstream=None, locations=[location.id])
            walk(location.locations)

    for server in model.servers:
        walk(server.locations)
    return list(found.values())


_DEFAULT: TopologyProbe | None = None
_DEFAULT_LOCK = threading.Lock()


def default_probe() -> TopologyProbe:
    """
    The process-wide probe, whose cache every request shares.

    Returns:
        The probe.
    """
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = TopologyProbe()
        return _DEFAULT
