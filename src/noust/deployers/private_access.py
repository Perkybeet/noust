# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that answers a private network and nobody else.

Publishing an application "only on the tailnet" or "only on the VPN" is not a new kind of site: it
is the site's address list set to the network's addresses. So it is one function over the one
implementation of a site's policy (:func:`noust.deployers.access.set_policy`), which already checks
the lists against the operator's own address, renders the site again, tests it with the web server
and puts everything back when the web server refuses. What this module adds is what belongs to the
idea of a private network and not to a list of addresses:

- **The answer to "is it private".** It is derived, never recorded: a site is private when its
  allowed addresses are not empty and every one of them is a range nobody on the public internet
  has an address in. Edit the list by hand and the answer follows, so there is no second source of
  truth to drift from the first.
- **The network by name.** ``--network tailscale`` or ``--network wg0`` instead of the ranges, from
  :mod:`noust.managers.private_networks`.
- **A certificate for the tailnet's own name.** Let's Encrypt cannot reach a name that only exists
  inside a tailnet (``web.tail1234.ts.net``), and Tailscale issues one for it
  (``tailscale cert``). The certificate is kept as an uploaded one named ``tailnet-<name>`` and
  served through the same path as any other upload (:func:`noust.deployers.certificates.use_uploaded`,
  which refuses it for an application that answers on a name the certificate does not cover), and
  it is renewed by the network upkeep before it expires.

What this does not do: it does not close the ports. The web server still listens on the public
address and answers a visitor from outside with 403; the firewall is where a port is closed, and
it is not Noust's place to close 443 for a server that serves other sites on it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.central import require_server_role
from noust.core.certificate_store import SOURCE_CUSTOM, CertificateStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import get_store
from noust.core.teams.model import ResourceNotFoundError
from noust.deployers import access, certificates, domains
from noust.deployers.helpers.site import certificate_names
from noust.managers.custom_certificates import CustomCertificates, covers, pem_blocks
from noust.managers.private_networks import (
    TAILSCALE,
    PrivateNetwork,
    PrivateNetworks,
    checked_cidrs,
    is_private_range,
)
from noust.validators.domain import validate_domain

logger = logging.getLogger(__name__)

#: What an uploaded certificate from Tailscale is called, so the upkeep finds it.
TAILNET_PREFIX = "tailnet-"

#: Days before expiry from which the upkeep asks Tailscale for the certificate again.
RENEW_WITHIN_DAYS = 30

#: Seconds Tailscale may take to issue a certificate (it asks the coordination server).
CERT_TIMEOUT = 120


@dataclass(frozen=True)
class PrivateStatus:
    """
    Whether an application answers a private network only, and which.

    Attributes:
        app: The application's primary domain.
        private: Its allowed addresses are not empty and all of them are private ranges.
        networks: The networks this server is on whose ranges are all allowed.
        allow_ips: The addresses and networks allowed.
        public_ranges: Allowed entries that are not private; while there are any, the site is
            not private, whatever else the list holds.
        applicable: Whether the application's site takes a policy at all.
        reason: Why not.
        available: The private networks this server is on.
        tailnet_name: What the tailnet calls this server, when it is on one.
        tailnet_certificate: The uploaded certificate the application is served with from
            Tailscale, when it is.
    """

    app: str
    private: bool
    networks: tuple[str, ...]
    allow_ips: tuple[str, ...]
    public_ranges: tuple[str, ...]
    applicable: bool
    reason: str
    available: tuple[PrivateNetwork, ...]
    tailnet_name: str = ""
    tailnet_certificate: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "app": self.app,
            "private": self.private,
            "networks": list(self.networks),
            "allow_ips": list(self.allow_ips),
            "public_ranges": list(self.public_ranges),
            "applicable": self.applicable,
            "reason": self.reason,
            "available": [network.to_dict() for network in self.available],
            "tailnet_name": self.tailnet_name,
            "tailnet_certificate": self.tailnet_certificate,
        }


@dataclass(frozen=True)
class TailnetCertificate:
    """
    A certificate Tailscale issued for this server's name.

    Attributes:
        name: The uploaded certificate's name.
        hostname: The name it is for.
        not_after: When it expires.
        applications: The applications that serve it.
    """

    name: str
    hostname: str
    not_after: str
    applications: tuple[str, ...]


def networks(runner: CommandRunner | None = None) -> list[PrivateNetwork]:
    """
    Args:
        runner: What the programs run through; the process-wide runner by default.

    Returns:
        The private networks this server is on.
    """
    return PrivateNetworks(runner).discover()


def status(app_domain: str, *, verbose: bool = False) -> PrivateStatus:
    """
    Say whether an application answers a private network only.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        The answer, with the networks it could be published to.

    Raises:
        NoustError: When the application is unknown.
    """
    view = access.describe(app_domain, verbose=verbose)
    allowed = view.policy.allow_ips
    public = tuple(entry for entry in allowed if not is_private_range(entry))
    available = tuple(networks())
    ranges = set(allowed)
    on = tuple(
        network.name for network in available if network.cidrs and set(network.cidrs) <= ranges
    )
    tailnet = next((network for network in available if network.name == TAILSCALE), None)
    name = tailnet.dns_name if tailnet is not None else ""
    return PrivateStatus(
        app=view.app,
        private=bool(allowed) and not public,
        networks=on,
        allow_ips=tuple(allowed),
        public_ranges=public,
        applicable=view.applicable,
        reason=view.reason,
        available=available,
        tailnet_name=name,
        tailnet_certificate=_served_tailnet_certificate(view.app),
    )


def enable(
    app_domain: str,
    *,
    network: str | None = None,
    cidrs: Sequence[str] = (),
    operator_addresses: Sequence[str] = (),
    accept_lockout: bool = False,
    verbose: bool = False,
) -> PrivateStatus:
    """
    Make an application answer a private network only.

    The allowed addresses become the network's ranges, plus the ranges named, plus the private
    ranges the list already had; the public ones it had go. Nothing else about the policy changes.

    Args:
        app_domain: The application's primary domain.
        network: A network of :func:`networks` (``tailscale``, ``wg0``).
        cidrs: More private ranges to allow.
        operator_addresses: The addresses the operator is making the change from, so a list that
            refuses them is caught.
        accept_lockout: Go ahead although the list refuses the operator's own address.
        verbose: Verbosity of the deployer.

    Returns:
        The status afterwards.

    Raises:
        ValidationError: Nothing was named, the network is down, a range is public, the lists
            would refuse the operator, or the site cannot carry a policy.
        ResourceNotFoundError: This server is not on that network.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    wanted: list[str] = []
    if network is not None:
        found = PrivateNetworks().get(network)
        if not found.up or not found.cidrs:
            raise ValidationError(
                f"{network} is not usable: {found.problem or 'it has no addresses'}",
                details="Bring the network up on this server first, or name its ranges with --cidr.",
                field="network",
            )
        wanted.extend(found.cidrs)
    wanted.extend(checked_cidrs(list(cidrs)))
    if not wanted:
        raise ValidationError(
            "Name a network or a range",
            details="For example --network tailscale, or --cidr 10.8.0.0/24.",
            field="network",
        )
    current = access.describe(domain, verbose=verbose).policy.allow_ips
    kept = [entry for entry in current if is_private_range(entry)]
    merged = list(dict.fromkeys([*wanted, *kept]))
    access.set_policy(
        domain,
        {"allow_ips": merged},
        operator_addresses=operator_addresses,
        accept_lockout=accept_lockout,
        verbose=verbose,
    )
    return status(domain, verbose=verbose)


def disable(
    app_domain: str,
    *,
    operator_addresses: Sequence[str] = (),
    accept_lockout: bool = False,
    verbose: bool = False,
) -> PrivateStatus:
    """
    Let an application answer anybody again.

    Every private range comes off the allowed addresses; a public one somebody put there stays,
    because it is not what this undoes.

    Args:
        app_domain: The application's primary domain.
        operator_addresses: The addresses the operator is making the change from.
        accept_lockout: Go ahead although the list refuses the operator's own address.
        verbose: Verbosity of the deployer.

    Returns:
        The status afterwards.

    Raises:
        ValidationError: The site cannot carry a policy, or the lists would refuse the operator.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    current = access.describe(domain, verbose=verbose).policy.allow_ips
    left = [entry for entry in current if not is_private_range(entry)]
    if len(left) != len(current):
        access.set_policy(
            domain,
            {"allow_ips": left},
            operator_addresses=operator_addresses,
            accept_lockout=accept_lockout,
            verbose=verbose,
        )
    return status(domain, verbose=verbose)


# -- the tailnet's own certificate ---------------------------------------------------


def certificate_name(hostname: str) -> str:
    """
    Args:
        hostname: A name of the tailnet.

    Returns:
        What its uploaded certificate is called.
    """
    return f"{TAILNET_PREFIX}{hostname}"


def tailnet_certificate(app_domain: str, *, verbose: bool = False) -> TailnetCertificate:
    """
    Serve an application with the certificate Tailscale issues for this server's name.

    The application must answer on that name and nothing else (an application that also answers
    on a public name would have it refused by the browser), which is checked before Tailscale is
    asked. Tailscale's issued pair is kept as an uploaded certificate, and the application is
    given it.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        The certificate that is now served.

    Raises:
        ValidationError: This server is not on a tailnet, the tailnet cannot issue certificates
            (HTTPS is off for it), or the application answers on a name the certificate
            does not cover.
        CertificateError: Tailscale refused, or did not hand back a pair.
    """
    require_server_role("Certificates")
    domain = validate_domain(app_domain)
    try:
        tailnet: PrivateNetwork | None = PrivateNetworks().get(TAILSCALE)
    except ResourceNotFoundError:
        tailnet = None
    if tailnet is None or not tailnet.up or not tailnet.dns_name:
        raise ValidationError(
            "This server is not connected to a tailnet",
            details="Join one first: tailscale up",
            field="network",
        )
    hostname = tailnet.dns_name
    if hostname not in tailnet.cert_domains:
        raise ValidationError(
            "The tailnet does not issue certificates for this server",
            details="Turn on HTTPS certificates for the tailnet in Tailscale's admin console "
            "(DNS, HTTPS Certificates), and MagicDNS with it.",
            field="network",
        )
    app = domains.application(domain)
    others = [
        name for name in certificate_names(get_store(), app.domain) if not covers((hostname,), name)
    ]
    if others:
        raise ValidationError(
            f"{app.domain} also answers on {', '.join(others)}, which a tailnet certificate "
            f"cannot cover",
            details=f"Make {hostname} the application's only name, or keep its own certificate.",
            field="domain",
        )
    name = certificate_name(hostname)
    if not is_rehearsal():
        _issue_and_keep(name, hostname)
    certificates.use_uploaded(app.domain, name, verbose=verbose)
    return _describe(name, hostname)


def renew_tailnet_certificates(*, now: datetime | None = None) -> list[str]:
    """
    Ask Tailscale for the certificates that are about to expire again, and serve them.

    Args:
        now: The current time; the clock by default.

    Returns:
        The names of the certificates that were renewed.

    Raises:
        NoustError: Tailscale or the web server refused one; the ones before it were renewed.
    """
    renewed: list[str] = []
    for record in CustomCertificates().uploaded():
        if not record.name.startswith(TAILNET_PREFIX) or not _expiring(record.not_after, now):
            continue
        hostname = record.name.removeprefix(TAILNET_PREFIX)
        _issue_and_keep(record.name, hostname)
        certificates.refresh_sites_serving(record.name)
        renewed.append(record.name)
    return renewed


def renewal_due(now: datetime | None = None) -> bool:
    """
    Args:
        now: The current time; the clock by default.

    Returns:
        Whether a Tailscale certificate is within :data:`RENEW_WITHIN_DAYS` of expiring.
    """
    return any(
        record.name.startswith(TAILNET_PREFIX) and _expiring(record.not_after, now)
        for record in CustomCertificates().uploaded()
    )


def _expiring(not_after: str | None, now: datetime | None) -> bool:
    """
    Args:
        not_after: An ISO timestamp, or None when the record has none.
        now: The current time; the clock by default.

    Returns:
        Whether it is less than :data:`RENEW_WITHIN_DAYS` away (or past, or unreadable).
    """
    if not_after is None:
        return True
    try:
        expires = datetime.fromisoformat(not_after)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    return (expires - moment).days < RENEW_WITHIN_DAYS


def _issue_and_keep(name: str, hostname: str) -> None:
    """
    Ask Tailscale for the certificate and keep what it hands back.

    Tailscale writes the certificate and the key to standard output when both are named ``-``;
    nothing is put in a file of ours but the store's own, with the key mode it gives.

    Args:
        name: What the uploaded certificate is called.
        hostname: The name to ask for.

    Raises:
        CertificateError: Tailscale refused, or the answer holds no pair.
    """
    result = get_runner().run(
        [
            "tailscale",
            "cert",
            "--cert-file",
            "-",
            "--key-file",
            "-",
            "--min-validity",
            f"{RENEW_WITHIN_DAYS * 24}h",
            hostname,
        ],
        timeout=CERT_TIMEOUT,
    )
    blocks = pem_blocks(result.stdout)
    certificate = "".join(block for label, block in blocks if label == "CERTIFICATE")
    key = next((block for label, block in blocks if label.endswith("PRIVATE KEY")), "")
    if not result.success or not certificate or not key:
        raise CertificateError(
            f"Tailscale did not issue a certificate for {hostname}",
            details="Check that HTTPS certificates are on for the tailnet and that the name is "
            "this machine's: tailscale cert needs them.",
            output=(result.stderr or "").strip()[:500] or None,
        )
    CustomCertificates().upload(name, certificate, key, uploaded_by="tailscale")


def _served_tailnet_certificate(app_domain: str) -> str:
    """
    Args:
        app_domain: An application.

    Returns:
        The name of the Tailscale certificate it is served with, or an empty string.
    """
    settings = CertificateStore().get_settings(app_domain)
    if settings.source == SOURCE_CUSTOM and (settings.name or "").startswith(TAILNET_PREFIX):
        return settings.name or ""
    return ""


def _describe(name: str, hostname: str) -> TailnetCertificate:
    """
    Args:
        name: An uploaded certificate.
        hostname: The name it is for.

    Returns:
        What it is and who serves it.
    """
    manager = CustomCertificates()
    record = manager.get(name)
    return TailnetCertificate(
        name=name,
        hostname=hostname,
        not_after=record.not_after or "",
        applications=tuple(manager.records.domains_using("custom", name)),
    )
