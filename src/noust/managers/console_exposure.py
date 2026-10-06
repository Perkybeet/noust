# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console on a name of its own, over TLS: ``noust console expose``.

The console is root-equivalent and listens on loopback; the supported way in is
an SSH tunnel. This is the other way, opt-in and guided, for an operator who
wants to open it from a browser without one: an nginx site on a dedicated name
that proxies to the console, with a Let's Encrypt certificate obtained the way
every site gets one (:func:`~noust.managers.webserver.create_secured_site`),
optionally restricted to the addresses the operator names.

What makes it safe enough to offer is what it refuses to do:

- **Without a second factor on every account.** Whoever signs in reaches
  everything this server can do, so an account that can sign in with a password
  alone stops the exposure (:func:`second_factor_blockers`).
- **Under the ENS profile**, unless forced in so many words and then only with
  an allow-list: the profile keeps the console on loopback
  (:func:`~noust.core.ens.profile.console_exposure_allowed`).
- **Over cleartext, ever.** There is no ``--no-ssl``: until a certificate
  exists the site answers 404 to everything but the certificate challenge.
- **On a name that belongs to something else**: an application's name, or a
  site Noust did not write. Those are never rewritten.

And it records exactly what it made (:class:`ConsoleExposureStore`), so
:meth:`ConsoleExposure.unexpose` removes that and nothing more: the site and
the certificate only if Noust created them, the trusted proxy only if Noust
added it, the two settings only if they still hold what Noust wrote.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from noust.central import require_server_role
from noust.core import audit
from noust.core.config import Config
from noust.core.console_exposure_store import (
    STATE_ACTIVE,
    STATE_PENDING,
    ConsoleExposureStore,
    ExposureState,
)
from noust.core.ens import profile
from noust.core.exceptions import (
    CertificateError,
    DomainError,
    NoustError,
    SecurityError,
    ServiceError,
    SiteError,
)
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import get_store
from noust.managers.cert_manager import CertManager
from noust.managers.webserver import (
    NGINX_BACKEND,
    SecuredSite,
    WebServerManager,
    create_secured_site,
)
from noust.validators.domain import is_valid_domain

logger = logging.getLogger(__name__)

#: The nginx template, under ``templates/nginx``.
TEMPLATE = "console"

#: The first line of every console site: how Noust recognises its own.
MARKER = "# Noust console site for "

#: The peer the console is told to believe forwarding headers from: nginx, on this machine.
PROXY_PEER = "127.0.0.1"

#: How a restart of the console is made. ``now`` for a command at a terminal;
#: ``later`` for a request the console itself is answering, which a restart in
#: the middle would cut; ``never`` for a caller that restarts it itself.
RestartMode = Literal["now", "later", "never"]

#: Seconds before a deferred restart fires, long enough for the answer to leave.
RESTART_DELAY_SECONDS = 4

#: The most addresses and networks one allow-list holds.
MAX_ALLOW_ENTRIES = 64

#: Names that are never a public name for the console.
_REFUSED_NAMES = frozenset({"localhost"})

Progress = Callable[[str], None]


class ConsoleHandle(Protocol):
    """What the exposure needs from the console's service, so a test can stand in for it."""

    def is_service(self) -> bool:
        """Whether the console runs as ``noust-web.service``."""
        ...

    def upstream(self) -> tuple[int, str]:
        """The loopback port the console listens on, and ``http`` or ``https``."""
        ...

    def trust_proxy(self, address: str) -> bool:
        """Make the service believe forwarding headers from a peer; True when its unit changed."""
        ...

    def forget_proxy(self, address: str) -> bool:
        """Stop believing a peer; True when the unit changed."""
        ...

    def restart(self, mode: RestartMode) -> None:
        """Restart the service according to ``mode``."""
        ...


class SystemConsole:
    """
    The real console service.

    Args:
        runner: The runner a deferred restart goes through.
        verbose: Whether to log verbosely.
    """

    def __init__(self, runner: CommandRunner | None = None, verbose: bool = False) -> None:
        self._runner = runner
        self._verbose = verbose

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    def is_service(self) -> bool:
        """
        Report whether the console is a service of Noust's that is running.

        Returns:
            True when ``noust-web.service`` is installed and active.
        """
        from noust.cli.commands import web

        status = web._service_status(self._verbose)
        return status is not None and bool(status["active"])

    def upstream(self) -> tuple[int, str]:
        """
        Say where the service listens.

        Returns:
            The port and the scheme.
        """
        from noust.cli.commands import web

        return web.console_service_port()

    def trust_proxy(self, address: str) -> bool:
        """
        Add a peer to the service's trusted proxies.

        Args:
            address: The peer.

        Returns:
            True when the unit was rewritten.
        """
        from noust.cli.commands import web

        return web.change_trusted_proxies(add=[address], verbose=self._verbose)

    def forget_proxy(self, address: str) -> bool:
        """
        Remove a peer from the service's trusted proxies.

        Args:
            address: The peer.

        Returns:
            True when the unit was rewritten.
        """
        from noust.cli.commands import web

        return web.change_trusted_proxies(remove=[address], verbose=self._verbose)

    def restart(self, mode: RestartMode) -> None:
        """
        Restart the service so a rewritten unit applies.

        Args:
            mode: ``now`` restarts and waits until it serves; ``later`` schedules
                the restart a few seconds ahead in a unit of its own, so the
                request that asked can still be answered; ``never`` does nothing.

        Raises:
            ServiceError: When ``now`` is asked and the service does not come back.
        """
        if mode == "never":
            return
        if mode == "later":
            self.runner.run(
                [
                    "systemd-run",
                    f"--unit=noust-console-restart-{secrets.token_hex(4)}",
                    "--collect",
                    f"--on-active={RESTART_DELAY_SECONDS}",
                    "--description=Restart the Noust console for its new exposure",
                    "systemctl",
                    "try-restart",
                    "noust-web.service",
                ],
                timeout=60,
                check=True,
            )
            return
        from noust.cli.commands import web

        web.restart_console_service(self._verbose)


@dataclass
class ExposureResult:
    """
    What exposing or unexposing did.

    Attributes:
        domain: The public name.
        url: Where the console answers now, or None after an unexpose.
        allow_ips: The addresses allowed through nginx.
        site_created: Noust wrote the site this time (or earlier, for a re-run).
        certificate_created: Noust obtained the certificate.
        restart: How the console was restarted.
        removed: For an unexpose, what was removed, in order.
        notes: What the operator should know, in order.
    """

    domain: str
    url: str | None = None
    allow_ips: list[str] = field(default_factory=list)
    site_created: bool = False
    certificate_created: bool = False
    restart: str = "never"
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the outcome for the API and the CLI.

        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "url": self.url,
            "allow_ips": list(self.allow_ips),
            "site_created": self.site_created,
            "certificate_created": self.certificate_created,
            "restart": self.restart,
            "removed": list(self.removed),
            "notes": list(self.notes),
        }


def parse_allow_ips(entries: Sequence[str]) -> list[str]:
    """
    Check the addresses and networks allowed to reach the console.

    Every entry becomes an ``allow`` directive in a file nginx reads as root, so
    each one is parsed as an address or a network, never copied: a ``;`` or a
    newline in it would end the directive and start another.

    Args:
        entries: Addresses (``203.0.113.7``) or networks (``203.0.113.0/24``,
            ``2001:db8::/32``); each may hold several separated by commas or spaces.

    Returns:
        The networks, normalised, without repeats, in the order given.

    Raises:
        SecurityError: When an entry is not an address or a network, there are
            too many, or one is the whole internet (which is no list at all).
    """
    found: list[str] = []
    for entry in entries:
        for piece in entry.replace(",", " ").split():
            try:
                network = ipaddress.ip_network(piece, strict=False)
            except ValueError as exc:
                raise SecurityError(
                    f"{piece!r} is not an address or a network",
                    details=(
                        "Give an IP address (203.0.113.7) or a network in CIDR form "
                        "(203.0.113.0/24, 2001:db8::/32)."
                    ),
                ) from exc
            if network.prefixlen == 0:
                raise SecurityError(
                    f"{piece} allows every address: that is no allow-list",
                    details="Name who may connect, or leave --allow-ip out to allow everyone.",
                )
            text = str(network)
            if text not in found:
                found.append(text)
    if len(found) > MAX_ALLOW_ENTRIES:
        raise SecurityError(
            f"{len(found)} allowed networks is more than the {MAX_ALLOW_ENTRIES} one site holds",
            details="Join them into larger networks, or put the console behind a VPN.",
        )
    return found


def second_factor_blockers() -> list[str]:
    """
    List what stops the console from being published for want of a second factor.

    Whoever signs in reaches what this server can do as root, and an exposed
    sign-in form is tried by the whole internet. So every account that can sign
    in must hold a second factor (an authenticator or a passkey); and before any
    account exists, the access token's own second factor must be on.

    Returns:
        One actionable sentence per blocker; empty when the console may be published.
    """
    from noust.core.accounts import AccountManager

    try:
        accounts = AccountManager().list_all()
    except (NoustError, sqlite3.Error) as exc:
        return [
            f"The accounts could not be read ({exc}); the console is not published on a guess. "
            "Check that the store is readable (noust store stats)."
        ]
    signing_in = [account for account in accounts if account.can_sign_in()]
    if signing_in:
        lacking = sorted(account.username for account in signing_in if not account.has_mfa)
        return [
            f"The account {name} signs in with a password alone. Its owner enrols an "
            "authenticator or a passkey (Security, after signing in), or the account is "
            f"disabled (noust user disable {name}); then publish the console."
            for name in lacking
        ]
    if _master_second_factor():
        return []
    return [
        "No account exists and the console's access token has no second factor. Create the first "
        "account (noust user create NAME --role admin) and enrol its authenticator or passkey, or "
        "enable the token's own with 'noust 2fa enroll' and 'noust 2fa confirm CODE', then "
        "publish the console."
    ]


def _master_second_factor() -> bool:
    """
    Report whether the access token's own sign-in is protected by a second factor.

    Returns:
        True when the console's TOTP is enabled or the token has a passkey.
    """
    try:
        from noust.core.accounts.passkeys import PasskeyManager
        from noust.web.auth import SecurityConfig, TokenManager, get_global_token_manager
    except ImportError:
        return False
    manager = get_global_token_manager() or TokenManager(SecurityConfig())
    return bool(manager.totp_enabled() or PasskeyManager().count(None) > 0)


def normalise_domain(domain: str) -> str:
    """
    Check the public name and refuse what cannot be one.

    Args:
        domain: The name, as typed.

    Returns:
        The name, lower case and without a trailing dot.

    Raises:
        DomainError: When it is not a domain name, or is a name that can never
            be a public one (an address, ``localhost``).
    """
    name = domain.strip().lower().rstrip(".")
    valid, reason = is_valid_domain(name)
    if not valid or name in _REFUSED_NAMES:
        raise DomainError(
            f"Invalid domain: {domain!r}",
            details=reason or "Use a name that points at this server, such as console.example.com.",
        )
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise DomainError(
        f"{domain!r} is an address, not a name",
        details="A certificate needs a domain name: console.example.com.",
    )


class ConsoleExposure:
    """
    Publish the console on a public name, and take it off again.

    Args:
        console: The console's service; the real one by default.
        store: Where the exposure is recorded; the process-wide store by default.
        nginx: The nginx manager (tests).
        certs: The certificate manager (tests).
        config: The configuration; the process-wide one by default.
        dns_check: Tells whether a name resolves to this machine (tests); the
            domains module's by default.
        second_factor: Lists what stops the exposure for want of a second
            factor (tests); :func:`second_factor_blockers` by default.
        verbose: Verbose logging on managers built here.
    """

    def __init__(
        self,
        *,
        console: ConsoleHandle | None = None,
        store: ConsoleExposureStore | None = None,
        nginx: WebServerManager | None = None,
        certs: CertManager | None = None,
        config: Config | None = None,
        dns_check: Callable[[str], Any] | None = None,
        second_factor: Callable[[], list[str]] | None = None,
        verbose: bool = False,
    ) -> None:
        self._console = console or SystemConsole(verbose=verbose)
        self._exposures = store or ConsoleExposureStore()
        self._nginx = nginx
        self._certs = certs
        self._config = config
        self._dns_check = dns_check
        self._second_factor = second_factor or second_factor_blockers
        self._verbose = verbose

    @property
    def nginx(self) -> WebServerManager:
        """The nginx manager."""
        if self._nginx is None:
            self._nginx = WebServerManager(NGINX_BACKEND, verbose=self._verbose)
        return self._nginx

    @property
    def certs(self) -> CertManager:
        """The certificate manager."""
        if self._certs is None:
            self._certs = CertManager(verbose=self._verbose)
        return self._certs

    @property
    def config(self) -> Config:
        """The configuration."""
        if self._config is None:
            self._config = Config()
        return self._config

    def state(self) -> ExposureState | None:
        """
        Read what the console is exposed as.

        Returns:
            The recorded exposure, or None.
        """
        return self._exposures.get()

    def status(self) -> dict[str, Any]:
        """
        Describe the exposure, and check that what it made is still there.

        Returns:
            ``exposed``, the recorded fields when it is, and ``problems``: what
            the record says exists and does not (a site someone removed by hand).
        """
        state = self._exposures.get()
        if state is None:
            return {
                "exposed": False,
                "ens_allows": profile.console_exposure_allowed(self.config),
                "problems": [],
            }
        problems: list[str] = []
        if state.state != STATE_ACTIVE:
            problems.append(
                "An earlier attempt did not finish. Run 'noust console unexpose' to clean it up."
            )
        if not self.nginx.site_exists(state.domain):
            problems.append(f"The nginx site of {state.domain} is gone.")
        if not self._console.is_service():
            problems.append("The console is not running as a service.")
        return {
            "exposed": state.state == STATE_ACTIVE,
            **state.to_dict(),
            "ens_allows": profile.console_exposure_allowed(self.config),
            "problems": problems,
        }

    def expose(
        self,
        domain: str,
        *,
        allow_ips: Sequence[str] = (),
        force_ens: bool = False,
        skip_dns_check: bool = False,
        restart: RestartMode = "now",
        actor: str | None = None,
        progress: Progress | None = None,
    ) -> ExposureResult:
        """
        Publish the console on ``domain``, over TLS.

        Args:
            domain: The name, already pointing at this server.
            allow_ips: Addresses and networks allowed to connect; none allows everyone.
            force_ens: Publish although the ENS profile is on. Needs ``allow_ips``.
            skip_dns_check: Do not require the name to resolve to this machine's
                addresses (a server behind NAT sees only its private one).
            restart: How to restart the console so it trusts nginx's headers.
            actor: Who asked, an actor label, for the record.
            progress: Told each step as it starts.

        Returns:
            What was done.

        Raises:
            RoleError: On a hub, which has no nginx.
            SecurityError: Under the ENS profile without being forced, with an
                account lacking a second factor, or with a bad allow-list.
            DomainError: When the name is invalid, an application's, or does not
                point at this server.
            SiteError: When a site Noust did not write is on the name, or nginx
                refused the configuration.
            ServiceError: When the console is not running as a service.
            CertificateError: When no certificate could be obtained; nothing this
                call created is left.
        """
        say = progress or (lambda _line: None)
        name, networks, existing = self._check(
            domain, allow_ips, force_ens=force_ens, skip_dns_check=skip_dns_check
        )

        port, scheme = self._console.upstream()
        before = replace(existing) if existing is not None else None
        state = existing or ExposureState(
            domain=name,
            port=port,
            scheme=scheme,
            previous_public_url=_text(self.config.get("web.public_url")),
            previous_allowed_hosts=_hosts(self.config.get("web.allowed_hosts")),
            created_by=actor,
        )
        state.port, state.scheme, state.allow_ips = port, scheme, networks
        state.state = STATE_PENDING
        self._exposures.save(state)

        completed = False
        try:
            say(f"Writing the nginx site and obtaining a certificate for {name}")
            secured = create_secured_site(
                name,
                manager=self.nginx,
                webserver="nginx",
                cert_manager=self.certs,
                template=TEMPLATE,
                port=port,
                context={"upstream_scheme": scheme, "allow_ips": networks},
            )
            self._record_site(state, secured)
            if not secured.ssl_enabled:
                raise CertificateError(
                    f"No certificate could be obtained for {name}",
                    details=(
                        f"{secured.certificate_error or 'certbot gave no reason'}\n"
                        f"Check that {name} points at this server and that ports 80 and 443 "
                        "reach it, then run the command again. Nothing of the console is "
                        "published without a certificate."
                    ),
                )
            say("Telling the console to believe the headers nginx sends")
            self._apply_settings(state, name)
            if self._console.trust_proxy(PROXY_PEER):
                state.proxy_added = PROXY_PEER
            self._exposures.save(state)
            say("Restarting the console" if restart == "now" else "Scheduling its restart")
            self._console.restart(restart)
            state.state = STATE_ACTIVE
            self._exposures.save(state)
            completed = True
        finally:
            if not completed:
                if before is not None:
                    # A run on top of a working exposure that fails leaves that
                    # exposure as it was; only a first run is undone.
                    self._exposures.save(before)
                else:
                    self._rollback(state, say)

        audit.record(
            "console.expose",
            target=f"console:{name}",
            details={
                "allow_ips": networks,
                "certificate_created": state.certificate_created,
                "forced_ens": bool(force_ens and not profile.console_exposure_allowed(self.config)),
            },
        )
        result = ExposureResult(
            domain=name,
            url=f"https://{name}",
            allow_ips=networks,
            site_created=state.site_created,
            certificate_created=state.certificate_created,
            restart=restart,
        )
        if not networks:
            result.notes.append(
                "Nothing restricts who may reach the sign-in page: it is protected by the second "
                "factor alone. Add --allow-ip with the addresses you connect from."
            )
        result.notes.append(
            "The console's access token (break-glass) still signs in here. Keep it offline, and "
            "make its own second factor mandatory: noust 2fa status."
        )
        if restart == "later":
            result.notes.append(
                f"The console restarts in about {RESTART_DELAY_SECONDS} seconds to apply this; "
                "open the page again after that."
            )
        return result

    def plan(
        self,
        domain: str,
        *,
        allow_ips: Sequence[str] = (),
        force_ens: bool = False,
        skip_dns_check: bool = False,
    ) -> list[str]:
        """
        Run every check an exposure runs, and say what it would do, changing nothing.

        This is what ``--dry-run`` and the console's preview show: the same
        refusals as :meth:`expose`, because they are the same checks.

        Args:
            domain: The name.
            allow_ips: Addresses and networks allowed to connect.
            force_ens: Publish although the ENS profile is on.
            skip_dns_check: Do not require the name to resolve to this machine.

        Returns:
            The steps, in order.

        Raises:
            RoleError: On a hub.
            SecurityError: As :meth:`expose`.
            DomainError: As :meth:`expose`.
            SiteError: As :meth:`expose`.
            ServiceError: As :meth:`expose`.
        """
        name, networks, _existing = self._check(
            domain, allow_ips, force_ens=force_ens, skip_dns_check=skip_dns_check
        )
        port, scheme = self._console.upstream()
        who = f"only {', '.join(networks)}" if networks else "anyone (no allow-list)"
        return [
            f"write the nginx site of {name}, proxying to {scheme}://127.0.0.1:{port}, "
            f"reachable by {who}",
            f"obtain a Let's Encrypt certificate for {name} and serve only HTTPS",
            f"set web.public_url to https://{name} and answer only that name (web.allowed_hosts)",
            f"make the console believe the headers of {PROXY_PEER} (--trusted-proxy) and restart it",
        ]

    def _check(
        self,
        domain: str,
        allow_ips: Sequence[str],
        *,
        force_ens: bool,
        skip_dns_check: bool,
    ) -> tuple[str, list[str], ExposureState | None]:
        """
        Refuse an exposure that is not safe or not possible, before anything is written.

        Args:
            domain: The name, as typed.
            allow_ips: The allow-list, as typed.
            force_ens: The operator confirmed the ENS override.
            skip_dns_check: Do not require the name to resolve to this machine.

        Returns:
            The name, the allow-list as networks, and the exposure already
            recorded for this name, if any.

        Raises:
            RoleError: On a hub.
            SecurityError: The profile forbids it, an account lacks a second
                factor, or the allow-list is bad.
            DomainError: The name is invalid, an application's, or not pointing here.
            SiteError: Another name is exposed, or a site Noust did not write is on this one.
            ServiceError: The console is not running as a service.
        """
        require_server_role("Console exposure")
        name = normalise_domain(domain)
        networks = parse_allow_ips(allow_ips)
        self._guard_profile(networks, force_ens)
        blockers = self._second_factor()
        if blockers:
            raise SecurityError(
                "The console is not published while an account can sign in without a second factor",
                details="\n".join(blockers),
            )
        if not self._console.is_service():
            raise ServiceError(
                "The console is not running as a service",
                details="Make it one first: noust web enable. A console started with "
                "'noust web start' cannot be published.",
            )
        existing = self._exposures.get()
        if existing is not None and existing.domain != name:
            raise SiteError(
                f"The console is already published at {existing.domain}",
                details="Remove that first: noust console unexpose",
            )
        owner = get_store().domain_owner(name)
        if owner is not None:
            raise DomainError(
                f"{name} belongs to the application {owner[0]}",
                details="The console needs a name of its own (console.example.com, say); an "
                "application's site is never edited to add it.",
            )
        if self.nginx.site_exists(name) and not self._is_ours(name):
            raise SiteError(
                f"{name} already has an nginx site Noust did not write for the console",
                details=f"Choose another name, or remove {self.nginx.config_path(name)} first.",
            )
        if not skip_dns_check:
            self._require_dns(name)
        return name, networks, existing

    def unexpose(
        self,
        *,
        restart: RestartMode = "now",
        progress: Progress | None = None,
    ) -> ExposureResult:
        """
        Take the console off its public name, removing exactly what exposing made.

        Args:
            restart: How to restart the console so it stops trusting nginx's headers.
            progress: Told each step as it starts.

        Returns:
            What was removed; ``removed`` is empty when nothing was exposed.

        Raises:
            RoleError: On a hub.
        """
        say = progress or (lambda _line: None)
        require_server_role("Console exposure")
        state = self._exposures.get()
        if state is None:
            return ExposureResult(domain="", notes=["The console was not published."])
        removed = self._rollback(state, say)
        result = ExposureResult(
            domain=state.domain,
            removed=removed,
            site_created=state.site_created,
            certificate_created=state.certificate_created,
            restart=restart,
        )
        if state.proxy_added is not None:
            say("Restarting the console" if restart == "now" else "Scheduling its restart")
            self._console.restart(restart)
        audit.record(
            "console.unexpose", target=f"console:{state.domain}", details={"removed": removed}
        )
        return result

    # -- Internals ---------------------------------------------------------

    def _guard_profile(self, networks: list[str], force_ens: bool) -> None:
        """
        Refuse under the ENS profile unless forced, and then only with an allow-list.

        Args:
            networks: The allow-list.
            force_ens: The operator confirmed the override.

        Raises:
            SecurityError: When the profile forbids it and it was not forced, or
                was forced without an allow-list.
        """
        if profile.console_exposure_allowed(self.config):
            return
        if not force_ens:
            raise SecurityError(
                "The ENS profile keeps the console off the network",
                details=(
                    "security.profile is ens-medium: the console is root-equivalent and is "
                    "reached through an SSH tunnel (ssh -L 8080:127.0.0.1:8080 root@server). "
                    "To publish it anyway, repeat the command with --force-ens and an "
                    "--allow-ip naming who may connect; the override is audited."
                ),
            )
        if not networks:
            raise SecurityError(
                "Under the ENS profile the console is published only with an allow-list",
                details="Add --allow-ip with the addresses or networks that may connect "
                "(mp.com.1, op.acc.4.5).",
            )

    def _require_dns(self, name: str) -> None:
        """
        Refuse a name that does not resolve to this machine.

        Args:
            name: The public name.

        Raises:
            DomainError: When it does not point here.
        """
        check = self._dns_check
        if check is None:
            from noust.deployers.domains import check_dns

            check = check_dns
        result = check(name)
        if result.points_here:
            return
        resolved = ", ".join(result.resolved_addresses) or "nothing"
        raise DomainError(
            f"{name} does not point at this server",
            details=(
                f"It resolves to {resolved}; this server answers on "
                f"{', '.join(result.expected_addresses) or 'no public address'}. Create the "
                "DNS record, wait for it to spread, and run the command again. On a server "
                "behind NAT, whose public address it cannot see, add --skip-dns-check."
            ),
        )

    def _is_ours(self, name: str) -> bool:
        """
        Tell whether the site on a name is a console site Noust wrote.

        Args:
            name: The name.

        Returns:
            True when the site starts with :data:`MARKER`.
        """
        text = self.nginx.get_site_config(name)
        return text is not None and text.lstrip().startswith(MARKER)

    def _record_site(self, state: ExposureState, secured: SecuredSite) -> None:
        """
        Note what the site writer created, so a failure after this undoes it.

        Args:
            state: The exposure being made.
            secured: What :func:`create_secured_site` did.
        """
        if not secured.site_existed:
            state.site_created = True
        if secured.ssl_enabled and not secured.certificate_reused:
            state.certificate_created = True
        self._exposures.save(state)

    def _apply_settings(self, state: ExposureState, name: str) -> None:
        """
        Point the console's own settings at the public name.

        ``web.public_url`` is what passkeys, notifications and links use as the
        console's origin; ``web.allowed_hosts`` makes it answer only that name
        (and loopback), which a name published beside others needs.

        Args:
            state: The exposure being made.
            name: The public name.

        Raises:
            SecurityError: When the configuration file cannot be written.
        """
        config = self.config
        config.set("web.public_url", f"https://{name}")
        hosts = _hosts(config.get("web.allowed_hosts")) or []
        if name not in hosts:
            config.set("web.allowed_hosts", [*hosts, name])
        try:
            config.write()
        except OSError as exc:
            raise SecurityError("The configuration could not be written", details=str(exc)) from exc

    def _rollback(self, state: ExposureState, say: Progress) -> list[str]:
        """
        Remove what an exposure made, as the record says, and forget it.

        Each step is attempted on its own: a failing one is reported and does
        not stop the others, because what is left behind is exactly what the
        operator cannot see.

        Args:
            state: The recorded exposure.
            say: Told each step.

        Returns:
            What was removed, in order.
        """
        removed: list[str] = []
        name = state.domain
        if state.proxy_added is not None:
            say("Removing the trusted proxy from the console's unit")
            try:
                if self._console.forget_proxy(state.proxy_added):
                    removed.append(f"trusted proxy {state.proxy_added} of the console's unit")
            except (NoustError, OSError) as exc:
                logger.warning("Could not remove the trusted proxy: %s", exc)
                removed.append(f"NOT removed: trusted proxy {state.proxy_added} ({exc})")
        removed += self._restore_settings(state)
        if state.site_created:
            say(f"Removing the nginx site of {name}")
            try:
                if self.nginx.site_exists(name) and self._is_ours(name):
                    self.nginx.disable_site(name)
                    self.nginx.delete_site(name)
                    self.nginx.reload()
                    removed.append(f"nginx site of {name}")
            except (NoustError, OSError) as exc:
                logger.warning("Could not remove the nginx site of %s: %s", name, exc)
                removed.append(f"NOT removed: nginx site of {name} ({exc})")
        if state.certificate_created:
            say(f"Removing the certificate of {name}")
            try:
                if self.certs.is_installed() and self.certs.cert_exists(name):
                    self.certs.delete(name)
                    removed.append(f"certificate of {name}")
            except (NoustError, OSError) as exc:
                logger.warning("Could not remove the certificate of %s: %s", name, exc)
                removed.append(f"NOT removed: certificate of {name} ({exc})")
        self._exposures.clear()
        return removed

    def _restore_settings(self, state: ExposureState) -> list[str]:
        """
        Put back the two settings an exposure changed, when they still hold its values.

        A setting the operator changed since is theirs now and is left alone.

        Args:
            state: The recorded exposure.

        Returns:
            What was put back.
        """
        config = self.config
        done: list[str] = []
        ours = f"https://{state.domain}"
        changed = False
        if _text(config.get("web.public_url")) == ours:
            config.set("web.public_url", state.previous_public_url or "")
            done.append("web.public_url")
            changed = True
        hosts = _hosts(config.get("web.allowed_hosts")) or []
        if state.domain in hosts and state.domain not in (state.previous_allowed_hosts or []):
            config.set("web.allowed_hosts", [h for h in hosts if h != state.domain])
            done.append("web.allowed_hosts")
            changed = True
        if changed:
            try:
                config.write()
            except OSError as exc:
                logger.warning("Could not write the configuration: %s", exc)
                return [f"NOT restored: {', '.join(done)} ({exc})"]
        return done


def _text(value: Any) -> str | None:
    """
    Read a configuration value as text.

    Args:
        value: What the configuration holds.

    Returns:
        The text, or None when it is empty.
    """
    text = str(value).strip() if value else ""
    return text or None


def _hosts(value: Any) -> list[str] | None:
    """
    Read ``web.allowed_hosts`` as a list.

    Args:
        value: What the configuration holds.

    Returns:
        The names, or None when it is unset or empty.
    """
    if isinstance(value, list):
        names = [str(item).strip().lower() for item in value if str(item).strip()]
    elif isinstance(value, str):
        names = [item.strip().lower() for item in value.split(",") if item.strip()]
    else:
        names = []
    return names or None
