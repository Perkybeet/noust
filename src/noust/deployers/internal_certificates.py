# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application served with a certificate its server's own authority signed.

The authority and what it signs are :mod:`noust.managers.internal_ca`; this is what puts a
certificate on an application: every name the application answers on is on one certificate, kept as
an uploaded one named ``internal-<application>`` and served through the one path that serves an
upload (:func:`noust.deployers.certificates.use_uploaded`, which refuses a certificate that does not
cover a name and puts the choice back when the web server refuses the site).

And the upkeep: the certificates are made for ninety days and made again thirty days before, by the
network maintenance pass, with the sites that serve them reloaded. The authority is not made again
by anything but the operator.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.central import require_server_role
from noust.core.exceptions import CertificateError, NoustError
from noust.core.store import get_store
from noust.core.utils import domain_to_app_name
from noust.deployers import certificates, domains
from noust.deployers.helpers.site import certificate_names
from noust.managers.internal_ca import LEAF_DAYS, InternalCA
from noust.validators.domain import validate_domain

__all__ = ["Served", "renew", "renew_due", "renewal_due", "serve"]


@dataclass(frozen=True)
class Served:
    """
    What was done for an application.

    Attributes:
        app: Its primary domain.
        certificate: The upload it serves now.
        names: Every name on the certificate.
        not_after: When it stops being valid.
        view: The application's certificate afterwards, as ``noust cert source`` shows it.
    """

    app: str
    certificate: str
    names: tuple[str, ...]
    not_after: str
    view: certificates.CertificateView


def serve(app_domain: str, *, days: int = LEAF_DAYS, verbose: bool = False) -> Served:
    """
    Sign a certificate for every name an application answers on, and serve it.

    Args:
        app_domain: The application's primary domain.
        days: How long the certificate is valid.
        verbose: Verbosity of the deployer.

    Returns:
        The certificate that is now served.

    Raises:
        NoustError: When the application is unknown.
        CertificateError: This server has no authority.
        ValidationError: A name the application answers on is outside what the authority signs
            for (the certificate is not made), or the web server refuses the new site (the
            choice is put back).
    """
    require_server_role("Certificates")
    domain = validate_domain(app_domain)
    app = domains.application(domain)
    names = certificate_names(get_store(), app.domain)
    record = InternalCA().issue(domain_to_app_name(app.domain), names, days=days)
    change = certificates.use_uploaded(app.domain, record.name, verbose=verbose)
    return Served(
        app=app.domain,
        certificate=record.name,
        names=tuple(record.names),
        not_after=record.not_after or "",
        view=change.view,
    )


def renewal_due() -> bool:
    """
    Returns:
        Whether a certificate the authority signed is within its renewal window. False on a
        server with no authority.
    """
    authority = InternalCA()
    return authority.exists() and bool(authority.renew_due())


def renew(name: str) -> str:
    """
    Sign one certificate again, and reload the sites that serve it.

    Args:
        name: The certificate's upload name (``internal-wiki``).

    Returns:
        The name, once renewed.

    Raises:
        CertificateError: There is no authority, the certificate was not signed by it, or a site
            could not be reloaded (the certificate is already replaced then).
        ValidationError: A name on it is no longer one the authority signs for.
    """
    InternalCA().renew(name)
    certificates.refresh_sites_serving(name)
    return name


def renew_due() -> list[str]:
    """
    Sign again the certificates that are about to expire, and reload the sites that serve them.

    One that cannot be renewed (a name it carries is no longer one the authority signs for) does
    not keep the others from it: they are all tried, and the failures are reported together.

    Returns:
        The names of the certificates that were renewed.

    Raises:
        CertificateError: One or more could not be renewed or their sites reloaded; the message
            says which, and which were renewed.
    """
    authority = InternalCA()
    if not authority.exists():
        return []
    renewed: list[str] = []
    failed: dict[str, str] = {}
    for item in authority.renew_due():
        try:
            renewed.append(renew(item.name))
        except NoustError as exc:
            failed[item.name] = exc.message
    if failed:
        raise CertificateError(
            "Not renewed: " + "; ".join(f"{name} ({why})" for name, why in failed.items()),
            details=("Renewed: " + ", ".join(renewed) + ". " if renewed else "")
            + "Look at the authority with: noust cert ca list",
        )
    return renewed
