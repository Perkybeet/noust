# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TCP and UDP ports published in front of a backend: nginx's ``stream`` module.

Not everything an application offers is HTTP: a game server, a message broker, a
DNS or WireGuard endpoint, a database a desktop client connects to. Noust publishes
such a port with one nginx ``server`` in the ``stream`` context, per exposure, in
``/etc/nginx/noust-stream/<name>.conf``, and opens the port in the firewall for the
sources it was given.

**What stands between a request and a published port**, in this order, so a port is
never half published:

1. The request is validated and every port question is answered before anything is
   touched: a port something already listens on, one an exposure (or a database made
   public) holds, SSH's, the console's, 80 and 443 are refused with who holds them.
2. nginx must have the module (``nginx -V``), have it loaded (``nginx -T``), and read
   a ``stream`` block that includes Noust's directory. On Debian and Ubuntu the block
   is one file in ``modules-enabled`` that Noust writes; where an operator already has
   a ``stream`` block, or there is no such directory, Noust says the one line to add and
   changes nothing (an operator's configuration is never rewritten, only included).
3. The row is written first (``opening``), then the file, then nginx's own test of the
   whole configuration. When nginx refuses, the file is removed and nginx's words are
   the error; nothing is reloaded.
4. After the reload the firewall is told to allow the port, through the one firewall
   manager. Only an allow rule is made (it cannot lock anyone out) and only what Noust
   made is recorded and closed again.

Closing reverses it from the row, and an interrupted opening or closing is finished by
asking for the same thing again: the row says how far it got.

UDP is a session, not a connection: ``proxy_timeout`` is how long a quiet client keeps
its session. nginx cannot publish SCTP.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import (
    NoustError,
    PrerequisiteError,
    SecurityError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.package_family import PackageFamily, install_hint
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore, get_store
from noust.core.stream_store import CLOSING, OPEN, OPENING, StreamExposure, StreamStore
from noust.managers.database.exposure import listening_sockets
from noust.managers.port_claims import PortClaims
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.siteconf import ParseError, parse
from noust.managers.siteconf.tree import Block, Directive, Tree

logger = logging.getLogger(__name__)

#: The directory of nginx's configuration that holds one file per exposure.
STREAM_DIR_NAME = "noust-stream"

#: The loader Noust writes where nginx includes a ``modules-enabled`` directory.
LOADER_NAME = "60-noust-stream.conf"

#: Names an exposure may have: they are file names.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

#: nginx's ``-V`` mentions the module as ``--with-stream`` or ``--with-stream=dynamic``.
_WITH_STREAM = re.compile(r"--with-stream(?:=(?P<kind>\w+))?(?=\s|$)")

#: The most addresses an exposure may be restricted to.
MAX_SOURCES = 20

#: Idle time bounds, in seconds.
MIN_TIMEOUT = 1
MAX_TIMEOUT = 86400
DEFAULT_TIMEOUT = 3600

_PROBE_TIMEOUT = 15
_FIREWALL_COMMENT = "noust stream"


@dataclass(frozen=True)
class StreamSupport:
    """
    Whether this server's nginx can publish a TCP or UDP port, and what is missing.

    Attributes:
        available: Everything needed is in place or can be put in place by Noust.
        module: ``static`` (built in), ``dynamic`` (loaded as a module) or ``missing``.
        loader: ``present`` (a ``stream`` block includes Noust's directory), ``installable``
            (Noust can write it), ``foreign`` (the operator has a ``stream`` block that
            has to include the directory) or ``manual`` (add a block to ``nginx.conf``).
        problem: What is missing, when something is.
        hint: How to fix it, as a command or as the line to add.
    """

    available: bool
    module: str
    loader: str
    problem: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The support for the API and the CLI.
        """
        return {
            "available": self.available,
            "module": self.module,
            "loader": self.loader,
            "problem": self.problem,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class StreamPlan:
    """
    What opening a port would do, decided and checked before anything is touched.

    Attributes:
        exposure: The exposure as it would be recorded.
        config: The text of its nginx file.
        file: Where that file goes.
        loader: ``present``, or ``write`` when Noust would write the loader.
        support: What nginx offers.
        warnings: Things that are probably wrong and do not stop it.
        firewall: What the firewall would be asked for, one entry per rule.
    """

    exposure: StreamExposure
    config: str
    file: Path
    loader: str
    support: StreamSupport
    warnings: tuple[str, ...] = ()
    firewall: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan for the API and the CLI.
        """
        return {
            "exposure": self.exposure.to_dict(),
            "config": self.config,
            "file": str(self.file),
            "loader": self.loader,
            "support": self.support.to_dict(),
            "warnings": list(self.warnings),
            "firewall": list(self.firewall),
        }


@dataclass(frozen=True)
class StreamResult:
    """
    What opening or closing a port did.

    Attributes:
        exposure: The exposure, as recorded now (or as it was, when closed).
        warnings: Things the operator should know.
        firewall: A sentence on the firewall: what was opened, or why nothing was.
        rehearsal: Nothing was changed: this is what would have been.
    """

    exposure: StreamExposure
    warnings: tuple[str, ...] = field(default_factory=tuple)
    firewall: str = ""
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the CLI.
        """
        return {
            "exposure": self.exposure.to_dict(),
            "warnings": list(self.warnings),
            "firewall": self.firewall,
            "rehearsal": self.rehearsal,
        }


FirewallFactory = Callable[[CommandRunner], Firewall]


def _default_firewall(runner: CommandRunner) -> Firewall:
    """
    Args:
        runner: The runner to ask through.

    Returns:
        The one firewall manager, acting as this module.
    """
    return Firewall(SecurityProbe(runner=runner), None, actor="noust stream")


class StreamExposures:
    """
    Publish TCP and UDP ports through nginx's ``stream`` module, and take them down.

    Args:
        store: The store; the process-wide one by default.
        runner: Runs nginx and ``ss``; the process-wide runner by default.
        fs: Writes the files; the process-wide filesystem by default.
        conf_root: nginx's configuration directory; ``/etc/nginx`` by default.
        firewall: Builds the firewall manager (tests replace it).
        template: Renders an exposure's nginx file (tests replace it); the
            ``stream.conf.j2`` template of the nginx backend by default.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        conf_root: Path | None = None,
        firewall: FirewallFactory | None = None,
        template: Callable[[StreamExposure], str] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._fs = fs
        self._conf_root = conf_root
        self._firewall = firewall or _default_firewall
        self._template = template
        self.records = StreamStore(store)

    @property
    def runner(self) -> CommandRunner:
        """The runner that runs nginx and ``ss``."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem the files are written through."""
        return self._fs or get_fs()

    @property
    def conf_root(self) -> Path:
        """nginx's configuration directory."""
        if self._conf_root is not None:
            return self._conf_root
        from noust.managers.webserver import NGINX_BACKEND

        return NGINX_BACKEND.sites_available.parent

    @property
    def directory(self) -> Path:
        """The directory that holds one file per exposure."""
        return self.conf_root / STREAM_DIR_NAME

    @property
    def include_line(self) -> str:
        """The line a ``stream`` block needs to carry Noust's exposures."""
        return f"include {self.directory}/*.conf;"

    def file_for(self, name: str) -> Path:
        """
        Args:
            name: An exposure's name.

        Returns:
            Its nginx file.
        """
        return self.directory / f"{name}.conf"

    # ------------------------------------------------------------------ reading

    def all(self) -> list[StreamExposure]:
        """
        Returns:
            Every exposure, by port.
        """
        return self.records.all()

    def get(self, name: str) -> StreamExposure:
        """
        Args:
            name: An exposure's name.

        Returns:
            The exposure.

        Raises:
            NoustError: There is none of that name (the API answers 404).
        """
        found = self.records.get(name)
        if found is None:
            from noust.core.teams.model import ResourceNotFoundError

            raise ResourceNotFoundError(
                f"No published port is called {name}",
                details="List them with: noust stream list",
            )
        return found

    def support(self) -> StreamSupport:
        """
        Find out whether nginx can publish a port, and what the operator has to do if not.

        Returns:
            The answer; never raises for a missing piece.
        """
        runner = self.runner
        if not runner.exists("nginx"):
            return StreamSupport(
                False,
                "missing",
                "manual",
                "nginx is not installed",
                install_hint("nginx", runner=runner),
            )
        version = runner.run(["nginx", "-V"], timeout=_PROBE_TIMEOUT)
        built = _WITH_STREAM.search(f"{version.stdout}\n{version.stderr}")
        if built is None:
            return StreamSupport(
                False,
                "missing",
                "manual",
                "This nginx was built without the stream module",
                install_hint(
                    {
                        PackageFamily.APT: "libnginx-mod-stream",
                        PackageFamily.DNF: "nginx-mod-stream",
                        PackageFamily.ZYPPER: "",
                    },
                    notes={
                        PackageFamily.ZYPPER: "openSUSE ships nginx with stream; install the "
                        "nginx package from the distribution or from nginx.org."
                    },
                    runner=runner,
                ),
            )
        kind = "dynamic" if built.group("kind") == "dynamic" else "static"
        dump = runner.run(["nginx", "-T"], timeout=_PROBE_TIMEOUT)
        if not dump.success:
            return StreamSupport(
                False,
                kind,
                "manual",
                "nginx cannot read its own configuration",
                (dump.stderr or dump.stdout).strip() or "Run: nginx -t",
            )
        try:
            tree = parse(dump.stdout, "nginx")
        except ParseError as exc:
            return StreamSupport(
                False, kind, "manual", f"nginx's configuration is not readable: {exc}"
            )
        loaded = kind == "static" or _loads_stream_module(tree)
        if not loaded:
            return StreamSupport(
                False,
                kind,
                "manual",
                "The stream module is built as a module and nothing loads it",
                install_hint(
                    {
                        PackageFamily.APT: "libnginx-mod-stream",
                        PackageFamily.DNF: "nginx-mod-stream",
                        PackageFamily.ZYPPER: "",
                    },
                    notes={
                        PackageFamily.ZYPPER: "Add 'load_module modules/ngx_stream_module.so;' "
                        "at the top of nginx.conf."
                    },
                    runner=runner,
                ),
            )
        loader = self._loader_state(tree)
        if loader == "present":
            return StreamSupport(True, kind, loader)
        if loader == "installable":
            return StreamSupport(True, kind, loader)
        if loader == "foreign":
            return StreamSupport(
                False,
                kind,
                loader,
                "nginx.conf already has a stream block that does not include Noust's ports",
                f"Add this line inside it, then run the command again: {self.include_line}",
            )
        return StreamSupport(
            False,
            kind,
            loader,
            "nginx.conf has no stream block for Noust's ports",
            "Add this at the top level of nginx.conf (beside http), then run the command "
            f"again: stream {{ {self.include_line} }}",
        )

    def _loader_state(self, tree: Tree) -> str:
        """
        Find how Noust's directory reaches nginx.

        Args:
            tree: The parsed ``nginx -T`` output.

        Returns:
            ``present``, ``installable``, ``foreign`` or ``manual``.
        """
        wanted = str(self.directory / "*.conf")
        foreign = False
        modules_enabled = False
        for node in tree.children:
            if isinstance(node, Block) and node.name.value == "stream":
                includes = [
                    child.args[0].word.value
                    for child in node.children
                    if isinstance(child, Directive) and child.name.value == "include" and child.args
                ]
                if wanted in includes:
                    return "present"
                foreign = True
            elif isinstance(node, Directive) and node.name.value == "include" and node.args:
                if node.args[0].word.value.endswith("modules-enabled/*.conf"):
                    modules_enabled = True
        if foreign:
            return "foreign"
        return "installable" if modules_enabled else "manual"

    # ------------------------------------------------------------------ planning

    def plan(
        self,
        name: str,
        *,
        protocol: str,
        listen_port: int,
        target_port: int | None = None,
        target_host: str = "127.0.0.1",
        sources: Sequence[str] = (),
        timeout_seconds: int = DEFAULT_TIMEOUT,
        app_domain: str | None = None,
        actor: str | None = None,
    ) -> StreamPlan:
        """
        Check a request and say what it would do, changing nothing.

        Args:
            name: What to call it: lower case letters, digits and dashes.
            protocol: ``tcp`` or ``udp``.
            listen_port: The port to publish.
            target_port: The backend's port; the application's own when it is for one.
            target_host: The backend's address; this machine by default.
            sources: Addresses or networks allowed in; none means anyone.
            timeout_seconds: How long a quiet connection or session is kept.
            app_domain: The application it is for, when it is for one.
            actor: Who is asking.

        Returns:
            The plan.

        Raises:
            ValidationError: A field is not acceptable, the name or the port is taken,
                or something else listens on the port.
            PrerequisiteError: nginx cannot publish a port, and why.
        """
        if app_domain is not None:
            app = (self._store or get_store()).get_app(app_domain)
            if app is None:
                from noust.core.teams.model import ResourceNotFoundError

                raise ResourceNotFoundError(
                    f"There is no application called {app_domain}",
                    details="List them with: noust list",
                )
            if target_port is None:
                if not app.port:
                    raise ValidationError(
                        f"{app_domain} does not listen on a port of its own",
                        details="Say which port the backend listens on.",
                        field="target_port",
                    )
                target_port = int(app.port)
        if target_port is None:
            raise ValidationError(
                "Say which port the backend listens on",
                details="Give the backend's port, or name the application it is for.",
                field="target_port",
            )
        exposure = self._validated(
            name,
            protocol,
            listen_port,
            target_host,
            target_port,
            sources,
            timeout_seconds,
            app_domain,
            actor,
        )
        existing = self.records.get(exposure.name)
        self._check_port(exposure, existing)
        support = self.support()
        if not support.available:
            raise PrerequisiteError(
                support.problem or "nginx cannot publish a TCP or UDP port",
                details=support.hint or "",
            )
        warnings = self._warnings(exposure)
        rules = tuple(
            {"port": exposure.listen_port, "proto": exposure.protocol, "source": source}
            for source in (exposure.sources or ("any",))
        )
        return StreamPlan(
            exposure=exposure,
            config=self.render(exposure),
            file=self.file_for(exposure.name),
            loader="present" if support.loader == "present" else "write",
            support=support,
            warnings=warnings,
            firewall=rules,
        )

    def render(self, exposure: StreamExposure) -> str:
        """
        Args:
            exposure: An exposure.

        Returns:
            The text of its nginx file.
        """
        if self._template is not None:
            return self._template(exposure)
        from noust.managers.nginx_manager import NginxManager

        template = NginxManager(runner=self._runner, fs=self._fs).jinja_env.get_template(
            "stream.conf.j2"
        )
        rendered = template.render(
            name=exposure.name,
            protocol=exposure.protocol,
            udp=exposure.protocol == "udp",
            listen_port=exposure.listen_port,
            target=exposure.target,
            sources=list(exposure.sources),
            timeout_seconds=exposure.timeout_seconds,
        )
        # Jinja drops the last newline; a file of text ends with one.
        return rendered.rstrip("\n") + "\n"

    def _validated(
        self,
        name: str,
        protocol: str,
        listen_port: int,
        target_host: str,
        target_port: int,
        sources: Sequence[str],
        timeout_seconds: int,
        app_domain: str | None,
        actor: str | None,
    ) -> StreamExposure:
        """
        Check every field of a request.

        Args:
            name: The exposure's name.
            protocol: ``tcp`` or ``udp``.
            listen_port: The port to publish.
            target_host: The backend's address.
            target_port: The backend's port.
            sources: Addresses or networks.
            timeout_seconds: The idle time.
            app_domain: The application, when there is one.
            actor: Who is asking.

        Returns:
            The exposure, normalised.

        Raises:
            ValidationError: A field is not acceptable.
        """
        if not _NAME.fullmatch(name or ""):
            raise ValidationError(
                f"{name!r} cannot be the name of a published port",
                details="Use lower case letters, digits and dashes, up to 40, starting with a "
                "letter or digit.",
                field="name",
            )
        if protocol not in ("tcp", "udp"):
            raise ValidationError(
                f"{protocol!r} is not a protocol nginx publishes",
                details="Use tcp or udp. nginx cannot publish SCTP.",
                field="protocol",
            )
        for label, port in (("listen_port", listen_port), ("target_port", target_port)):
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValidationError(
                    f"{port!r} is not a port", details="A port is 1 to 65535.", field=label
                )
        host = (target_host or "127.0.0.1").strip().lower()
        if host == "localhost":
            host = "127.0.0.1"
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError as exc:
            raise ValidationError(
                f"{target_host!r} is not an IP address",
                details="Give the backend's address, such as 127.0.0.1 or 10.0.0.12. A name "
                "would be looked up when nginx reads its configuration, and a lookup that "
                "fails keeps nginx from reloading.",
                field="target_host",
            ) from exc
        if len(sources) > MAX_SOURCES:
            raise ValidationError(f"At most {MAX_SOURCES} sources can be listed", field="sources")
        cleaned: list[str] = []
        for source in sources:
            try:
                network = str(ipaddress.ip_network(str(source).strip(), strict=False))
            except ValueError as exc:
                raise ValidationError(
                    f"{source!r} is not an address or a network", field="sources"
                ) from exc
            if network in ("0.0.0.0/0", "::/0"):
                continue
            if network not in cleaned:
                cleaned.append(network)
        if (
            isinstance(timeout_seconds, bool)
            or not MIN_TIMEOUT <= int(timeout_seconds) <= MAX_TIMEOUT
        ):
            raise ValidationError(
                f"{timeout_seconds!r} is not a timeout",
                details=f"Seconds, {MIN_TIMEOUT} to {MAX_TIMEOUT}.",
                field="timeout_seconds",
            )
        return StreamExposure(
            name=name,
            protocol=protocol,
            listen_port=listen_port,
            target_host=host,
            target_port=target_port,
            sources=tuple(cleaned),
            timeout_seconds=int(timeout_seconds),
            app_domain=app_domain,
            created_by=actor,
        )

    def _check_port(self, exposure: StreamExposure, existing: StreamExposure | None) -> None:
        """
        Refuse a port that is not free to publish, and say who holds it.

        The question is the registry's (:class:`~noust.managers.port_claims.PortClaims`):
        the web server's, SSH's and the console's ports, applications and their copies, the
        ports a Compose stack publishes, other exposures, databases made reachable, the
        engines Noust runs, and whatever the kernel says listens.

        Args:
            exposure: The exposure asked for.
            existing: The exposure of the same name that is recorded, which may keep its port.

        Raises:
            ValidationError: The port is held, or the name is another exposure's.
        """
        if existing is not None and (
            existing.protocol != exposure.protocol
            or existing.listen_port != exposure.listen_port
            or existing.state != OPENING
        ):
            raise ValidationError(
                f"{exposure.name} is already a published port",
                details=f"It publishes {existing.protocol} {existing.listen_port}. "
                f"Close it first: noust stream remove {exposure.name}",
                field="name",
            )
        PortClaims(self.runner, self._store).require_free(
            exposure.listen_port,
            exposure.protocol,
            owner=exposure.name,
            field="listen_port",
            # An opening that was interrupted left nginx listening on its own port.
            ignore_processes=("nginx",) if existing is not None else (),
            reveal=True,
        )

    def _warnings(self, exposure: StreamExposure) -> tuple[str, ...]:
        """
        Find what is probably wrong with an exposure and does not stop it.

        Args:
            exposure: The exposure asked for.

        Returns:
            One sentence per finding.
        """
        found: list[str] = []
        if (
            exposure.protocol == "tcp"
            and ipaddress.ip_address(exposure.target_host).is_loopback
            and not any(
                socket.port == exposure.target_port for socket in listening_sockets(self.runner)
            )
        ):
            found.append(
                f"Nothing listens on {exposure.target} yet: visitors will be refused until "
                "the service is running"
            )
        if not exposure.sources:
            found.append(
                f"{exposure.protocol.upper()} {exposure.listen_port} will be open to everyone"
                + (
                    "; a service without its own authentication should be restricted to "
                    "known addresses"
                    if exposure.target_port in _AUTH_FREE_PORTS
                    else ""
                )
            )
        return tuple(found)

    # ------------------------------------------------------------------ opening

    def open(
        self,
        name: str,
        *,
        protocol: str,
        listen_port: int,
        target_port: int | None = None,
        target_host: str = "127.0.0.1",
        sources: Sequence[str] = (),
        timeout_seconds: int = DEFAULT_TIMEOUT,
        app_domain: str | None = None,
        actor: str | None = None,
    ) -> StreamResult:
        """
        Publish a port.

        Asking again for an exposure that stopped half way finishes it.

        Args:
            name: What to call it.
            protocol: ``tcp`` or ``udp``.
            listen_port: The port to publish.
            target_port: The backend's port; the application's own when it is for one.
            target_host: The backend's address; this machine by default.
            sources: Addresses or networks allowed in; none means anyone.
            timeout_seconds: How long a quiet connection or session is kept.
            app_domain: The application it is for, when it is for one.
            actor: Who is asking.

        Returns:
            What was done, or what would be under a rehearsal.

        Raises:
            ValidationError: The request is not acceptable, the port is held, or
                nginx refused the configuration (its words are the error).
            PrerequisiteError: nginx cannot publish a port.
            SecurityError: The firewall refused a rule; the port is closed again.
        """
        require_server_role("Published ports")
        plan = self.plan(
            name,
            protocol=protocol,
            listen_port=listen_port,
            target_port=target_port,
            target_host=target_host,
            sources=sources,
            timeout_seconds=timeout_seconds,
            app_domain=app_domain,
            actor=actor,
        )
        if is_rehearsal():
            return StreamResult(plan.exposure, plan.warnings, "", rehearsal=True)
        exposure = plan.exposure
        if self.records.get(exposure.name) is None:
            try:
                self.records.create(exposure)
            except sqlite3.IntegrityError as exc:
                raise ValidationError(
                    f"{exposure.protocol.upper()} {exposure.listen_port} or the name "
                    f"{exposure.name} was taken a moment ago",
                    field="listen_port",
                ) from exc
        loader_written = False
        try:
            if plan.loader == "write":
                self._write_loader()
                loader_written = True
            self.fs.make_dir(self.directory)
            self.fs.write_text(plan.file, plan.config)
            self._reload_or_undo(plan.file, loader_written)
            firewall = self._open_firewall(exposure)
        except (NoustError, OSError):
            self._abort(exposure)
            raise
        saved = self.records.set_state(exposure.name, OPEN, firewall) or exposure
        audit.record(
            "streams.open",
            target=f"stream:{exposure.name}",
            details={
                "protocol": exposure.protocol,
                "listen_port": exposure.listen_port,
                "target": exposure.target,
                "sources": list(exposure.sources),
                "app": exposure.app_domain,
            },
        )
        return StreamResult(saved, plan.warnings, _firewall_sentence(firewall))

    def _write_loader(self) -> None:
        """Write the file that gives nginx a ``stream`` block carrying Noust's directory."""
        directory = self.conf_root / "modules-enabled"
        self.fs.make_dir(directory)
        self.fs.write_text(
            directory / LOADER_NAME,
            "# Generated by Noust ('noust stream add'). Each published TCP or UDP port is a\n"
            f"# file of {self.directory}; this block only carries them.\n"
            f"stream {{\n    {self.include_line}\n}}\n",
        )

    def _reload_or_undo(self, file: Path, loader_written: bool) -> None:
        """
        Have nginx test the whole configuration and reload it, or put the files back.

        Args:
            file: The exposure's file, already written.
            loader_written: This call wrote the loader.

        Raises:
            ValidationError: nginx refused; its output is the error, verbatim.
        """
        from noust.managers.nginx_manager import NginxManager

        nginx = NginxManager(runner=self._runner, fs=self._fs)
        errors = nginx.config_errors()
        if errors is not None:
            self.fs.remove(file, missing_ok=True)
            if loader_written:
                self.fs.remove(self.conf_root / "modules-enabled" / LOADER_NAME, missing_ok=True)
            raise ValidationError(
                "nginx refused the configuration for this port, so nothing was changed",
                details="Fix what nginx names, then run the command again.",
                output=errors,
            )
        if not nginx.reload():
            self.fs.remove(file, missing_ok=True)
            if loader_written:
                self.fs.remove(self.conf_root / "modules-enabled" / LOADER_NAME, missing_ok=True)
            nginx.reload()
            raise ValidationError(
                "nginx did not reload, so the port was not published",
                details="Look at: systemctl status nginx",
            )

    def _open_firewall(self, exposure: StreamExposure) -> dict[str, Any]:
        """
        Allow the port in the firewall for the sources, and remember what was made.

        Args:
            exposure: The exposure.

        Returns:
            What the firewall came to: ``backend``, ``active``, ``made`` (the rules this
            call added, which are the only ones closed later) and a ``note``.

        Raises:
            SecurityError: The firewall refused a rule; the ones made are removed again.
        """
        firewall = self._firewall(self.runner)
        recorded = self.records.get(exposure.name)
        # What an earlier try made is still ours to close: the rule now reads as "already
        # there", and would otherwise be left behind.
        made: list[dict[str, Any]] = list((recorded.firewall if recorded else {}).get("made") or [])
        backend, active, note = "none", False, ""
        for source in exposure.sources or ("any",):
            request = RuleRequest(
                "allow",
                exposure.listen_port,
                exposure.protocol,
                source,
                f"{_FIREWALL_COMMENT} {exposure.name}",
            )
            change = firewall.allow_direct(request)
            backend, active = change.backend, change.active
            if change.changed:
                made.append(
                    {"port": exposure.listen_port, "proto": exposure.protocol, "source": source}
                )
                # Before the next rule, so a crash here still knows what to close.
                self.records.set_state(
                    exposure.name,
                    OPENING,
                    {"backend": backend, "active": active, "made": made, "note": note},
                )
            elif change.detail:
                note = change.detail
        record = {"backend": backend, "active": active, "made": made, "note": note}
        self.records.set_state(exposure.name, OPENING, record)
        return record

    # ------------------------------------------------------------------ closing

    def close(self, name: str, *, actor: str | None = None) -> StreamResult:
        """
        Take a published port down: the firewall rules Noust made, then the file.

        Asking again for one that stopped half way finishes it.

        Args:
            name: The exposure.
            actor: Who is asking.

        Returns:
            What was closed.

        Raises:
            NoustError: There is no such exposure.
            SecurityError: The firewall refused to remove a rule; the exposure stays
                recorded as closing so the same command finishes it.
        """
        require_server_role("Published ports")
        exposure = self.get(name)
        if is_rehearsal():
            return StreamResult(exposure, (), "", rehearsal=True)
        self.records.set_state(name, CLOSING)
        self._take_down(exposure, exposure.firewall)
        self.records.delete(name)
        audit.record(
            "streams.close",
            target=f"stream:{name}",
            details={
                "protocol": exposure.protocol,
                "listen_port": exposure.listen_port,
                "actor": actor,
            },
        )
        return StreamResult(exposure, (), "")

    def _abort(self, exposure: StreamExposure) -> None:
        """
        Put back everything an opening that failed had made.

        Args:
            exposure: The exposure that was being opened.

        The record stays, as closing, when the firewall refuses to take a rule away:
        deleting it would leave the rule with nothing that knows it is Noust's, and
        ``noust stream remove`` finishes the job from the row.
        """
        recorded = self.records.get(exposure.name)
        self.records.set_state(exposure.name, CLOSING)
        try:
            self._take_down(exposure, recorded.firewall if recorded else {})
        except SecurityError as exc:
            logger.warning(
                "The firewall rules of %s could not be removed (%s); finish with: "
                "noust stream remove %s",
                exposure.name,
                exc,
                exposure.name,
            )
            return
        self.records.delete(exposure.name)

    def _take_down(self, exposure: StreamExposure, firewall: dict[str, Any]) -> None:
        """
        Remove what was made for an exposure: its rules, its file, and nginx's use of it.

        Args:
            exposure: The exposure.
            firewall: What the firewall was asked for, as recorded.

        Raises:
            SecurityError: The firewall refused to remove a rule.
        """
        made = list(firewall.get("made") or [])
        if made:
            self._remove_rules(self._firewall(self.runner), made, exposure)
        file = self.file_for(exposure.name)
        present = file.exists() if not is_rehearsal() else False
        self.fs.remove(file, missing_ok=True)
        if present:
            from noust.managers.nginx_manager import NginxManager

            nginx = NginxManager(runner=self._runner, fs=self._fs)
            if not nginx.reload():
                logger.warning("nginx did not reload after %s was closed", exposure.name)

    def _remove_rules(
        self,
        firewall: Firewall,
        made: Sequence[dict[str, Any]],
        exposure: StreamExposure | None = None,
    ) -> None:
        """
        Remove the firewall rules this module made, and only those.

        Args:
            firewall: The firewall manager.
            made: The rules, as recorded.
            exposure: The exposure, for the comment the rules carry.

        Raises:
            SecurityError: A removal was refused.
        """
        for rule in reversed(list(made)):
            firewall.remove_direct(
                RuleRequest(
                    "allow",
                    int(rule["port"]),
                    str(rule["proto"]),
                    str(rule.get("source") or "any"),
                    f"{_FIREWALL_COMMENT} {exposure.name}" if exposure else _FIREWALL_COMMENT,
                )
            )

    # ------------------------------------------------------------------ repairing

    def repair(self) -> list[str]:
        """
        Finish the openings and closings that were interrupted.

        An exposure left ``opening`` never finished and is closed again; one left
        ``closing`` is finished.

        Returns:
            The names that were put right.
        """
        require_server_role("Published ports")
        done: list[str] = []
        for exposure in self.records.all():
            if exposure.state == OPEN:
                continue
            self.close(exposure.name)
            done.append(exposure.name)
        return done


#: Ports of services that commonly have no authentication of their own.
_AUTH_FREE_PORTS = frozenset({6379, 11211, 27017, 9200, 5984, 2375})


def _loads_stream_module(tree: Tree) -> bool:
    """
    Args:
        tree: The parsed ``nginx -T`` output.

    Returns:
        Whether a ``load_module`` names the stream module.
    """
    return any(
        isinstance(node, Directive)
        and node.name.value == "load_module"
        and node.args
        and node.args[0].word.value.endswith("ngx_stream_module.so")
        for node in tree.children
    )


def _firewall_sentence(firewall: dict[str, Any]) -> str:
    """
    Args:
        firewall: What the firewall came to, as recorded.

    Returns:
        A sentence for the operator.
    """
    made = firewall.get("made") or []
    if made:
        return f"{firewall.get('backend')} now allows {len(made)} rule(s) for it"
    note = str(firewall.get("note") or "")
    if note:
        return f"The firewall was not changed: {note}"
    return "The firewall was not changed"
