# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloudflare Tunnel as the way into an application: no public address, no open port.

A server at home or on a NAS has nobody to point a name at: its address is private, or
it changes, or the router is not the operator's. A tunnel turns that around. Noust runs
Cloudflare's connector (``cloudflared``) as a systemd unit; it dials *out* to Cloudflare
and keeps the connection, and Cloudflare carries every request for the names routed to
it back through that connection to a service on this machine. Nothing listens for it.

**What Noust does**, in this order, each step recorded so an interrupted one is finished
from the row:

1. A tunnel is created at Cloudflare with its routes kept there (``config_src:
   cloudflare``), through the DNS account's token (:mod:`noust.managers.dns.cloudflare`).
2. The connector's token goes to a file only root reads, ``tunnels/<name>`` of the secret
   store, never an argument and never ``/tmp``. The unit hands it to the process through
   ``EnvironmentFile=`` and runs as a user that exists only while the process does.
3. A route is a public name and the local service it reaches. Noust builds the whole
   ingress from the routes it records (and the catch-all that answers 404), so what
   Cloudflare holds is always what Noust says.
4. The name's CNAME to ``<tunnel id>.cfargotunnel.com``, proxied, is made through the one
   managed-records manager (:mod:`noust.managers.dns.records`): it is ours only if we
   made it, a record somebody else made is a conflict they confirm, and the last route to
   let go deletes it.

**Where a route goes.** For an application the connector reaches nginx on this machine,
so the site's own server block, headers and policy apply: ``http://localhost:80`` when the
site serves plain HTTP (a site that redirects to HTTPS would redirect forever through a
proxy that already speaks HTTPS), ``https://localhost:443`` when it serves TLS (the
certificate is not verified: the connection never leaves the machine, and an expired
certificate must not take the tunnel down). Either way the request keeps its public host
name. A route can instead name a service directly.

``cloudflared`` is installed by the operator from Cloudflare's own package repository: a
tunnel's connector is the one program Noust never fetches itself, and never as
``latest``.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader, TemplateError

from noust.central import require_server_role
from noust.core import audit, paths, sealing
from noust.core.config import SYSTEMD_DIR as _SYSTEMD_DIR
from noust.core.exceptions import (
    DomainConflictError,
    NoustError,
    PrerequisiteError,
    ServiceError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, get_store
from noust.core.teams.model import ResourceNotFoundError
from noust.core.tunnel_store import (
    CREATING,
    READY,
    REMOVING,
    Tunnel,
    TunnelRoute,
    TunnelStore,
)
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.catalog import kind_of
from noust.managers.dns.provider import DnsProvider, DnsProviderError
from noust.managers.dns.records import DnsRecordConflict, ManagedDns, checked_hostname

logger = logging.getLogger(__name__)

#: Where the unit is written. A module attribute so a test points it elsewhere.
SYSTEMD_DIR = _SYSTEMD_DIR

#: Where Cloudflare's package repository is explained.
PACKAGE_REPOSITORY = "https://pkg.cloudflare.com/"

#: Names a tunnel may have: they are part of a unit name and a secret name.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

#: What a connector token looks like: base64, never anything a unit file could be misled by.
_TOKEN = re.compile(r"^[A-Za-z0-9+/=_-]{20,4096}$")

#: What answers every name no route claims.
CATCH_ALL = {"service": "http_status:404"}

#: Seconds systemctl may take.
_SYSTEMCTL_TIMEOUT = 60

_UNIT_MODE = 0o644


@runtime_checkable
class TunnelProvider(Protocol):
    """What a DNS account must offer for Noust to run a tunnel through it."""

    def accounts(self) -> list[tuple[str, str]]:
        """Return ``(id, name)`` of each Cloudflare account the token reaches."""

    def create_tunnel(self, account_id: str, name: str) -> str:
        """Create a tunnel and return its id."""

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        """Return the token its connector runs with."""

    def configure_tunnel(
        self, account_id: str, tunnel_id: str, ingress: list[dict[str, Any]]
    ) -> None:
        """Set the tunnel's routes."""

    def delete_tunnel(self, account_id: str, tunnel_id: str) -> bool:
        """Delete the tunnel; False when it was already gone."""


@dataclass(frozen=True)
class TunnelSupport:
    """
    Whether this server can run a tunnel, and what is missing.

    Attributes:
        available: Everything needed is in place.
        cloudflared: Where the connector is, when it is installed.
        sealed: The secret store is sealed, so a unit could not read a token at boot.
        accounts: The DNS accounts that can manage a tunnel (Cloudflare ones).
        problem: What is missing, when something is.
        hint: How to fix it.
    """

    available: bool
    cloudflared: str | None
    sealed: bool = False
    accounts: tuple[str, ...] = ()
    problem: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The support for the API and the CLI.
        """
        return {
            "available": self.available,
            "cloudflared": self.cloudflared,
            "sealed": self.sealed,
            "accounts": list(self.accounts),
            "problem": self.problem,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class TunnelResult:
    """
    What creating or deleting a tunnel did.

    Attributes:
        tunnel: The tunnel (as it was, when deleted).
        warnings: Things the operator should know.
        released: What happened to the DNS records of its routes, when it was deleted.
        rehearsal: Nothing was changed: this is what would have been.
    """

    tunnel: Tunnel
    warnings: tuple[str, ...] = field(default_factory=tuple)
    released: tuple[dict[str, Any], ...] = ()
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the CLI.
        """
        return {
            "tunnel": self.tunnel.to_dict(),
            "warnings": list(self.warnings),
            "released": list(self.released),
            "rehearsal": self.rehearsal,
        }


@dataclass(frozen=True)
class RouteResult:
    """
    What adding or removing a route did.

    Attributes:
        tunnel: The tunnel.
        route: The route.
        ingress: What Cloudflare now holds for the tunnel.
        dns: What became of the name's CNAME: ``outcome`` (``created``, ``adopted``,
            ``exists``, ``replaced``, ``released``, ``conflict``, ``manual``, ``failed`` or
            ``skipped``), the record's ``type``, ``content`` and ``account``, and for a
            conflict the ``record_id`` and ``current`` the operator confirms.
        rehearsal: Nothing was changed: this is what would have been.
    """

    tunnel: Tunnel
    route: TunnelRoute
    ingress: tuple[dict[str, Any], ...]
    dns: dict[str, Any] = field(default_factory=dict)
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the CLI.
        """
        return {
            "tunnel": self.tunnel.to_dict(),
            "route": self.route.to_dict(),
            "ingress": list(self.ingress),
            "dns": self.dns,
            "rehearsal": self.rehearsal,
        }


def secret_name(tunnel: str) -> str:
    """
    Args:
        tunnel: A tunnel's name.

    Returns:
        The secret that holds its connector's environment.
    """
    return f"tunnels/{tunnel}"


def unit_name(tunnel: str) -> str:
    """
    Args:
        tunnel: A tunnel's name.

    Returns:
        The unit that runs its connector, without the suffix.
    """
    return f"{paths.TUNNEL_UNIT_PREFIX}{tunnel}"


def origin_request(hostname: str, service: str) -> dict[str, Any]:
    """
    Say how the connector should talk to a route's service.

    Args:
        hostname: The public name.
        service: Where the route goes.

    Returns:
        ``originRequest`` for Cloudflare's ingress rule: the request keeps its public
        host name, and a local TLS origin is reached under that name without verifying it
        (the connection never leaves the machine).
    """
    if service.startswith("https://"):
        return {"noTLSVerify": True, "originServerName": hostname}
    return {"httpHostHeader": hostname}


def ingress_for(routes: Sequence[TunnelRoute]) -> list[dict[str, Any]]:
    """
    Build the whole ingress of a tunnel from its routes.

    Args:
        routes: The routes, by name.

    Returns:
        One rule per route, then the catch-all Cloudflare requires last.
    """
    rules: list[dict[str, Any]] = [
        {
            "hostname": route.hostname,
            "service": route.service,
            "originRequest": origin_request(route.hostname, route.service),
        }
        for route in sorted(routes, key=lambda item: item.hostname)
    ]
    rules.append(dict(CATCH_ALL))
    return rules


def checked_service(service: str) -> str:
    """
    Check a service a route goes to.

    Args:
        service: ``http://host[:port]`` or ``https://host[:port]``.

    Returns:
        The service, normalised.

    Raises:
        ValidationError: It is not an HTTP or HTTPS address of a host, with no path.
    """
    text = service.strip()
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or port == -1
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or not re.fullmatch(r"[A-Za-z0-9.:\-\[\]]+", parts.netloc)
    ):
        raise ValidationError(
            f"{service!r} is not a service a tunnel can reach",
            details="Give http://HOST:PORT or https://HOST:PORT, such as http://localhost:3000. "
            "Other schemes and paths are not carried.",
            field="service",
        )
    return f"{parts.scheme}://{parts.netloc}"


class TunnelManager:
    """
    Create tunnels, route names through them, and take them down.

    Args:
        accounts: The DNS accounts; built over the process-wide store by default.
        store: The store; the process-wide one by default.
        runner: Runs systemctl; the process-wide runner by default.
        fs: Writes the unit; the process-wide filesystem by default.
        secrets: Where the connector's token lives; the secret store beside the store.
        systemd_dir: Where the unit is written.
        which: Finds ``cloudflared`` (tests replace it).
        dns: Builds the managed-records manager (tests replace it).
        serves_tls: Says whether an application's site serves TLS (tests replace it).
    """

    def __init__(
        self,
        accounts: DnsAccounts | None = None,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        secrets: SecretStore | None = None,
        systemd_dir: Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
        dns: Callable[[], ManagedDns] | None = None,
        serves_tls: Callable[[App], bool] | None = None,
    ) -> None:
        self._store = store
        self._accounts = accounts
        self._runner = runner
        self._fs = fs
        self._secrets = secrets
        self._systemd_dir = systemd_dir
        self._which = which
        self._dns = dns
        self._serves_tls = serves_tls
        self.records = TunnelStore(store)

    @property
    def accounts(self) -> DnsAccounts:
        """The DNS accounts a tunnel is managed through."""
        if self._accounts is None:
            self._accounts = DnsAccounts(store=self._store, secrets=self._secrets)
        return self._accounts

    @property
    def runner(self) -> CommandRunner:
        """The runner that runs systemctl."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem the unit is written through."""
        return self._fs or get_fs()

    @property
    def secrets(self) -> SecretStore:
        """Where the connector's token is kept."""
        return self._secrets or self.accounts.secrets

    def _unit_path(self, tunnel: Tunnel) -> Path:
        """
        Args:
            tunnel: A tunnel.

        Returns:
            Where its unit file is.
        """
        return (self._systemd_dir or SYSTEMD_DIR) / f"{tunnel.unit}.service"

    def _managed_dns(self) -> ManagedDns:
        """
        Returns:
            The managed-records manager, over the same accounts and store.
        """
        if self._dns is not None:
            return self._dns()
        return ManagedDns(accounts=self.accounts, store=self._store)

    # ------------------------------------------------------------------ reading

    def all(self) -> list[Tunnel]:
        """
        Returns:
            Every tunnel, by name.
        """
        return self.records.all()

    def get(self, name: str) -> Tunnel:
        """
        Args:
            name: A tunnel's name.

        Returns:
            The tunnel.

        Raises:
            ResourceNotFoundError: There is none of that name (the API answers 404).
        """
        found = self.records.get(name)
        if found is None:
            raise ResourceNotFoundError(
                f"There is no tunnel called {name}",
                details="List them with: noust tunnel list",
            )
        return found

    def routes(self, name: str) -> list[TunnelRoute]:
        """
        Args:
            name: A tunnel's name.

        Returns:
            Its routes, by name.
        """
        return self.records.routes(self.get(name).name)

    def active(self, tunnel: Tunnel) -> str:
        """
        Ask systemd whether a tunnel's connector runs.

        Args:
            tunnel: The tunnel.

        Returns:
            ``active``, ``activating``, ``failed``, ``inactive``... as systemd says.
        """
        result = self.runner.run(
            ["systemctl", "is-active", f"{tunnel.unit}.service"], timeout=_SYSTEMCTL_TIMEOUT
        )
        return result.stdout.strip() or "unknown"

    def support(self) -> TunnelSupport:
        """
        Find out whether a tunnel can be created here, and what the operator has to do if not.

        Returns:
            The answer; never raises for a missing piece.
        """
        found = self._which("cloudflared")
        sealed = sealing.is_sealed(self.secrets.root)
        names = tuple(
            account.name
            for account in self.accounts.accounts()
            if (spec := kind_of(account.kind)) is not None and spec.native
        )
        problem: str | None = None
        hint: str | None = None
        if found is None:
            problem = "cloudflared is not installed"
            hint = (
                "Install it from Cloudflare's own package repository, "
                f"{PACKAGE_REPOSITORY}, then run the command again."
            )
        elif sealed:
            problem = "The secret store is sealed"
            hint = (
                "A tunnel's connector reads its token at boot, with nobody to type a passphrase. "
                "Run tunnels on a server, not on a sealed central."
            )
        elif not names:
            problem = "There is no Cloudflare account to manage a tunnel with"
            hint = (
                "Add one with Account > Cloudflare Tunnel:Edit: "
                "noust dns provider add NAME --kind cloudflare --token-stdin"
            )
        return TunnelSupport(problem is None, found, sealed, names, problem, hint)

    # ------------------------------------------------------------------ creating

    def create(
        self,
        name: str,
        *,
        account: str | None = None,
        cloudflare_account: str | None = None,
        actor: str | None = None,
    ) -> TunnelResult:
        """
        Create a tunnel at Cloudflare and run its connector here.

        Args:
            name: What to call it: lower case letters, digits and dashes.
            account: The DNS account to create it through; the only Cloudflare one by default.
            cloudflare_account: The Cloudflare account id, when the token reaches several.
            actor: Who is asking.

        Returns:
            The tunnel, ready.

        Raises:
            ValidationError: The name is taken or not acceptable, or the account is ambiguous.
            PrerequisiteError: ``cloudflared`` is not installed, or the store is sealed.
            DnsProviderError: Cloudflare refused it (its words are the error).
            ServiceError: The connector did not start; everything made is undone.
        """
        require_server_role("Tunnels")
        if not _NAME.fullmatch(name or ""):
            raise ValidationError(
                f"{name!r} cannot be the name of a tunnel",
                details="Use lower case letters, digits and dashes, up to 40, starting with a "
                "letter or digit.",
                field="name",
            )
        if self.records.get(name) is not None:
            raise ValidationError(
                f"There is already a tunnel called {name}",
                details="Remove it first: noust tunnel remove " + name,
                field="name",
            )
        support = self.support()
        if support.cloudflared is None or support.sealed:
            raise PrerequisiteError(
                support.problem or "A tunnel cannot run here", details=support.hint or ""
            )
        chosen = self._choose_account(account, support.accounts)
        provider = self._provider(chosen)
        cloudflare_id = self._cloudflare_account(chosen, provider, cloudflare_account)
        tunnel = Tunnel(
            name=name,
            provider=chosen,
            account_id=cloudflare_id,
            tunnel_id="",
            unit=unit_name(name),
            state=CREATING,
            created_by=actor,
        )
        if is_rehearsal():
            return TunnelResult(tunnel, rehearsal=True)
        try:
            tunnel = self.records.create(tunnel)
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"There is already a tunnel called {name}", field="name") from exc
        try:
            tunnel_id = provider.create_tunnel(cloudflare_id, name)
            tunnel = self.records.set_state(name, CREATING, tunnel_id) or tunnel
            token = provider.tunnel_token(cloudflare_id, tunnel_id)
            if not _TOKEN.fullmatch(token):
                raise DnsProviderError("Cloudflare returned a connector token that is not one")
            self.secrets.write(secret_name(name), f"TUNNEL_TOKEN={token}\n")
            provider.configure_tunnel(cloudflare_id, tunnel_id, ingress_for(()))
            self._start_connector(tunnel, support.cloudflared)
        except (NoustError, OSError):
            self._abort(tunnel, provider)
            raise
        ready = self.records.set_state(name, READY) or tunnel
        audit.record(
            "tunnels.create",
            target=f"tunnel:{name}",
            details={"account": chosen, "tunnel_id": tunnel.tunnel_id, "actor": actor},
        )
        return TunnelResult(ready)

    def _choose_account(self, account: str | None, native: Sequence[str]) -> str:
        """
        Pick the DNS account a tunnel is created through.

        Args:
            account: What was asked for.
            native: The Cloudflare accounts there are.

        Returns:
            The account's name.

        Raises:
            ValidationError: None was named and there is not exactly one, or the one named
                cannot manage a tunnel.
        """
        if account is not None:
            if account not in native:
                raise ValidationError(
                    f"{account} is not a Cloudflare account Noust can manage a tunnel with",
                    details="Cloudflare accounts: " + (", ".join(native) or "none") + ".",
                    field="account",
                )
            return account
        if len(native) == 1:
            return native[0]
        raise ValidationError(
            "Say which DNS account creates the tunnel",
            details="Cloudflare accounts: " + (", ".join(native) or "none") + ".",
            field="account",
        )

    def _provider(self, account: str) -> TunnelProvider:
        """
        Args:
            account: A DNS account's name.

        Returns:
            Its client.

        Raises:
            ValidationError: The account's provider cannot manage tunnels.
        """
        provider: DnsProvider = self.accounts.provider(account)
        if not isinstance(provider, TunnelProvider):
            raise ValidationError(
                f"{account} cannot manage tunnels", details="Only Cloudflare does.", field="account"
            )
        return provider

    def _cloudflare_account(self, account: str, provider: TunnelProvider, asked: str | None) -> str:
        """
        Find the Cloudflare account a tunnel lives in.

        Args:
            account: The DNS account.
            provider: Its client.
            asked: What the operator named, if anything.

        Returns:
            The Cloudflare account id.

        Raises:
            ValidationError: The token reaches several accounts and none was named.
        """
        if asked:
            return asked
        recorded = self.accounts.get(account).settings.get("account_id")
        if recorded:
            return str(recorded)
        reachable = provider.accounts()
        if len(reachable) == 1:
            return reachable[0][0]
        listing = ", ".join(f"{ident} ({label})" for ident, label in reachable) or "none"
        raise ValidationError(
            "The token reaches more than one Cloudflare account, or none",
            details=f"Name the one the tunnel lives in. Reachable: {listing}.",
            field="cloudflare_account",
        )

    def _start_connector(self, tunnel: Tunnel, cloudflared: str) -> None:
        """
        Write the connector's unit and start it.

        Args:
            tunnel: The tunnel.
            cloudflared: The program's absolute path.

        Raises:
            ServiceError: The unit cannot be written or the connector does not run.
        """
        try:
            text = (
                Environment(
                    loader=PackageLoader("noust", "templates/systemd"),
                    trim_blocks=True,
                    lstrip_blocks=True,
                    autoescape=False,  # noqa: S701 - a systemd unit, not markup
                )
                .get_template("tunnel.service.j2")
                .render(
                    name=tunnel.name,
                    cloudflared=cloudflared,
                    env_file=str(self.secrets.path(secret_name(tunnel.name))),
                )
            )
        except TemplateError as exc:
            raise ServiceError("Failed to render the tunnel's unit", details=str(exc)) from exc
        path = self._unit_path(tunnel)
        try:
            self.fs.write_text(path, text, mode=_UNIT_MODE)
        except OSError as exc:
            raise ServiceError(
                f"Failed to write unit file: {path}",
                details=f"{exc}. Noust must run as root to manage systemd units.",
            ) from exc
        self._systemctl("daemon-reload")
        started = self._systemctl("enable", "--now", f"{tunnel.unit}.service")
        if not started.success:
            raise ServiceError(
                f"The connector of {tunnel.name} did not start",
                details=(started.stderr or started.stdout).strip()
                or f"See: journalctl -u {tunnel.unit}",
            )

    def _systemctl(self, *args: str) -> Any:
        """
        Args:
            *args: systemctl's arguments.

        Returns:
            Its result.
        """
        return self.runner.run(["systemctl", *args], timeout=_SYSTEMCTL_TIMEOUT)

    def _abort(self, tunnel: Tunnel, provider: TunnelProvider) -> None:
        """
        Put back everything a creation that failed had made.

        Args:
            tunnel: The tunnel being created.
            provider: Its client.

        The row stays, as removing, when Cloudflare refuses to delete the tunnel, so
        ``noust tunnel remove`` finishes the job from it.
        """
        self.records.set_state(tunnel.name, REMOVING)
        try:
            self._take_down(tunnel, provider)
        except NoustError as exc:
            logger.warning(
                "The tunnel %s could not be removed completely (%s); finish with: "
                "noust tunnel remove %s",
                tunnel.name,
                exc,
                tunnel.name,
            )
            return
        self.records.delete(tunnel.name)

    def _take_down(self, tunnel: Tunnel, provider: TunnelProvider) -> None:
        """
        Stop the connector, delete the tunnel at Cloudflare, and remove the unit and token.

        Args:
            tunnel: The tunnel.
            provider: Its client.

        Raises:
            DnsProviderError: Cloudflare refused to delete it (a connector still counts as
                connected for a moment after it stops).
        """
        unit_file = self._unit_path(tunnel)
        if unit_file.exists():
            self._systemctl("disable", "--now", f"{tunnel.unit}.service")
        if tunnel.tunnel_id:
            provider.delete_tunnel(tunnel.account_id, tunnel.tunnel_id)
        self.fs.remove(unit_file, missing_ok=True)
        self._systemctl("daemon-reload")
        self.secrets.delete(secret_name(tunnel.name))

    # ------------------------------------------------------------------ routes

    def add_route(
        self,
        tunnel_name: str,
        hostname: str,
        *,
        app_domain: str | None = None,
        service: str | None = None,
        dns: bool = True,
        replace_record: str | None = None,
        replace_current: str | None = None,
        actor: str | None = None,
    ) -> RouteResult:
        """
        Carry a public name through a tunnel to a service on this machine.

        Asking again for a route that exists changes where it goes and makes its record.

        Args:
            tunnel_name: The tunnel.
            hostname: The public name.
            app_domain: The application it is for: the site is reached through nginx.
            service: Where the connector sends it; the application's site by default.
            dns: Point the name's CNAME at the tunnel in the account that holds its zone.
            replace_record: The id of the record in the name's way the operator confirmed
                replacing.
            replace_current: What that record held when they confirmed.
            actor: Who is asking.

        Returns:
            The route, the ingress Cloudflare now holds, and what became of the CNAME. A
            conflicting record is reported, not raised: the route works at Cloudflare and
            the name starts to reach it when the record is made.

        Raises:
            ResourceNotFoundError: The tunnel or the application does not exist.
            ValidationError: The name or the service is not acceptable, or the name is not one
                of the application's, or is carried by another tunnel.
            PrerequisiteError: The tunnel is not ready.
            DnsProviderError: Cloudflare refused the ingress; the route is not kept.
        """
        require_server_role("Tunnels")
        tunnel = self._ready(tunnel_name)
        name = checked_hostname(hostname)
        app, chosen = self._route_service(name, app_domain, service)
        carried = self.records.route_for(name)
        if carried is not None and carried.tunnel != tunnel.name:
            raise DomainConflictError(
                f"{name} is already carried by the tunnel {carried.tunnel}",
                details=f"Remove it there first: noust tunnel route remove {carried.tunnel} {name}",
            )
        route = TunnelRoute(tunnel.name, name, chosen, app.domain if app else None)
        if is_rehearsal():
            return RouteResult(
                tunnel,
                route,
                tuple(ingress_for([*self.records.routes(tunnel.name), route])),
                {},
                True,
            )
        previous = carried
        saved = self.records.set_route(route)
        provider = self._provider(tunnel.provider)
        ingress = ingress_for(self.records.routes(tunnel.name))
        try:
            provider.configure_tunnel(tunnel.account_id, tunnel.tunnel_id, ingress)
        except (NoustError, OSError):
            if previous is None:
                self.records.delete_route(tunnel.name, name)
            else:
                self.records.set_route(previous)
            raise
        outcome: dict[str, Any] = {"outcome": "skipped"}
        if dns:
            outcome = self._point_dns(tunnel, saved, replace_record, replace_current, actor)
        audit.record(
            "tunnels.route",
            target=f"tunnel:{tunnel.name}",
            details={
                "action": "add",
                "hostname": name,
                "service": chosen,
                "app": saved.app_domain,
                "dns": outcome.get("outcome"),
                "actor": actor,
            },
        )
        return RouteResult(tunnel, saved, tuple(ingress), outcome)

    def _route_service(
        self, hostname: str, app_domain: str | None, service: str | None
    ) -> tuple[App | None, str]:
        """
        Decide where a route goes.

        Args:
            hostname: The public name.
            app_domain: The application it is for, if any.
            service: What the operator named, if anything.

        Returns:
            The application (when there is one) and the service.

        Raises:
            ResourceNotFoundError: The application does not exist.
            ValidationError: Nothing says where the name goes, or the name is not the
                application's.
        """
        app: App | None = None
        if app_domain is not None:
            store = self._store or get_store()
            app = store.get_app(app_domain)
            if app is None:
                raise ResourceNotFoundError(
                    f"There is no application called {app_domain}",
                    details="List them with: noust list",
                )
            names = {record.domain for record in store.list_domains(app.domain)} | {app.domain}
            if hostname not in names:
                raise ValidationError(
                    f"{hostname} is not a domain of {app.domain}",
                    details=f"Add it first: noust domain add {app.domain} {hostname}",
                    field="hostname",
                )
        if service:
            return app, checked_service(service)
        if app is None:
            raise ValidationError(
                "Say where the name goes",
                details="Name the application it is for (APP) or the service (http://localhost:3000).",
                field="service",
            )
        return app, "https://localhost:443" if self._tls(app) else "http://localhost:80"

    def _tls(self, app: App) -> bool:
        """
        Args:
            app: An application.

        Returns:
            Whether its site serves TLS now.
        """
        if self._serves_tls is not None:
            return self._serves_tls(app)
        from noust.deployers.domains import serves_tls, site_deployer

        return serves_tls(app, site_deployer(app))

    def _ready(self, name: str) -> Tunnel:
        """
        Args:
            name: A tunnel's name.

        Returns:
            The tunnel, when it is ready.

        Raises:
            ResourceNotFoundError: There is no such tunnel.
            PrerequisiteError: It is being created or removed.
        """
        tunnel = self.get(name)
        if tunnel.state != READY:
            raise PrerequisiteError(
                f"The tunnel {name} is {tunnel.state}",
                details=f"Finish it first: noust tunnel remove {name}",
            )
        return tunnel

    def _point_dns(
        self,
        tunnel: Tunnel,
        route: TunnelRoute,
        replace_record: str | None,
        replace_current: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        """
        Point a name at a tunnel with a proxied CNAME, as far as it can be done unasked.

        Args:
            tunnel: The tunnel.
            route: The route.
            replace_record: The record the operator confirmed replacing.
            replace_current: What it held when they confirmed.
            actor: Who is asking.

        Returns:
            ``{"outcome": ...}`` and what goes with it: never raises for DNS, because the
            route already works at Cloudflare and the operator may make the record by hand.
        """
        wanted = tunnel.target
        base: dict[str, Any] = {"type": "CNAME", "content": wanted, "hostname": route.hostname}
        try:
            result = self._managed_dns().ensure(
                route.hostname,
                wanted,
                app_domain=route.app_domain or f"tunnel:{tunnel.name}",
                record_type="CNAME",
                proxied=True,
                account=tunnel.provider,
                replace_record=replace_record,
                replace_current=replace_current,
                actor=actor,
            )
        except DnsRecordConflict as exc:
            return {
                **base,
                "outcome": "conflict",
                "record_id": exc.record_id,
                "current": exc.current,
                "detail": exc.message,
            }
        except ValidationError as exc:
            return {**base, "outcome": "manual", "detail": f"{exc.message}. {exc.details}".strip()}
        except NoustError as exc:
            return {**base, "outcome": "failed", "detail": exc.output or str(exc)}
        except sqlite3.Error as exc:
            return {**base, "outcome": "failed", "detail": str(exc)}
        return {**base, "outcome": result.outcome.value, "account": result.account}

    def remove_route(
        self, tunnel_name: str, hostname: str, *, keep_dns: bool = False, actor: str | None = None
    ) -> RouteResult:
        """
        Stop carrying a public name, and let go of its record.

        Args:
            tunnel_name: The tunnel.
            hostname: The public name.
            keep_dns: Do not touch the name's CNAME.
            actor: Who is asking.

        Returns:
            The route that was removed and the ingress that remains.

        Raises:
            ResourceNotFoundError: The tunnel does not exist, or does not carry the name.
            DnsProviderError: Cloudflare refused the ingress; the route is kept.
        """
        require_server_role("Tunnels")
        tunnel = self._ready(tunnel_name)
        name = checked_hostname(hostname)
        route = next((r for r in self.records.routes(tunnel.name) if r.hostname == name), None)
        if route is None:
            raise ResourceNotFoundError(
                f"{tunnel.name} does not carry {name}",
                details="See what it carries: noust tunnel list",
            )
        remaining = [r for r in self.records.routes(tunnel.name) if r.hostname != name]
        if is_rehearsal():
            return RouteResult(tunnel, route, tuple(ingress_for(remaining)), {}, True)
        provider = self._provider(tunnel.provider)
        ingress = ingress_for(remaining)
        provider.configure_tunnel(tunnel.account_id, tunnel.tunnel_id, ingress)
        self.records.delete_route(tunnel.name, name)
        outcome: dict[str, Any] = {"outcome": "skipped"}
        if not keep_dns:
            outcome = self._release_dns(tunnel, route)
        audit.record(
            "tunnels.route",
            target=f"tunnel:{tunnel.name}",
            details={
                "action": "remove",
                "hostname": name,
                "dns": outcome.get("outcome"),
                "actor": actor,
            },
        )
        return RouteResult(tunnel, route, tuple(ingress), outcome)

    def _release_dns(self, tunnel: Tunnel, route: TunnelRoute) -> dict[str, Any]:
        """
        Let go of a route's CNAME: the last one to let go deletes what Noust made.

        Args:
            tunnel: The tunnel.
            route: The route.

        Returns:
            ``{"outcome": ...}``; never raises, because the route is already gone.
        """
        try:
            released = self._managed_dns().release(
                route.app_domain or f"tunnel:{tunnel.name}", hostname=route.hostname
            )
        except (NoustError, sqlite3.Error) as exc:
            return {"outcome": "failed", "hostname": route.hostname, "detail": str(exc)}
        if not released:
            return {"outcome": "none", "hostname": route.hostname}
        return {"outcome": released[0].outcome.value, "hostname": route.hostname}

    # ------------------------------------------------------------------ deleting

    def delete(self, name: str, *, actor: str | None = None) -> TunnelResult:
        """
        Stop a tunnel's connector, delete the tunnel at Cloudflare, and let go of its records.

        Asking again after a failure finishes it.

        Args:
            name: The tunnel.
            actor: Who is asking.

        Returns:
            The tunnel that was removed and what became of the records of its routes.

        Raises:
            ResourceNotFoundError: There is no such tunnel.
            DnsProviderError: Cloudflare refused to delete it; the same call finishes it.
        """
        require_server_role("Tunnels")
        tunnel = self.get(name)
        if is_rehearsal():
            return TunnelResult(tunnel, rehearsal=True)
        self.records.set_state(name, REMOVING)
        routes = self.records.routes(name)
        provider = self._provider(tunnel.provider)
        self._take_down(tunnel, provider)
        released = tuple(self._release_dns(tunnel, route) for route in routes)
        self.records.delete(name)
        audit.record(
            "tunnels.delete",
            target=f"tunnel:{name}",
            details={"tunnel_id": tunnel.tunnel_id, "routes": len(routes), "actor": actor},
        )
        warnings = tuple(
            f"The record of {item['hostname']} was not released: {item.get('detail', '')}"
            for item in released
            if item.get("outcome") == "failed"
        )
        return TunnelResult(tunnel, warnings, released)

    def repair(self) -> list[str]:
        """
        Finish the creations and removals that were interrupted.

        A tunnel left ``creating`` never finished and is removed; one left ``removing`` is
        finished.

        Returns:
            The names that were put right.
        """
        require_server_role("Tunnels")
        done: list[str] = []
        for tunnel in self.records.all():
            if tunnel.state != READY:
                self.delete(tunnel.name)
                done.append(tunnel.name)
        return done
