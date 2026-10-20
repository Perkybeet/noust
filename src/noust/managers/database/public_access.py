# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Making a database reachable from the network, on purpose, and making it private again.

A database is private: its engine listens on the loopback and an operator
reaches it through an SSH tunnel (``noust db connect-info``). Some people
connect a desktop client from their own computer on the first day, and for
them there is a deliberate, reversible action: *make this database public*.
Nothing here is implicit and nothing is permanent.

**What it does**, in this order, each step recorded so it can be undone:

1. A *proxy* is opened on the public port you choose: a systemd socket and a
   service running ``systemd-socket-proxyd``, forwarding that port to the
   engine's own loopback port. The engine is not reconfigured and not
   restarted, nothing already connected notices, and it keeps listening on
   the loopback only: the way back is removing two unit files.
2. The database gets an *account of its own* for this, with the access you
   chose (owner, read-write or read-only) on that database alone. The
   applications' accounts and passwords are never the ones given out, so
   closing the door never means rotating an application's password.
3. Where the engine can, that account is made to *require TLS*: PostgreSQL
   refuses it without (one marked line in ``pg_hba.conf``), MySQL and MariaDB
   refuse it without (``REQUIRE SSL`` on the account). The others cannot do it
   for one account, so TLS is optional for them only when you say so.
4. The *firewall* is told to allow the port, from the addresses you list or
   from anyone, through the one firewall manager (ufw or firewalld).

**What it refuses**, whatever you confirm: the ENS profile (an engine is
reached through a tunnel there), an engine in a container Noust did not make (its
compose file publishes its ports), an engine that lets a client in without a
password (Redis without one, MongoDB without authorization), a port that is
taken, one below 1024 or the engine's own.

An **instance Noust runs** is the one container this works for: it is published
on 127.0.0.1 and nothing else, by Noust, so the proxy forwards to that port and
the instance stays private. TLS cannot be required of an account in it (the
image's own configuration decides), so a public database on an instance is
plaintext unless the engine's server already has TLS (MySQL's image makes its
own certificate), and the plan says so.

**Undoing it** (:meth:`PublicAccess.make_private`) removes what the record
says was made and nothing else: the firewall rule only if Noust added it, the
units, the TLS line, the account. The record is written before the first
change and says how far it got, so an interrupted action is finished from it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, TemplateError

from noust.central import require_server_role
from noust.core import paths
from noust.core.config import SYSTEMD_DIR as _SYSTEMD_DIR
from noust.core.database_exposures import (
    CLOSING,
    EXPOSED,
    TLS_OPTIONAL,
    TLS_REQUIRED,
    Exposure,
    ExposureStore,
)
from noust.core.ens import profile as security_profile
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseEngineError,
    DatabaseError,
    DatabaseNotFoundError,
    NoustError,
    ServiceError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.managers.database.base import PROFILES, BaseDatabaseManager, is_loopback
from noust.managers.database.exposure import listening_sockets, public_address
from noust.managers.database.instances import engine_of, storage_name
from noust.managers.database.service import DatabaseService
from noust.managers.database.urls import MASK, connection_url
from noust.managers.port_claims import PortClaims
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe

logger = logging.getLogger(__name__)

#: Where the proxy units are written. A module attribute so a test points it elsewhere.
SYSTEMD_DIR = _SYSTEMD_DIR

#: Where ``systemd-socket-proxyd`` is, by distribution layout.
PROXY_CANDIDATES: tuple[Path, ...] = (
    Path("/usr/lib/systemd/systemd-socket-proxyd"),
    Path("/lib/systemd/systemd-socket-proxyd"),
)

#: The lowest public port: below it a port is a privileged service's.
MIN_PORT = 1024
MAX_PORT = 65535

#: How many connections the proxy keeps open at once (systemd's own default).
CONNECTIONS_MAX = 256

#: How many sources may be listed.
MAX_SOURCES = 20

#: What systemctl may take.
SYSTEMCTL_TIMEOUT = 60

#: What an account may be given on the database, most to least.
DEFAULT_PROFILE = "owner"

#: The query option that makes a client of each engine use TLS.
TLS_CLIENT_OPTIONS: dict[str, dict[str, str]] = {
    "postgresql": {"sslmode": "require"},
    "mysql": {"ssl-mode": "REQUIRED"},
    # Encrypted, nothing checked: the certificate is Noust's authority's, which a client verifies
    # with the CA file the Connect tab hands out.
    "redis": {"ssl_cert_reqs": "none"},
}

_UNIT_MODE = 0o644


@dataclass(frozen=True)
class PublicPlan:
    """
    What making a database public would do, decided before anything is touched.

    Attributes:
        engine: The engine.
        database: The database.
        port: The public port.
        bind_address: Where the proxy listens.
        sources: Who may reach it: CIDRs, or ``any``.
        tls: ``required`` or ``optional``.
        profile: The access of the account made for it.
        remote_user: The account's name.
        unit: The name of the socket and service units.
        target: Where the proxy forwards to.
        firewall: ``backend``, ``active`` and what would be done.
        warnings: What the operator accepts by confirming.
    """

    engine: str
    database: str
    port: int
    bind_address: str
    sources: tuple[str, ...]
    tls: str
    profile: str
    remote_user: str
    unit: str
    target: str
    firewall: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "database": self.database,
            "port": self.port,
            "bind_address": self.bind_address,
            "sources": list(self.sources),
            "tls": self.tls,
            "profile": self.profile,
            "remote_user": self.remote_user,
            "unit": self.unit,
            "target": self.target,
            "firewall": self.firewall,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PublicOutcome:
    """
    A database made public.

    Attributes:
        exposure: The record.
        password: The account's password, shown this once and kept as a secret
            (sudo mode reveals it again).
        connection: How to connect, with the password.
        warnings: What the operator should know, once more.
    """

    exposure: Exposure
    password: str
    connection: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready; the password is in ``connection``.
        """
        return {
            "exposure": self.exposure.to_dict(),
            "connection": self.connection,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PrivateOutcome:
    """
    A database made private again.

    Attributes:
        engine: The engine.
        database: The database.
        removed: What was taken away, in the order it was.
        already_private: The database was not public.
    """

    engine: str
    database: str
    removed: list[str] = field(default_factory=list)
    already_private: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "database": self.database,
            "removed": list(self.removed),
            "already_private": self.already_private,
        }


def _templates() -> Environment:
    """
    Load the systemd templates.

    Returns:
        A Jinja environment over ``templates/systemd``.
    """
    return Environment(
        loader=PackageLoader("noust", "templates/systemd"),
        trim_blocks=True,
        lstrip_blocks=True,
        # Systemd units are not markup: HTML escaping would corrupt them.
        autoescape=False,  # noqa: S701 - systemd unit files, not markup
    )


def listen_spec(address: str, port: int) -> str:
    """
    Spell a socket unit's ``ListenStream``.

    Args:
        address: ``0.0.0.0``, ``::`` or an address.
        port: The port.

    Returns:
        ``0.0.0.0:5433`` or ``[::]:5433``.
    """
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def remote_user_name(manager: BaseDatabaseManager, database: str) -> str:
    """
    Name the account made for a database's public access.

    ``<database>_remote``, shortened with a hash of the whole name where the
    engine's limit (MySQL's is 32) would cut it.

    Args:
        manager: The engine's manager.
        database: The database.

    Returns:
        The account's name, valid for the engine.
    """
    base = re.sub(r"[^A-Za-z0-9_]", "_", database)
    if not base or base[0].isdigit():
        base = f"db_{base}"
    suffix = "_remote"
    name = f"{base}{suffix}"
    limit = manager.MAX_USER_NAME_LENGTH
    if len(name) > limit:
        digest = hashlib.sha256(database.encode()).hexdigest()[:6]
        name = f"{base[: limit - len(suffix) - 7]}_{digest}{suffix}"
    return manager.validate_user_name(name)


class PublicAccess:
    """
    Make a database public and private again; the one implementation, for the CLI and the API.

    Args:
        service: The database service, which resolves engines and makes accounts.
        runner: The runner systemctl and ss go through; the process-wide one by default.
        fs: The filesystem the unit files are written through.
        exposures: The table of what is public.
        firewall: Builds the firewall manager; ufw and firewalld, by default.
    """

    def __init__(
        self,
        service: DatabaseService,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        exposures: ExposureStore | None = None,
        firewall: Callable[[], Firewall] | None = None,
    ) -> None:
        self.service = service
        self._runner = runner
        self._fs = fs
        self.exposures = exposures or ExposureStore(service.store)
        self._firewall = firewall

    # ------------------------------------------------------------ plumbing

    @property
    def runner(self) -> CommandRunner:
        """The runner systemctl and ss go through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem the unit files are written through."""
        return self._fs or get_fs()

    def firewall(self) -> Firewall:
        """
        Build the firewall manager.

        Returns:
            One over this runner, with no confirmation ledger: only the
            direct allow and remove paths are used here.
        """
        if self._firewall is not None:
            return self._firewall()
        return Firewall(SecurityProbe(runner=self.runner), None, actor="noust db public")

    def _unit_path(self, unit: str, suffix: str) -> Path:
        """
        Where one of a public port's units is.

        Args:
            unit: The unit's name.
            suffix: ``socket`` or ``service``.

        Returns:
            The path under :data:`SYSTEMD_DIR`.
        """
        return SYSTEMD_DIR / f"{unit}.{suffix}"

    def _systemctl(self, *args: str) -> Any:
        """
        Run a systemctl verb.

        Args:
            *args: What follows ``systemctl``.

        Returns:
            The outcome.
        """
        return self.runner.run(["systemctl", *args], timeout=SYSTEMCTL_TIMEOUT)

    # ------------------------------------------------------------- choosing

    def _reserved_ports(self) -> set[int]:
        """
        List the ports a public port may not be.

        Returns:
            Every port the registry says something holds, and the engines' own.
        """
        # The registry knows who holds a port (E-44): what listens, SSH and the console, the
        # applications and the Compose stacks, the published streams, and the other exposures.
        reserved = PortClaims(self.runner, self.exposures.store).taken_ports()
        for manager in self.service.host_managers():
            reserved.add(manager.DEFAULT_PORT)
            if manager.is_installed():
                reserved.add(manager.server_port())
        return reserved

    def suggest_port(self, engine: str, reserved: set[int] | None = None) -> int:
        """
        Suggest a free public port: the engine's own, plus one, then the next free.

        Args:
            engine: The engine.
            reserved: The ports that are not free, when the caller has them.

        Returns:
            A port that nothing listens on and no exposure holds.

        Raises:
            ValidationError: When none of the next fifty is free.
        """
        manager = self.service.manager(engine)
        reserved = self._reserved_ports() if reserved is None else reserved
        start = manager.DEFAULT_PORT + 1
        for port in range(start, min(start + 50, MAX_PORT)):
            if port not in reserved:
                return port
        raise ValidationError(
            f"No free port after {manager.DEFAULT_PORT}",
            details="Choose one with --port.",
            field="port",
        )

    def _check_port(self, port: int | None, manager: BaseDatabaseManager) -> int:
        """
        Choose and check the public port.

        Args:
            port: What was asked for, or None for a suggestion.
            manager: The engine's manager.

        Returns:
            The port.

        Raises:
            ValidationError: When it is out of range, or taken.
        """
        reserved = self._reserved_ports()
        if port is None:
            return self.suggest_port(manager.ENGINE_NAME, reserved)
        if isinstance(port, bool) or not isinstance(port, int) or not MIN_PORT <= port <= MAX_PORT:
            raise ValidationError(
                f"{port!r} is not a public port",
                details=f"A port from {MIN_PORT} to {MAX_PORT}: below it ports belong to services.",
                field="port",
            )
        held = self.exposures.on_port(port)
        if held is not None:
            raise ValidationError(
                f"Port {port} is already the public port of {held.engine}/{held.db_name}",
                details="Choose another with --port.",
                field="port",
            )
        if port in reserved:
            raise ValidationError(
                f"Port {port} is taken on this server",
                details="Something listens on it already (the engine's own port, SSH, the "
                f"console or another service). Suggestion: "
                f"{self.suggest_port(manager.ENGINE_NAME, reserved)}.",
                field="port",
            )
        return port

    @staticmethod
    def _check_bind(bind: str | None) -> str:
        """
        Check where the proxy listens.

        Args:
            bind: An address, or None for every IPv4 address.

        Returns:
            The address, normalised.

        Raises:
            ValidationError: When it is not an address, or is the loopback,
                which would be no exposure at all.
        """
        text = (bind or "0.0.0.0").strip()  # noqa: S104 - the public default, on purpose
        try:
            address = ipaddress.ip_address(text)
        except ValueError as exc:
            raise ValidationError(
                f"{bind!r} is not an address to listen on",
                details="Give 0.0.0.0 (every IPv4 address), :: (every address) or one address "
                "of this server.",
                field="bind_address",
            ) from exc
        if address.is_loopback or is_loopback(text):
            raise ValidationError(
                "The loopback is where the engine already is",
                details="That is not making it public. Give 0.0.0.0, :: or an address of this "
                "server; an SSH tunnel reaches the loopback ('noust db connect-info').",
                field="bind_address",
            )
        if address.is_multicast:
            raise ValidationError(f"{text} is a multicast address", field="bind_address")
        return str(address)

    @staticmethod
    def _check_sources(sources: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
        """
        Check who may reach the port.

        Args:
            sources: Addresses or CIDRs, or ``any``; None or empty for anyone.

        Returns:
            The sources, normalised and without repeats; ``("any",)`` for anyone.

        Raises:
            ValidationError: When one is not an address or a network, or too
                many are listed.
        """
        wanted = [str(item).strip() for item in (sources or []) if str(item).strip()]
        if not wanted or "any" in wanted:
            return ("any",)
        if len(wanted) > MAX_SOURCES:
            raise ValidationError(
                f"At most {MAX_SOURCES} sources can be listed, not {len(wanted)}",
                field="sources",
            )
        normalised: list[str] = []
        for item in wanted:
            try:
                network = str(ipaddress.ip_network(item, strict=False))
            except ValueError as exc:
                raise ValidationError(
                    f"{item!r} is not an address or a network",
                    details="Give an address or a CIDR such as 203.0.113.7 or 203.0.113.0/24.",
                    field="sources",
                ) from exc
            if network in ("0.0.0.0/0", "::/0"):
                return ("any",)
            if network not in normalised:
                normalised.append(network)
        return tuple(normalised)

    def _proxy_binary(self) -> Path:
        """
        Find ``systemd-socket-proxyd``.

        Returns:
            Its path.

        Raises:
            ValidationError: When this system has none.
        """
        for candidate in PROXY_CANDIDATES:
            if candidate.is_file():
                return candidate
        raise ValidationError(
            "systemd-socket-proxyd is not installed",
            details="It is part of systemd (the 'systemd' package on Debian and Ubuntu, "
            "'systemd' on RPM systems); the public port is a proxy that runs it.",
        )

    # ------------------------------------------------------------- planning

    def plan(
        self,
        engine: str,
        database: str,
        *,
        port: int | None = None,
        bind_address: str | None = None,
        sources: list[str] | tuple[str, ...] | None = None,
        tls: str | None = None,
        profile: str = DEFAULT_PROFILE,
    ) -> PublicPlan:
        """
        Decide what making a database public would do, changing nothing.

        Args:
            engine: The engine.
            database: The database (a slot number for Redis).
            port: The public port; a free one is suggested when None.
            bind_address: Where to listen; every IPv4 address when None.
            sources: Who may reach it; anyone when empty.
            tls: ``required`` (the default where the engine can) or ``optional``.
            profile: The access of the account made for it.

        Returns:
            The plan, with what the operator accepts by confirming.

        Raises:
            ValidationError: Something asked for cannot be had; nothing was
                changed. Under the ENS profile it is always refused.
            DatabaseEngineError: The engine is not installed, not running, or is
                in a container.
            DatabaseExistsError: The database is already public.
        """
        require_server_role("Public access to a database")
        if not security_profile.database_remote_listen_allowed():
            raise ValidationError(
                "The ENS profile does not let a database be reached from the network",
                details="Under security.profile ens-medium engines are reached through an SSH "
                "tunnel: 'noust db connect-info' prints one.",
            )
        manager = self.service.running(engine)
        if manager.instance is not None and getattr(manager.instance, "managed", None) is None:
            raise ValidationError(
                "A database container's ports are its compose file's",
                details="Noust does not open a container's ports. Publish the port in the "
                "compose file, on the interface you mean.",
                field="engine",
            )
        name = manager.ENGINE_NAME
        db = manager.validate_database_name(database)
        if not manager.database_exists(db):
            raise DatabaseNotFoundError(
                f"Database '{db}' does not exist on {manager.DISPLAY_NAME}",
                details=f"List them with: noust db list --engine {name}",
            )
        if self.exposures.get(name, db) is not None:
            raise ValidationError(
                f"{name}/{db} is already reachable from the network",
                details=f"See it with 'noust db public show {db} -e {name}', or make it private first.",
            )
        blockers = manager.remote_access_blockers()
        if blockers:
            raise ValidationError(
                f"{manager.DISPLAY_NAME} cannot be opened to the network as it is",
                details=" ".join(blockers),
                field="engine",
            )
        if profile not in PROFILES:
            raise ValidationError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
                field="profile",
            )
        chosen_port = self._check_port(port, manager)
        bind = self._check_bind(bind_address)
        allowed = self._check_sources(sources)
        mode = self._check_tls(manager, tls)
        self._proxy_binary()
        user = remote_user_name(manager, db)
        if manager.user_exists(user):
            raise ValidationError(
                f"The account {user} exists already",
                details="It may be left from an earlier attempt: drop it with "
                f"'noust db user-delete {user} --engine {name}', or use another database name.",
            )
        unit = f"{paths.DB_EXPOSE_UNIT_PREFIX}{storage_name(name)}-{chosen_port}"
        self._refuse_foreign_units(unit)
        firewall = self._firewall_view(allowed, chosen_port)
        warnings = self._warnings(manager, db, chosen_port, bind, allowed, mode, profile, firewall)
        return PublicPlan(
            engine=name,
            database=db,
            port=chosen_port,
            bind_address=bind,
            sources=allowed,
            tls=mode,
            profile=profile,
            remote_user=user,
            unit=unit,
            target=f"127.0.0.1:{manager.server_port()}",
            firewall=firewall,
            warnings=warnings,
        )

    def _check_tls(self, manager: BaseDatabaseManager, tls: str | None) -> str:
        """
        Decide whether the account is made to require TLS.

        Args:
            manager: The engine's manager.
            tls: ``required``, ``optional`` or None for required where it can.

        Returns:
            ``required`` or ``optional``.

        Raises:
            ValidationError: When TLS is required and the engine cannot do it.
        """
        wanted = (tls or "").strip().lower() or None
        if wanted not in (None, TLS_REQUIRED, TLS_OPTIONAL):
            raise ValidationError(
                f"{tls!r} is not a TLS setting",
                details=f"Use {TLS_REQUIRED} or {TLS_OPTIONAL}.",
                field="tls",
            )
        if wanted == TLS_OPTIONAL:
            return TLS_OPTIONAL
        blocker = manager.remote_tls_blocker()
        if blocker is None:
            return TLS_REQUIRED
        raise ValidationError(
            blocker,
            details="Public access is TLS-only unless you say otherwise: pass tls=optional "
            "(--allow-plaintext) to accept that the password and the data cross the network "
            "in clear, or reach the database through an SSH tunnel.",
            field="tls",
        )

    def _refuse_foreign_units(self, unit: str) -> None:
        """
        Refuse to overwrite a unit of that name that Noust did not write.

        Args:
            unit: The unit's name.

        Raises:
            ServiceError: When one is there without Noust's marker.
        """
        for suffix in ("socket", "service"):
            path = self._unit_path(unit, suffix)
            if path.exists():
                try:
                    ours = paths.carries_unit_marker(path.read_text(encoding="utf-8"))
                except OSError as exc:
                    raise ServiceError(f"Cannot read {path}", details=str(exc)) from exc
                if not ours:
                    raise ServiceError(
                        f"Refusing to overwrite {path}: Noust did not write it",
                        details="Remove or rename that unit, or choose another port.",
                    )

    def _firewall_view(self, sources: tuple[str, ...], port: int) -> dict[str, Any]:
        """
        Say what the firewall would be told, and whether it filters anything.

        Args:
            sources: Who may reach the port.
            port: The port.

        Returns:
            ``backend``, ``active``, ``manages`` (Noust changes it) and ``sources``.
        """
        state = self.firewall().state()
        return {
            "backend": state.backend,
            "active": state.active,
            "manages": state.backend in ("ufw", "firewalld") and state.installed,
            "sources": list(sources),
            "port": port,
        }

    def _warnings(
        self,
        manager: BaseDatabaseManager,
        database: str,
        port: int,
        bind: str,
        sources: tuple[str, ...],
        tls: str,
        profile: str,
        firewall: dict[str, Any],
    ) -> list[str]:
        """
        Write what the operator accepts by making a database public.

        Args:
            manager: The engine's manager.
            database: The database.
            port: The public port.
            bind: Where it listens.
            sources: Who may reach it.
            tls: ``required`` or ``optional``.
            profile: The account's access.
            firewall: What the firewall would be told.

        Returns:
            One sentence each, the most important first.
        """
        shown = "every address" if bind in ("0.0.0.0", "::") else bind  # noqa: S104
        who = "anyone" if sources == ("any",) else ", ".join(sources)
        warnings = [
            f"{manager.DISPLAY_NAME} database {database} becomes reachable on port {port} "
            f"({shown}) by {who}: whoever can reach the port can try the account's password, "
            "and Noust sets no limit on attempts."
        ]
        if sources == ("any",):
            warnings.append(
                "Nothing limits who connects: list the addresses that need to (--from) so "
                "the firewall lets only them in."
            )
        if not firewall["manages"]:
            warnings.append(
                "No firewall that Noust changes (ufw or firewalld) is in use, so nothing here "
                f"limits who reaches port {port}: only your network's own firewall does."
            )
        elif not firewall["active"]:
            warnings.append(
                f"{firewall['backend']} is installed but not active, so every port of this "
                "server is open to everyone whatever rule is added."
            )
        if tls == TLS_OPTIONAL:
            warnings.append(
                "TLS is optional: the password and the data cross the network in clear for any "
                "client that does not ask for TLS."
            )
        if profile == "owner":
            warnings.append(
                "The account owns the database: it can create, change and drop everything in "
                "it. Use read_write or read_only if that is more than needed."
            )
        if manager.engine_type == "redis":
            warnings.append(
                "A Redis account cannot be limited to one slot: it reaches every key of this Redis."
            )
        warnings.append(
            "The engine itself stays private; a proxy forwards the port to it, so the engine's "
            "own logs and rules see these connections as coming from 127.0.0.1. Close it "
            f"again with: noust db public close {database} --engine {manager.ENGINE_NAME}."
        )
        return warnings

    # -------------------------------------------------------------- opening

    def expose(
        self,
        engine: str,
        database: str,
        *,
        port: int | None = None,
        bind_address: str | None = None,
        sources: list[str] | tuple[str, ...] | None = None,
        tls: str | None = None,
        profile: str = DEFAULT_PROFILE,
        confirm: bool = False,
    ) -> PublicOutcome:
        """
        Make a database reachable from the network, on purpose.

        Args:
            engine: The engine.
            database: The database.
            port: The public port; a free one when None.
            bind_address: Where to listen; every IPv4 address when None.
            sources: Who may reach it; anyone when empty.
            tls: ``required`` (the default where the engine can) or ``optional``.
            profile: The access of the account made for it.
            confirm: The operator accepted every warning of the plan.

        Returns:
            The outcome, with the account's password, shown once.

        Raises:
            ConfirmationRequired: Not confirmed; nothing was changed. The
                warnings are the ones to show.
            ValidationError: See :meth:`plan`.
            DatabaseError: A step failed; every earlier one was undone.
        """
        decided = self.plan(
            engine,
            database,
            port=port,
            bind_address=bind_address,
            sources=sources,
            tls=tls,
            profile=profile,
        )
        if not confirm:
            raise ConfirmationRequired(decided.warnings)
        manager = self.service.running(engine)
        actor = self.service.actor.label if self.service.actor else None
        record = self.exposures.create(
            engine=decided.engine,
            db_name=decided.database,
            port=decided.port,
            bind_address=decided.bind_address,
            sources=decided.sources,
            tls=decided.tls,
            remote_user=decided.remote_user,
            profile=decided.profile,
            unit=decided.unit,
            created_by=actor,
        )
        committed = False
        password = ""
        try:
            _user, password = self.service.create_user(
                decided.engine,
                decided.remote_user,
                host="localhost",
                database=decided.database,
                profile=decided.profile,
            )
            if decided.tls == TLS_REQUIRED:
                manager.set_remote_tls(decided.remote_user, decided.database, True)
            self._open_proxy(record, manager)
            self._open_firewall(record)
            self.exposures.set_state(decided.engine, decided.database, EXPOSED)
            committed = True
        finally:
            if not committed:
                self._undo_partial(record)
        self.service.audit(
            "db.expose",
            f"{decided.engine}/{decided.database}",
            port=decided.port,
            bind_address=decided.bind_address,
            sources=list(decided.sources),
            tls=decided.tls,
            profile=decided.profile,
            remote_user=decided.remote_user,
        )
        stored = self.exposures.get(decided.engine, decided.database) or record
        return PublicOutcome(
            exposure=stored,
            password=password,
            connection=self.connection(stored, password),
            warnings=decided.warnings,
        )

    def _render(self, name: str, **values: Any) -> str:
        """
        Render one of the proxy's unit templates.

        Args:
            name: Template file name.
            **values: What it interpolates.

        Returns:
            The unit file.

        Raises:
            ServiceError: The template is missing or broken.
        """
        try:
            return _templates().get_template(name).render(**values)
        except TemplateError as exc:
            raise ServiceError(
                f"Failed to render systemd template: {name}",
                details=f"{exc}. The Noust installation may be incomplete.",
            ) from exc

    def _open_proxy(self, record: Exposure, manager: BaseDatabaseManager) -> None:
        """
        Write the proxy's units and start its socket.

        Args:
            record: The exposure being opened.
            manager: The engine's manager.

        Raises:
            ServiceError: A unit cannot be written, the socket does not start,
                or nothing listens on the port afterwards.
        """
        values = {
            "engine": record.engine,
            "database": record.db_name,
            "port": record.port,
            "unit": record.unit,
            "engine_unit": (
                getattr(manager.instance, "unit", None) or manager.service_unit()
                if manager.instance is not None
                else manager.service_unit()
            ),
            "listen": listen_spec(record.bind_address, record.port),
            "proxy": str(self._proxy_binary()),
            "connections_max": CONNECTIONS_MAX,
            "target": f"127.0.0.1:{manager.server_port()}",
        }
        files = {
            self._unit_path(record.unit, "socket"): self._render("db-public.socket.j2", **values),
            self._unit_path(record.unit, "service"): self._render("db-public.service.j2", **values),
        }
        for path, content in files.items():
            try:
                self.fs.write_text(path, content, mode=_UNIT_MODE)
            except OSError as exc:
                raise ServiceError(
                    f"Failed to write unit file: {path}",
                    details=f"{exc}. Noust must run as root to manage systemd units.",
                ) from exc
        self._systemctl("daemon-reload")
        started = self._systemctl("enable", "--now", f"{record.unit}.socket")
        if not started.success:
            raise ServiceError(
                f"The socket on port {record.port} did not start",
                details=(started.stderr or started.stdout).strip()
                or f"See: systemctl status {record.unit}.socket",
            )
        if not is_rehearsal() and record.port not in {
            socket.port for socket in listening_sockets(self.runner)
        }:
            raise ServiceError(
                f"Nothing listens on port {record.port} after the socket started",
                details=f"See: systemctl status {record.unit}.socket and journalctl -u "
                f"{record.unit}.socket.",
            )

    def _rules(self, record: Exposure) -> list[RuleRequest]:
        """
        Build the firewall rules an exposure asks for, one per source.

        Args:
            record: The exposure.

        Returns:
            Allow rules for the port over TCP.
        """
        comment = f"db {record.engine} {record.db_name} {record.port}"
        return [
            RuleRequest("allow", record.port, "tcp", source, comment) for source in record.sources
        ]

    def _open_firewall(self, record: Exposure) -> None:
        """
        Allow the port, from each source, and record exactly what was added.

        Args:
            record: The exposure being opened.

        Raises:
            SecurityError: A rule was refused (the earlier ones are undone by
                :meth:`_undo_partial` through the record).
        """
        firewall = self.firewall()
        added: list[dict[str, Any]] = []
        backend = ""
        active = False
        for rule in self._rules(record):
            change = firewall.allow_direct(rule)
            backend, active = change.backend, change.active
            added.append({"source": rule.source, "created": change.changed})
            self.exposures.set_firewall(
                record.engine,
                record.db_name,
                {"backend": backend, "active": active, "rules": added},
            )
        if not added:  # pragma: no cover - sources is never empty
            self.exposures.set_firewall(
                record.engine, record.db_name, {"backend": backend, "active": active, "rules": []}
            )

    # -------------------------------------------------------------- closing

    def make_private(self, engine: str, database: str) -> PrivateOutcome:
        """
        Make a database private again: undo everything making it public did.

        Removes what the record says was made and nothing else; a step that
        cannot be done now (the engine is stopped) leaves the record
        ``closing`` and is named, so running this again finishes it.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            What was removed; ``already_private`` when there was nothing to undo.

        Raises:
            DatabaseError: A step failed; the record stays, saying so.
        """
        manager = self.service.manager(engine)
        db = manager.validate_database_name(database)
        record = self.exposures.get(manager.ENGINE_NAME, db)
        if record is None:
            return PrivateOutcome(manager.ENGINE_NAME, db, already_private=True)
        self.exposures.set_state(record.engine, record.db_name, CLOSING)
        removed, failures = self._take_down(record, manager)
        if failures:
            raise DatabaseEngineError(
                f"{record.engine}/{record.db_name} is closed to the network, but not all of it "
                "was undone",
                details="; ".join(failures)
                + f". Run it again once that is fixed: noust db public close {db} "
                f"--engine {record.engine}",
            )
        self.exposures.delete(record.engine, record.db_name)
        self.service.audit(
            "db.unexpose",
            f"{record.engine}/{record.db_name}",
            port=record.port,
            removed=removed,
        )
        return PrivateOutcome(record.engine, record.db_name, removed=removed)

    def _take_down(
        self, record: Exposure, manager: BaseDatabaseManager | None
    ) -> tuple[list[str], list[str]]:
        """
        Undo what an exposure made, each step on its own.

        Order: the firewall (closes the door first), the proxy, the TLS line,
        the account. A step that fails is reported and the rest still run.

        Args:
            record: The exposure.
            manager: The engine's manager, or None when it cannot be reached.

        Returns:
            What was removed, and what failed.
        """
        removed: list[str] = []
        failures: list[str] = []
        steps: list[tuple[str, Callable[[], bool]]] = [
            ("firewall", lambda: self._close_firewall(record)),
            ("proxy", lambda: self._close_proxy(record)),
            ("tls", lambda: self._lift_tls(record, manager)),
            ("account", lambda: self._drop_account(record, manager)),
        ]
        for name, step in steps:
            try:
                if step():
                    removed.append(name)
            except NoustError as exc:
                logger.warning(
                    "Could not undo %s of %s/%s: %s", name, record.engine, record.db_name, exc
                )
                failures.append(f"{name}: {exc.message}")
        return removed, failures

    def _undo_partial(self, record: Exposure) -> None:
        """
        Undo an opening that failed, and forget the record when nothing is left.

        Args:
            record: The exposure that was being opened.
        """
        try:
            manager: BaseDatabaseManager | None = self.service.manager(record.engine)
        except DatabaseError:
            manager = None
        _removed, failures = self._take_down(record, manager)
        if failures:
            # What could not be undone stays on record, closing, for 'close' to finish.
            self.exposures.set_state(record.engine, record.db_name, CLOSING)
            logger.error(
                "Opening %s/%s failed and could not be fully undone: %s",
                record.engine,
                record.db_name,
                "; ".join(failures),
            )
            return
        self.exposures.delete(record.engine, record.db_name)

    def _close_firewall(self, record: Exposure) -> bool:
        """
        Remove the firewall rules this exposure added, and only those.

        Args:
            record: The exposure.

        Returns:
            True when a rule was removed.
        """
        recorded = (record.firewall or {}).get("rules", [])
        created = {item["source"] for item in recorded if item.get("created")}
        if not created:
            return False
        firewall = self.firewall()
        changed = False
        for rule in self._rules(record):
            if rule.source in created and firewall.remove_direct(rule).changed:
                changed = True
        return changed

    def _close_proxy(self, record: Exposure) -> bool:
        """
        Stop the proxy and remove its units, the ones Noust wrote.

        Args:
            record: The exposure.

        Returns:
            True when something was removed.

        Raises:
            ServiceError: A unit is not Noust's, or cannot be removed.
        """
        present = [
            path
            for path in (
                self._unit_path(record.unit, "socket"),
                self._unit_path(record.unit, "service"),
            )
            if path.exists()
        ]
        for path in present:
            try:
                ours = paths.carries_unit_marker(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ServiceError(f"Cannot read {path}", details=str(exc)) from exc
            if not ours:
                raise ServiceError(
                    f"Refusing to remove {path}: Noust did not write it",
                    details="Remove it by hand; the port stays open until then.",
                )
        if not present:
            return False
        self._systemctl("disable", "--now", f"{record.unit}.socket")
        self._systemctl("stop", f"{record.unit}.service")
        for path in present:
            try:
                self.fs.remove(path)
            except OSError as exc:
                raise ServiceError(f"Failed to remove {path}", details=str(exc)) from exc
        self._systemctl("daemon-reload")
        return True

    def _lift_tls(self, record: Exposure, manager: BaseDatabaseManager | None) -> bool:
        """
        Remove the TLS requirement of the account (the ``pg_hba.conf`` line).

        Args:
            record: The exposure.
            manager: The engine's manager, or None.

        Returns:
            True when there was a requirement to lift.

        Raises:
            DatabaseError: The engine could not be reached.
        """
        if record.tls != TLS_REQUIRED:
            return False
        if manager is None or not manager.is_running():
            raise DatabaseEngineError(f"{record.engine} is not running")
        if not manager.user_exists(record.remote_user):
            return False
        manager.set_remote_tls(record.remote_user, record.db_name, False)
        return True

    def _drop_account(self, record: Exposure, manager: BaseDatabaseManager | None) -> bool:
        """
        Drop the account made for the exposure, when it still exists.

        Args:
            record: The exposure.
            manager: The engine's manager, or None.

        Returns:
            True when it was dropped.

        Raises:
            DatabaseError: The engine could not be reached or refused.
        """
        if manager is None or not manager.is_running():
            raise DatabaseEngineError(f"{record.engine} is not running")
        if not manager.user_exists(record.remote_user):
            return False
        self.service.drop_user(record.engine, record.remote_user)
        return True

    # -------------------------------------------------------------- reading

    def listing(self) -> list[Exposure]:
        """
        List every database that is reachable from the network.

        Returns:
            The exposures.
        """
        return self.exposures.all()

    def get(self, engine: str, database: str) -> Exposure | None:
        """
        Find one database's exposure.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            The exposure, or None when it is private.
        """
        manager = self.service.manager(engine)
        return self.exposures.get(manager.ENGINE_NAME, manager.validate_database_name(database))

    def listening(self) -> set[int]:
        """
        Name the ports something listens on now.

        Returns:
            The ports, to tell a live exposure from one whose proxy stopped.
        """
        return {socket.port for socket in listening_sockets(self.runner)}

    def connection(self, record: Exposure, password: str | None) -> dict[str, Any]:
        """
        Say how to connect to a public database.

        Args:
            record: The exposure.
            password: The account's password, or None to leave it masked.

        Returns:
            ``host``, ``port``, ``database``, ``username``, ``password``,
            ``tls``, ``url`` and ``options``.
        """
        host = (
            record.bind_address
            if record.bind_address not in ("0.0.0.0", "::")  # noqa: S104
            else (public_address(self.runner) or "<this server's address>")
        )
        options = (
            dict(TLS_CLIENT_OPTIONS.get(engine_of(record.engine), {}))
            if record.tls == TLS_REQUIRED
            else {}
        )
        if engine_of(record.engine) == "mongodb":
            options["authSource"] = record.db_name
        url = connection_url(
            record.engine,
            database=record.db_name,
            user=record.remote_user,
            password=password if password is not None else MASK,
            host=host,
            port=record.port,
            options=options or None,
            secure=record.tls == TLS_REQUIRED,
        )
        return {
            "host": host,
            "port": record.port,
            "database": record.db_name,
            "username": record.remote_user,
            "password": password,
            "tls": record.tls,
            "url": url,
            "options": options,
        }

    def reveal(self, engine: str, database: str) -> dict[str, Any]:
        """
        Show the connection details with the password, again.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            What :meth:`connection` gives, with the stored password.

        Raises:
            DatabaseNotFoundError: When the database is not public.
            DatabaseUserError: When Noust does not know the password.
        """
        record = self.get(engine, database)
        if record is None:
            raise DatabaseNotFoundError(
                f"{engine}/{database} is not reachable from the network",
                details="Make it public first: noust db public open.",
            )
        password = self.service.reveal_password(record.engine, record.remote_user)
        return self.connection(record, password)
