# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one implementation of "where an application's certificate comes from".

``noust cert use`` and ``/api/apps/{domain}/certificate`` are presentation over the
functions here. An application's certificate is, in turn, one of:

- **ordered** from the authority :mod:`noust.managers.acme` names, proven with the
  HTTP-01 challenge over the application's own port 80 or with DNS-01 through a DNS
  account (needed for a wildcard, for a host nobody can reach, and for a server whose
  port 80 is closed);
- **uploaded** (:mod:`noust.managers.custom_certificates`): a Cloudflare Origin
  Certificate, a corporate CA's, a purchased one.

A change goes through the same steps, in this order, and puts everything back if any of
them fails: the choice is kept in the store; the certificate is ordered (or the upload's
names checked against the application's); the site is rendered again with TLS and tested
by the web server before it is reloaded. The certificate that served before keeps
serving until the new one is in place - a failed order never takes TLS away.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.certificate_store import (
    CHALLENGE_DNS,
    CHALLENGE_HTTP,
    SOURCE_ACME,
    SOURCE_CUSTOM,
    CertificateSettings,
    CertificateStore,
)
from noust.core.dns_store import DnsStore
from noust.core.exceptions import CertificateError, NoustError, ValidationError
from noust.core.store import App, get_store
from noust.deployers import domains
from noust.deployers.helpers.site import certificate_names
from noust.managers.acme import AcmeSettings
from noust.managers.cert_manager import CertManager
from noust.managers.custom_certificates import UploadNotFoundError, covers
from noust.validators.domain import validate_domain

#: Types whose certificate is ordered by their deploy, for names only it knows.
UNSUPPORTED_TYPES: frozenset[str] = frozenset({"monorepo"})

_APPLY_ERRORS = (NoustError, OSError)


@dataclass(frozen=True)
class CertificateView:
    """
    How an application's certificate comes to be, and what it is now.

    Attributes:
        app: The application's primary domain.
        source: ``acme`` or ``custom``; ``lineage`` for one served with another's.
        name: The upload or the lineage, for those sources.
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account that answers DNS-01.
        authority: How the authority that orders is called.
        tls: Whether the site serves TLS now.
        names: The names the application answers on.
        certificate: What is served: its ``names``, ``issuer``, ``expires_on`` and
            ``days_remaining``; None when no certificate is on disk.
    """

    app: str
    source: str
    name: str | None
    challenge: str
    dns_provider: str | None
    authority: str
    tls: bool
    names: tuple[str, ...]
    certificate: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "app": self.app,
            "source": self.source,
            "name": self.name,
            "challenge": self.challenge,
            "dns_provider": self.dns_provider,
            "authority": self.authority,
            "tls": self.tls,
            "names": list(self.names),
            "certificate": self.certificate,
        }


@dataclass(frozen=True)
class CertificateChange:
    """
    What a change of certificate did.

    Attributes:
        view: The application's certificate afterwards.
        ordered: Whether a certificate was ordered.
    """

    view: CertificateView
    ordered: bool = False


def describe(app_domain: str, *, verbose: bool = False) -> CertificateView:
    """
    Read how an application's certificate comes to be, and what it is.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the managers.

    Returns:
        The description.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(app_domain)
    manager = CertManager(verbose=verbose)
    settings = manager.certificate_settings(app.domain)
    info = manager.get_cert_info(app.domain)
    certificate: dict[str, Any] | None = None
    if info is not None:
        days = None
        if info.expiry:
            days = (datetime.strptime(info.expiry, "%Y-%m-%d").date() - date.today()).days
        certificate = {
            "name": info.name,
            "names": list(info.domains),
            "issuer": info.issuer,
            "expires_on": info.expiry,
            "days_remaining": days,
            "source": info.source,
        }
    return CertificateView(
        app=app.domain,
        source=settings.source,
        name=settings.name,
        challenge=settings.challenge,
        dns_provider=settings.dns_provider,
        authority=AcmeSettings().authority().label,
        tls=_serves_tls(app, manager),
        names=tuple(certificate_names(get_store(), app.domain)),
        certificate=certificate,
    )


def use_acme(
    app_domain: str,
    *,
    challenge: str = CHALLENGE_HTTP,
    dns_provider: str | None = None,
    order: bool = True,
    verbose: bool = False,
) -> CertificateChange:
    """
    Order the application's certificate from the authority, proven the way asked.

    Changing how a certificate that exists is proven orders it again: certbot keeps the
    method in the lineage's renewal configuration, and renewals would go on using the
    old one. That spends the authority's rate limit once.

    Args:
        app_domain: The application's primary domain.
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account that answers DNS-01 (required for it).
        order: Order the certificate now, when the site already serves TLS. False
            only keeps the choice, for the next order to use.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: The challenge is not one, the account does not exist, or the
            application's type orders its certificate itself.
        CertificateError: The order failed; the choice is put back and the certificate
            that served keeps serving.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    check_choice(challenge, dns_provider)
    with app_lock(validate_domain(app_domain), "certificate change"):
        return _use_acme(
            app_domain, challenge=challenge, dns_provider=dns_provider, order=order, verbose=verbose
        )


def check_choice(challenge: str, dns_provider: str | None) -> None:
    """
    Refuse a way of proving control that cannot work, before anything is queued or changed.

    Args:
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account that answers DNS-01.

    Raises:
        ValidationError: The challenge is not one, or DNS-01 has no account, or the
            account does not exist.
    """
    if challenge not in (CHALLENGE_HTTP, CHALLENGE_DNS):
        raise ValidationError("The challenge is http-01 or dns-01", field="challenge")
    if challenge != CHALLENGE_DNS:
        return
    if not dns_provider:
        raise ValidationError(
            "The DNS challenge needs a DNS account",
            details="Add one: noust dns provider add NAME --kind cloudflare",
            field="dns_provider",
        )
    if DnsStore().get_account(dns_provider) is None:
        raise ValidationError(
            f"There is no DNS account called {dns_provider}",
            details="List them with: noust dns provider list",
            field="dns_provider",
        )


def _use_acme(
    app_domain: str, *, challenge: str, dns_provider: str | None, order: bool, verbose: bool
) -> CertificateChange:
    """:func:`use_acme`, under the application's lock."""
    app = domains.application(app_domain)
    _require_supported(app)
    if challenge != CHALLENGE_DNS:
        dns_provider = None
    store = CertificateStore()
    before = store.get_settings(app.domain)
    after = CertificateSettings(
        domain=app.domain,
        source=SOURCE_ACME,
        challenge=challenge,
        dns_provider=dns_provider,
        updated_by=_actor(),
    )
    # Ordering again is needed when the way it is proven or the place it comes from
    # changes under a certificate that is already there.
    changed = (before.source, before.challenge, before.dns_provider) != (
        after.source,
        after.challenge,
        after.dns_provider,
    )
    deployer = domains.site_deployer(app, verbose=verbose)
    tls = domains.serves_tls(app, deployer) or (
        before.source == SOURCE_CUSTOM and deployer.has_certificate()
    )
    ordered = False
    store.set_settings(after)
    try:
        if tls and order and changed:
            deployer.obtain_certificate(
                force=deployer.has_certificate() and before.source == SOURCE_ACME
            )
            deployer.refresh_site(with_ssl=True)
            ordered = True
    except _APPLY_ERRORS:
        store.set_settings(before)
        raise
    audit.record(
        "certs.assign",
        target=f"app:{app.domain}",
        details={
            "source": SOURCE_ACME,
            "challenge": challenge,
            "dns_provider": dns_provider,
            "ordered": ordered,
        },
    )
    return CertificateChange(describe(app.domain, verbose=verbose), ordered)


def serve_ordered(app_domain: str, *, verbose: bool = False) -> bool:
    """
    Write an application's site with the certificate that was just ordered for its name.

    ``noust cert create`` and the console's "Issue certificate" order a certificate and record
    that the site serves TLS. certbot's nginx plugin edits the site while it proves an HTTP
    order; a DNS order has no installer, so the site stayed on port 80 while Noust said it served
    TLS, and the advice after a deploy whose certificate failed (noust cert create) did not
    deliver. This renders the site the way a deploy would.

    Args:
        app_domain: The name the certificate was ordered for.
        verbose: Verbosity of the managers.

    Returns:
        True when the site was rendered with TLS; False when the name is not an application's
        primary domain, its type orders its certificate itself, or it records no TLS or has no
        certificate on disk (an order for a name Noust does not host).

    Raises:
        ValidationError: The web server refused the new site (the old file is back).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    app = get_store().get_app(validate_domain(app_domain))
    if app is None or app.app_type in UNSUPPORTED_TYPES:
        return False
    with app_lock(app.domain, "certificate change"):
        deployer = domains.site_deployer(app, verbose=verbose)
        if not domains.serves_tls(app, deployer):
            return False
        deployer.refresh_site(with_ssl=True)
    return True


def use_uploaded(app_domain: str, name: str, *, verbose: bool = False) -> CertificateChange:
    """
    Serve an application with a certificate that was uploaded.

    Nothing is ordered, renewed or revoked for it. Every name the application answers
    on must be covered, or the browser warns on the one that is not; the names are
    checked before anything is changed.

    Args:
        app_domain: The application's primary domain.
        name: The uploaded certificate.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        CertificateError: When nothing was uploaded under that name.
        ValidationError: The certificate does not cover every name, or the web server
            refuses the new site (the choice is put back).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    with app_lock(validate_domain(app_domain), "certificate change"):
        app = domains.application(app_domain)
        _require_supported(app)
        store = CertificateStore()
        record = store.get_custom(name)
        if record is None:
            raise UploadNotFoundError(
                f"No uploaded certificate is called {name}",
                details="Upload one: noust cert upload NAME --cert FILE --key FILE",
            )
        wanted = certificate_names(get_store(), app.domain)
        missing = [host for host in wanted if not covers(record.names, host)]
        if missing:
            raise ValidationError(
                f"{name} does not cover {', '.join(missing)}",
                details="It covers " + (", ".join(record.names) or "nothing") + ".",
                field="name",
            )
        before = store.get_settings(app.domain)
        store.set_settings(
            replace(
                before,
                source=SOURCE_CUSTOM,
                name=name,
                challenge=CHALLENGE_HTTP,
                dns_provider=None,
                updated_by=_actor(),
            )
        )
        deployer = domains.site_deployer(app, verbose=verbose)
        try:
            deployer.obtain_certificate()
            deployer.refresh_site(with_ssl=True)
        except _APPLY_ERRORS:
            store.set_settings(before)
            raise
        audit.record(
            "certs.assign",
            target=f"app:{app.domain}",
            details={"source": SOURCE_CUSTOM, "name": name, "fingerprint": record.fingerprint},
        )
        return CertificateChange(describe(app.domain, verbose=verbose))


def refresh_sites_serving(name: str) -> list[str]:
    """
    Reload the site of every application that serves an uploaded certificate whose files were
    just replaced (a renewal), so the web server serves the new one and not the one in memory.

    Every site is tried: one that cannot be rendered now (the web server refuses it, another
    operation holds the application) does not leave the others on the old certificate.

    Args:
        name: The uploaded certificate.

    Returns:
        The applications whose site was reloaded.

    Raises:
        CertificateError: A site could not be reloaded. The certificate is already replaced on
            disk, so the message says which sites still serve the old one.
    """
    refreshed: list[str] = []
    failed: dict[str, str] = {}
    for domain in CertificateStore().domains_using(SOURCE_CUSTOM, name):
        try:
            app = domains.application(domain)
            domains.site_deployer(app).refresh_site(with_ssl=True)
        except _APPLY_ERRORS as exc:
            failed[domain] = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
        else:
            refreshed.append(domain)
    if failed:
        raise CertificateError(
            f"{name} was renewed but {', '.join(failed)} could not be reloaded with it",
            details="; ".join(f"{domain}: {why}" for domain, why in failed.items())
            + f". They keep serving the old certificate until: noust cert use {next(iter(failed))} "
            f"--upload {name}",
        )
    return refreshed


def _serves_tls(app: App, manager: CertManager) -> bool:
    """
    Args:
        app: The application.
        manager: The certificate manager.

    Returns:
        Whether its site is recorded as serving TLS and the certificate it names is on disk.
    """
    site = get_store().get_site(app.domain)
    recorded = bool(site.ssl_enabled if site is not None else app.ssl_enabled)
    return recorded and manager.cert_exists(app.domain)


def _require_supported(app: App) -> None:
    """
    Refuse an application whose certificate is not the domain rows' to order.

    Args:
        app: The application.

    Raises:
        ValidationError: Its type orders its certificate itself.
    """
    if app.app_type in UNSUPPORTED_TYPES:
        raise ValidationError(
            f"The certificate of {app.domain} is ordered by its deploy",
            details="A monorepo's names include each workspace's subdomain, which only its "
            f"deploy knows. Redeploy it to order the certificate: noust update {app.domain}",
        )


def _actor() -> str | None:
    """
    Returns:
        A label for who is acting, for the records' ``updated_by``.
    """
    actor = audit.current_actor()
    return None if actor is None else str(actor.name or actor.id or actor.kind)
