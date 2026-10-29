# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
This server's part in Noust DNS: installing the name server, making it a primary or a secondary,
and keeping its configuration in step with the store.

A **primary** holds the zones (:mod:`noust.managers.authdns.zones` edits them) and lists them in a
catalog it generates; a **secondary** copies from a primary every zone that catalog lists. The two
sign their transfers with one key. The primary makes it when it is set up and the secondary is
given it (it travels from the primary to the secondary once, over the central's tunnels, and is
kept in the secret store of each; it never reaches a column, a log line or an audit event).

Setting up changes the machine in four places, and an error in any puts the earlier ones back:
the package, the configuration (checked before it replaces the working one), the firewall (the
rules Noust makes are recorded, and are all it ever closes) and the store.

It is offered as an option and never by default: a name server is a service the whole internet
can ask, which has two costs that :data:`WARNINGS` states and the console shows before it is
set up.
"""

from __future__ import annotations

import base64
import logging
import secrets as token_source
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.central import require_server_role
from noust.core import audit, paths
from noust.core.authdns_store import (
    PRIMARY,
    SECONDARY,
    AuthDnsSecondary,
    AuthDnsServer,
    AuthDnsStore,
    check_address,
)
from noust.core.exceptions import DependencyError, NoustError, SecurityError, ValidationError
from noust.core.runner import CommandRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.engine import EngineError, KnotEngine
from noust.managers.authdns.knotconf import KnotSettings, ZoneEntry, render_config
from noust.managers.port_claims import PortClaims
from noust.managers.server.security_firewall import Firewall, RuleRequest
from noust.managers.server.security_probe import SecurityProbe
from noust.validators.dns_zone import check_hostname

log = logging.getLogger(__name__)

__all__ = [
    "KEY_SECRET",
    "WARNINGS",
    "AuthDnsServers",
    "ServerStatus",
    "TransferKey",
]

#: Where the transfer key is kept in the secret store.
KEY_SECRET = "authdns/transfer-key"  # noqa: S105 - a name in the secret store, not a secret

#: The comment of the firewall rules Noust makes for the name server.
_FIREWALL_COMMENT = "noust dns"

#: What setting up a name server takes on, said before anyone does it: the codes are the
#: sentences of :mod:`noust.core.server_text`.
WARNINGS = ("authdns.warning.availability", "authdns.warning.amplification")

FirewallFactory = Callable[[CommandRunner], Firewall]


def _default_firewall(runner: CommandRunner) -> Firewall:
    return Firewall(SecurityProbe(runner=runner), None, actor="noust dns")


@dataclass(frozen=True)
class TransferKey:
    """
    The key a primary and its secondaries sign zone transfers with.

    Attributes:
        name: Its name, the same on every server.
        algorithm: ``hmac-sha256``.
        secret: The key, base64.
    """

    name: str
    algorithm: str
    secret: str

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The key, for the one call that hands it to another server.
        """
        return {"name": self.name, "algorithm": self.algorithm, "secret": self.secret}


@dataclass
class ServerStatus:
    """
    Noust DNS on this server.

    Attributes:
        engine: The program.
        installed: It is on this machine.
        version: Its version.
        running: Its service runs.
        configured: Noust has set this server up.
        role: ``primary`` or ``secondary``.
        hostname: The name it is known by as a name server.
        listen: The addresses it answers on.
        primary_address: A secondary's primary.
        zones: How many zones it serves.
        secondaries: A primary's secondaries.
        candidates: Addresses it could answer on, when it is not set up.
        firewall: What Noust opened for it.
        foreign_zones: Zones of a Knot somebody else configured, which Noust does not take over.
        warnings: What setting it up takes on.
    """

    engine: str = paths.AUTHDNS_ENGINE
    installed: bool = False
    version: str | None = None
    running: bool = False
    configured: bool = False
    role: str | None = None
    hostname: str | None = None
    listen: list[str] = field(default_factory=list)
    primary_address: str | None = None
    zones: int = 0
    secondaries: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    firewall: dict[str, Any] = field(default_factory=dict)
    foreign_zones: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=lambda: list(WARNINGS))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status for the API and the CLI.
        """
        return {
            "engine": self.engine,
            "installed": self.installed,
            "version": self.version,
            "running": self.running,
            "configured": self.configured,
            "role": self.role,
            "hostname": self.hostname,
            "listen": self.listen,
            "primary_address": self.primary_address,
            "zones": self.zones,
            "secondaries": self.secondaries,
            "candidates": self.candidates,
            "firewall": self.firewall,
            "foreign_zones": self.foreign_zones,
            "warnings": self.warnings,
        }


def _public_addresses() -> list[str]:
    """
    Returns:
        The public addresses of this machine, which a name server can answer on.
    """
    from noust.managers.dns.records import server_addresses

    return server_addresses()


class AuthDnsServers:
    """
    Set Noust DNS up on this server, change it and take it away.

    Args:
        store: The store; the process-wide one by default.
        engine: Knot on this machine.
        secrets: Where the transfer key is kept.
        runner: Runs the programs.
        firewall: Builds the firewall manager (tests replace it).
        claims: Says who holds a port.
        addresses: Where the server's own addresses come from (tests replace it).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        engine: KnotEngine | None = None,
        secrets: SecretStore | None = None,
        runner: CommandRunner | None = None,
        firewall: FirewallFactory | None = None,
        claims: PortClaims | None = None,
        addresses: Callable[[], list[str]] | None = None,
    ) -> None:
        self.records = AuthDnsStore(store)
        self.engine = engine or KnotEngine(runner)
        self.secrets = secrets or SecretStore()
        self._runner = runner
        self._firewall = firewall or _default_firewall
        self._claims = claims
        self._addresses = addresses or _public_addresses

    @property
    def claims(self) -> PortClaims:
        """Who holds which port."""
        return self._claims or PortClaims(runner=self._runner, store=self.records.store)

    # -- the transfer key ----------------------------------------------------------------

    def transfer_key(self) -> TransferKey:
        """
        Returns:
            The key this server signs transfers with.

        Raises:
            ValidationError: Noust DNS has not been set up here.
        """
        secret = self.secrets.read(KEY_SECRET)
        if not secret or not secret.strip():
            raise ValidationError(
                "This server has no transfer key",
                details="Set Noust DNS up first: noust dns server setup",
                field="transfer_key",
            )
        return TransferKey(paths.AUTHDNS_TRANSFER_KEY, "hmac-sha256", secret.strip())

    # -- what there is -------------------------------------------------------------------

    def status(self) -> ServerStatus:
        """
        Returns:
            Noust DNS on this server, whether or not it has been set up.
        """
        server = self.records.server()
        installed = self.engine.installed()
        version = self.engine.version() if installed else None
        status = ServerStatus(
            installed=installed,
            version=".".join(str(part) for part in version) if version else None,
            running=self.engine.is_active() if installed else False,
            configured=server is not None,
            foreign_zones=self.engine.foreign_zones() if server is None else [],
        )
        if server is None:
            try:
                status.candidates = self._addresses()
            except (DependencyError, OSError):
                status.candidates = []
            return status
        status.role = server.role
        status.hostname = server.hostname
        status.listen = list(server.listen)
        status.primary_address = server.primary_address
        status.firewall = server.firewall
        status.secondaries = [s.to_dict() for s in self.records.secondaries()]
        if server.role == PRIMARY:
            status.zones = len(self.records.zones())
        elif status.running:
            status.zones = len([z for z in self.engine.zone_status() if z != paths.AUTHDNS_CATALOG])
        return status

    # -- installing and setting up -------------------------------------------------------

    def install(
        self, *, epel: bool = False, on_output: Callable[[str], None] | None = None
    ) -> ServerStatus:
        """
        Install the name server's package and check its version; configure nothing.

        Args:
            epel: The operator agreed to enable EPEL, where it is needed.
            on_output: Receives each command and its output.

        Returns:
            Noust DNS on this server.

        Raises:
            ConfirmationRequiredError: EPEL is needed and ``epel`` is False.
            UnsupportedHostError: It cannot be installed here.
            EngineError: A step failed or the version is too old.
        """
        require_server_role("Noust DNS")
        self.engine.install(epel=epel, on_output=on_output)
        return self.status()

    def setup(
        self,
        role: str,
        *,
        hostname: str | None = None,
        listen: list[str] | None = None,
        primary_address: str | None = None,
        transfer_key: TransferKey | None = None,
        epel: bool = False,
        actor: str | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ServerStatus:
        """
        Make this server a primary or a secondary of Noust DNS.

        Args:
            role: ``primary`` or ``secondary``.
            hostname: The name it is known by as a name server (``ns1.example.net``).
            listen: The addresses it answers on; its public addresses by default.
            primary_address: For a secondary, the primary's address.
            transfer_key: For a secondary, the key the primary signs transfers with (it asks the
                primary for it: :meth:`transfer_key`).
            epel: The operator agreed to enable EPEL, where it is needed.
            actor: Who sets it up.
            on_output: Receives each command and its output.

        Returns:
            Noust DNS on this server.

        Raises:
            ValidationError: A value is not usable, the role would change on a server that has
                zones, or an address is held by something else.
            ConfirmationRequiredError: EPEL is needed and ``epel`` is False.
            EngineError: Knot, its configuration or its service refused a step; everything
                done before it is put back.
        """
        require_server_role("Noust DNS")
        if role not in (PRIMARY, SECONDARY):
            raise ValidationError("A server is a primary or a secondary", field="role")
        previous = self.records.server()
        name = None if not hostname else check_hostname(hostname, field="hostname")
        if name == ".":
            raise ValidationError("A name server has a name", field="hostname")
        addresses = self._choose_addresses(listen)
        source = None
        if role == SECONDARY:
            if not primary_address:
                raise ValidationError(
                    "A secondary copies its zones from a primary",
                    details="Give the primary's address.",
                    field="primary_address",
                )
            source = check_address(primary_address, field_name="primary_address")
        if previous is not None and previous.role != role:
            if self.records.zones() or self.records.secondaries():
                raise ValidationError(
                    f"This server is a {previous.role} with zones or secondaries",
                    details="Remove Noust DNS here first: noust dns server remove",
                    field="role",
                )
        for address in addresses:
            for protocol in ("tcp", "udp"):
                self.claims.require_free(
                    paths.AUTHDNS_PORT,
                    protocol,
                    address=address,
                    owner="noust-dns",
                    field="listen",
                    ignore_processes=("knotd",),
                )
        if role == SECONDARY and transfer_key is None and self.secrets.read(KEY_SECRET) is None:
            raise ValidationError(
                "A secondary signs its transfers with the primary's key",
                details="Give the key the primary printed: noust dns server key",
                field="transfer_key",
            )
        # Nothing on the machine has changed yet but the package, which stays whatever follows.
        self.engine.install(epel=epel, on_output=on_output)
        new_key = False
        made: list[dict[str, Any]] = []
        try:
            if role == PRIMARY:
                if self.secrets.read(KEY_SECRET) is None:
                    self.secrets.write(
                        KEY_SECRET, base64.b64encode(token_source.token_bytes(32)).decode()
                    )
                    new_key = True
            elif transfer_key is not None:
                self.secrets.write(KEY_SECRET, transfer_key.secret)
                new_key = True
            self.engine.prepare_zones_dir()
            server = AuthDnsServer(
                engine=paths.AUTHDNS_ENGINE,
                role=role,
                hostname=name,
                listen=tuple(addresses),
                primary_address=source,
                firewall=previous.firewall if previous is not None else {},
            )
            # The configuration is rendered from the store, so the row goes in first and goes
            # back out if anything after it fails.
            self.records.save_server(server, actor=actor)
            restart = (
                previous is None or previous.listen != server.listen or not self.engine.is_active()
            )
            self._apply(restart=restart)
            record = self._open_firewall(made, previous.firewall if previous else {})
            self.records.save_server(
                AuthDnsServer(
                    engine=server.engine,
                    role=server.role,
                    hostname=server.hostname,
                    listen=server.listen,
                    primary_address=server.primary_address,
                    firewall=record,
                ),
                actor=actor,
            )
        except (NoustError, OSError):
            self._undo_setup(previous, made, new_key)
            raise
        audit.record(
            "dns.server",
            target="dns:server",
            details={"action": "setup", "role": role, "listen": addresses, "actor": actor},
        )
        return self.status()

    def _choose_addresses(self, listen: list[str] | None) -> list[str]:
        """
        Args:
            listen: What the operator named, or None.

        Returns:
            The addresses to answer on.

        Raises:
            ValidationError: None was named and the machine has no public address, or one is
                not usable.
        """
        if listen:
            chosen = [check_address(a, field_name="listen") for a in listen]
        else:
            try:
                chosen = list(self._addresses())
            except (DependencyError, OSError):
                chosen = []
            if not chosen:
                raise ValidationError(
                    "Noust DNS needs the addresses it answers on, and this server has no public "
                    "address of its own to take",
                    details="Name them: noust dns server setup --listen ADDRESS (a server behind "
                    "NAT answers on its private address, with the router forwarding port 53).",
                    field="listen",
                )
        unique = list(dict.fromkeys(chosen))
        if len(unique) > 16:
            raise ValidationError("A server answers on at most 16 addresses", field="listen")
        return unique

    def _settings(self) -> KnotSettings:
        """
        Returns:
            What the configuration is written from, as the store says it now.

        Raises:
            ValidationError: This server is not set up.
        """
        server = self.records.server()
        if server is None:
            raise ValidationError("Noust DNS is not set up on this server", field="server")
        secret = self.transfer_key().secret
        if server.role == PRIMARY:
            return KnotSettings(
                role=PRIMARY,
                listen=server.listen,
                secret=secret,
                zones=tuple(ZoneEntry(z.name, z.dnssec) for z in self.records.zones()),
                secondaries=tuple(s.address for s in self.records.secondaries()),
            )
        return KnotSettings(
            role=SECONDARY,
            listen=server.listen,
            secret=secret,
            primary_address=server.primary_address,
        )

    def _apply(self, *, restart: bool = False, zones: tuple[str, ...] = ()) -> None:
        """
        Write the configuration the store says and make Knot take it.

        Args:
            restart: Restart instead of reloading (the addresses changed).
            zones: Zones whose file is read again after the configuration.

        Raises:
            EngineError: Knot did not take it; the previous file is back.
        """
        self.engine.apply_config(render_config(self._settings()), restart=restart)
        if zones:
            self.engine.reload(zones)

    def reconfigure(self, zones: tuple[str, ...] = ()) -> None:
        """
        Bring Knot to what the store says (a zone was added or signed, a secondary joined).

        Args:
            zones: Zones whose file is read again as well.

        Raises:
            EngineError: Knot did not take it.
        """
        self._apply(zones=zones)

    def _open_firewall(
        self, made: list[dict[str, Any]], recorded: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Allow port 53 over both protocols, and remember exactly what was made.

        Args:
            made: Filled with the rules this call adds, so a failure can close them.
            recorded: What an earlier setup recorded: it is still Noust's to close.

        Returns:
            ``backend``, ``active``, ``made`` (every rule Noust made, earlier ones included) and
            ``note``.

        Raises:
            SecurityError: The firewall refused a rule.
        """
        firewall = self._firewall(self.engine.runner)
        backend, active, note = "none", False, ""
        earlier: list[dict[str, Any]] = list(recorded.get("made") or [])
        for protocol in ("udp", "tcp"):
            change = firewall.allow_direct(
                RuleRequest("allow", paths.AUTHDNS_PORT, protocol, "any", _FIREWALL_COMMENT)
            )
            backend, active = change.backend, change.active
            if change.changed:
                made.append({"port": paths.AUTHDNS_PORT, "proto": protocol, "source": "any"})
            elif change.detail:
                note = change.detail
        return {"backend": backend, "active": active, "made": [*earlier, *made], "note": note}

    def _close_firewall(self, made: list[dict[str, Any]]) -> None:
        """
        Args:
            made: Rules Noust made.
        """
        firewall = self._firewall(self.engine.runner)
        for rule in reversed(made):
            firewall.remove_direct(
                RuleRequest(
                    "allow",
                    int(rule["port"]),
                    str(rule["proto"]),
                    str(rule.get("source") or "any"),
                    _FIREWALL_COMMENT,
                )
            )

    def _undo_setup(
        self, previous: AuthDnsServer | None, made: list[dict[str, Any]], new_key: bool
    ) -> None:
        """
        Put back what a setup that failed had changed.

        Args:
            previous: This server's part before, or None.
            made: The firewall rules the setup made.
            new_key: The setup wrote the transfer key.
        """
        try:
            self._close_firewall(made)
        except (SecurityError, OSError) as exc:
            log.warning(
                "The firewall rules of a failed setup of Noust DNS could not be closed: %s", exc
            )
        if previous is None:
            self.records.delete_server()
            try:
                self.engine.restore_config()
            except (EngineError, OSError) as exc:
                log.warning("Knot could not be put back after a failed setup of Noust DNS: %s", exc)
            if new_key:
                self.secrets.delete(KEY_SECRET)
        else:
            self.records.save_server(previous)

    # -- secondaries ---------------------------------------------------------------------

    def add_secondary(
        self, address: str, hostname: str | None = None, *, actor: str | None = None
    ) -> AuthDnsSecondary:
        """
        Let a server copy this primary's zones, and tell it when they change.

        Args:
            address: Where the secondary is reached.
            hostname: The name it is known by as a name server.
            actor: Who adds it.

        Returns:
            The secondary.

        Raises:
            ValidationError: This server is not a primary, or a value is not usable.
            EngineError: Knot did not take the change; the secondary is not added.
        """
        self._require_primary()
        previous = {s.address: s for s in self.records.secondaries()}
        added = self.records.add_secondary(address, hostname, actor=actor)
        try:
            self._apply()
        except EngineError:
            if added.address in previous:
                self.records.add_secondary(added.address, previous[added.address].hostname)
            else:
                self.records.remove_secondary(added.address)
            raise
        audit.record(
            "dns.server",
            target="dns:server",
            details={"action": "secondary_add", "address": added.address, "actor": actor},
        )
        return added

    def remove_secondary(self, address: str, *, actor: str | None = None) -> bool:
        """
        Stop copying zones to a secondary.

        Args:
            address: The secondary's address.
            actor: Who removes it.

        Returns:
            True when there was one.

        Raises:
            ValidationError: This server is not a primary.
            EngineError: Knot did not take the change; the secondary stays.
        """
        self._require_primary()
        known = next(
            (s for s in self.records.secondaries() if s.address == check_address(address)), None
        )
        if known is None:
            return False
        self.records.remove_secondary(known.address)
        try:
            self._apply()
        except EngineError:
            self.records.add_secondary(known.address, known.hostname, actor=known.added_by)
            raise
        audit.record(
            "dns.server",
            target="dns:server",
            details={"action": "secondary_remove", "address": known.address, "actor": actor},
        )
        return True

    def _require_primary(self) -> AuthDnsServer:
        """
        Returns:
            This server's part.

        Raises:
            ValidationError: It is not set up, or is not a primary.
        """
        server = self.records.server()
        if server is None or server.role != PRIMARY:
            raise ValidationError(
                "This server is not a primary of Noust DNS",
                details="Secondaries are added to the server that holds the zones.",
                field="role",
            )
        return server

    # -- taking it away ------------------------------------------------------------------

    def remove(self, *, delete_zones: bool = False, actor: str | None = None) -> None:
        """
        Stop the name server and take Noust DNS off this server.

        The package and the signing keys stay: the keys are the only copy of what the registrar's
        DS record points at.

        Args:
            delete_zones: Also delete the zones and records it serves. Without it a server that
                has zones is refused.
            actor: Who removes it.

        Raises:
            ValidationError: There are zones and ``delete_zones`` is false.
        """
        server = self.records.server()
        if server is None:
            return
        if self.records.zones() and not delete_zones:
            raise ValidationError(
                "This server serves zones",
                details="Removing Noust DNS deletes them and their records. Delegate their "
                "domains elsewhere first, then: noust dns server remove --delete-zones",
                field="delete_zones",
            )
        self.engine.restore_config()
        self._close_firewall(list(server.firewall.get("made") or []))
        self.records.delete_server()
        self.secrets.delete(KEY_SECRET)
        audit.record(
            "dns.server",
            target="dns:server",
            details={"action": "remove", "role": server.role, "actor": actor},
        )
