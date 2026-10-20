# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one implementation of "the names an application answers on".

``noust domain`` and ``/api/apps/{domain}/domains`` are presentation over the
functions here. Each change goes through the same four places, in this order:

1. **The store**, which refuses a name that is not a domain, that belongs to
   another application, or that would be a second primary
   (:meth:`noust.core.store.NoustStore.add_domain`).
2. **The site**, rendered again by the application's own deployer
   (``refresh_site`` of :class:`~noust.deployers.helpers.site.ServesDomains`:
   ``BaseDeployer``, a Docker Compose stack, a monorepo), exactly as a deploy
   renders it. The web server reads the names from the store where it writes
   the file, so a redeploy later renders the same names again. The whole
   configuration is tested before the reload, and a change the web server
   refuses is put back - the row and the file both.
3. **The certificate**, expanded under the same lineage to cover every name,
   redirects included (:meth:`noust.managers.cert_manager.CertManager.obtain`
   reads the same rows). Only for an application that already serves TLS. A
   removed name is dropped from it too: a certificate renews with every name it
   has, so one that no longer points here would fail the renewal of the others.
4. **The DNS record**, when a DNS account holds the zone and creates records
   without asking (:mod:`noust.managers.dns.records`): the name points here as soon
   as it is added, and is released when the last application using it lets go.
5. **Nothing else.** The unit, the build and the release are not touched.

A name another site already answers on, or a wildcard that would take over names,
is a conflict the operator confirms (``force``), not an accident: the web server
settles two sites claiming one name by file order and a warning nobody reads.

Applications deployed before domains had rows get only their primary from the
store migration, which may not read the web server's files. The first change
made here adopts, as aliases, whatever else the live site answered on - the
``www`` a 1.x ``--www`` deploy served - so adding one name never silently
drops another.

:func:`check_dns` answers the question to ask before a certificate is ordered:
does this name resolve to this machine?
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from noust.central import require_server_role
from noust.core import audit, cdn_networks
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import (
    CertificateError,
    DependencyError,
    DeploymentError,
    DomainConflictError,
    DomainError,
    DomainOverlapError,
    NoustError,
    ValidationError,
)
from noust.core.logger import Logger
from noust.core.store import App, DomainKind, DomainRecord, get_store
from noust.deployers.helpers.layout import app_root
from noust.deployers.helpers.site import ServesDomains, is_operator_site
from noust.deployers.registry import get_deployer
from noust.managers import trusted_proxies
from noust.managers.dns.provider import is_public_address
from noust.managers.dns.resolver import Transport
from noust.managers.dns.resolver import resolve as dns_resolve
from noust.managers.domain_overlaps import find_overlaps
from noust.managers.site_policy import (
    SitePolicyManager,
    canonical_host,
    check_canonical_possible,
    counterpart,
)
from noust.validators.domain import is_wildcard, validate_domain, validate_server_name

_log = logging.getLogger(__name__)

#: What can fail while a change is applied, and is undone before it is raised.
_APPLY_ERRORS = (NoustError, OSError, sqlite3.Error)

#: The kinds a name can be added as. The primary is the application itself.
ADDABLE_KINDS: tuple[str, ...] = (DomainKind.ALIAS.value, DomainKind.REDIRECT.value)

#: Resolves a host the way :func:`socket.getaddrinfo` does.
Resolver = Callable[..., Sequence[tuple[Any, ...]]]

#: IP address, either family.
_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class DomainChange:
    """
    What a change to an application's domains did.

    Attributes:
        app: The application's primary domain.
        domains: Every domain it answers on afterwards, primary first.
        tls: Whether its site serves TLS. A name added to a site that does
            not is served over plain HTTP, like the rest of it.
        adopted: Names the live site already answered on that had no row,
            recorded as aliases before the change (see the module docs).
        certificate_issued: Whether the certificate was confirmed to cover
            every domain, ordering an expansion when it did not.
        certificate_error: Why it was not, in certbot's own words, when an
            order was attempted and failed. The domain stays; the site keeps
            serving TLS with the certificate it had.
        certificate_reduced: Whether the certificate was ordered again without
            the name that was removed.
        dns: What happened to the DNS records of the name: one entry per record,
            ``{"hostname", "type", "outcome"}`` (or ``{"error"}``).
        canonical: The spelling served (``www``, ``apex``), when one is chosen.
    """

    app: str
    domains: tuple[DomainRecord, ...]
    tls: bool
    adopted: tuple[str, ...] = ()
    certificate_issued: bool = False
    certificate_error: str | None = None
    certificate_reduced: bool = False
    dns: tuple[dict[str, Any], ...] = ()
    canonical: str = ""


@dataclass(frozen=True)
class DnsCheck:
    """
    Whether a name resolves to this machine.

    Attributes:
        domain: The name that was resolved.
        expected_addresses: This machine's addresses: every address on a
            non-loopback interface, sorted.
        resolved_addresses: What the name resolves to (A and AAAA), sorted.
            Empty when it does not resolve.
        points_here: True when it resolves and every address it resolves to
            is one of this machine's, or one of a CDN that forwards to it. An
            AAAA record pointing elsewhere makes it False: Let's Encrypt
            prefers IPv6, and so do most browsers.
        verdict: ``here``, ``cdn`` (every address is one CDN's - Cloudflare, Fastly, Bunny or
            ArvanCloud: it proxies the name and reaches this server on its own), ``elsewhere``,
            ``none`` (it does not resolve) or ``unknown`` (this server only
            sees private addresses, so it cannot say what its public one is).
        cdn: The CDN every address belongs to, when ``verdict`` is ``cdn``.
        resolver: The resolver that answered; empty for the machine's own.
    """

    domain: str
    expected_addresses: tuple[str, ...]
    resolved_addresses: tuple[str, ...]
    points_here: bool
    verdict: str = ""
    cdn: str | None = None
    resolver: str = ""


def list_domains(app_domain: str) -> list[DomainRecord]:
    """
    List the domains an application answers on.

    Args:
        app_domain: The application's primary domain.

    Returns:
        Its domains, primary first, then aliases, then redirects.

    Raises:
        NoustError: When no application is deployed at ``app_domain``.
    """
    app = _application(app_domain)
    return get_store().list_domains(app.domain)


def add_domain(
    app_domain: str,
    domain: str,
    kind: str = DomainKind.ALIAS.value,
    *,
    issue_cert: bool = True,
    force: bool = False,
    dns: bool = True,
    port: int | None = None,
    logger: Logger | None = None,
    verbose: bool = False,
) -> DomainChange:
    """
    Make an application answer on another name.

    Args:
        app_domain: The application's primary domain.
        domain: The name to add; ``*.example.com`` for an alias that takes every
            name one label under it (its certificate needs the DNS challenge).
        kind: ``alias`` to serve the application on it, ``redirect`` to send
            it permanently to the primary.
        issue_cert: Expand the certificate to cover the name, when the site
            serves TLS. False leaves the certificate alone: the name is served
            with a certificate that does not cover it until
            :func:`issue_certificate` runs, or this is called again.
        force: Add the name although another site of the web server already
            answers on it (or on a wildcard that covers it).
        dns: Point the name at this server in the DNS account that holds its zone,
            when that account creates records without asking. Accounts that ask
            are reported in the change's ``dns`` entries as ``pending``.
        port: Proxy this name to another port of the application than its own.
        logger: Where progress and warnings go.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        DomainError: When the name is not a domain or ``kind`` is ``primary``.
        DomainConflictError: When the name belongs to another application,
            to this one in another role, has a site of its own, or another site
            answers on it and ``force`` is not set. Adding a name this
            application already has in the same role is not an error: the site
            and the certificate are brought up to date again.
        ValidationError: When the application's type has no site to give
            names to (a static deploy without one, a headless stack), or the web
            server refuses the new configuration; nothing is left changed:
            the row and the file are put back.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    with app_lock(validate_domain(app_domain), "domain change"):
        return _add_domain(
            app_domain,
            domain,
            kind,
            issue_cert=issue_cert,
            force=force,
            dns=dns,
            port=port,
            logger=logger,
            verbose=verbose,
        )


def _add_domain(
    app_domain: str,
    domain: str,
    kind: str,
    *,
    issue_cert: bool,
    force: bool,
    dns: bool,
    port: int | None,
    logger: Logger | None,
    verbose: bool,
) -> DomainChange:
    """:func:`add_domain`, under the application's lock."""
    log = logger or Logger(verbose=verbose)
    app = _application(app_domain)
    name = validate_server_name(domain)
    deployer = _site_deployer(app, verbose=verbose)
    store = get_store()
    manager = deployer.webserver_manager()

    if not is_wildcard(name) and name != app.domain and manager.site_exists(name):
        raise DomainConflictError(
            f"{name} already has a site of its own",
            details=f"Delete it first if {app.domain} should answer on it: noust site delete {name}",
        )

    existing = {r.domain: r for r in store.list_domains(app.domain)}.get(name)
    if existing is not None and not existing.enabled:
        raise DomainError(
            f"{name} is switched off",
            details=f"Switch it on again: noust domain enable {app.domain} {name}",
        )
    retry = existing is not None and existing.kind == kind
    if not retry and not force:
        overlaps = find_overlaps(manager, name, own={manager.config_path(app.domain).name})
        if overlaps:
            raise DomainOverlapError(
                f"Another site of the web server already answers on {name}",
                details="; ".join(overlap.describe() for overlap in overlaps)
                + ". Add it anyway with force (noust domain add --force) if that is what you want.",
            )
    if port is not None:
        _check_port(app, kind, port)

    tls = _serves_tls(app, deployer)
    # Adding a name the application already has, in the same role, is how a
    # certificate order that failed is retried once DNS points here: the site
    # is rendered and the certificate ordered again, and the row is kept.
    if not retry:
        # The row first: the store is what refuses a bad name, a bad kind or
        # a taken one, and the name must be known before the live names are
        # adopted, or re-adding as a redirect the www a 1.x site served would
        # find it already adopted as an alias.
        store.add_domain(app.domain, name, kind)
    if port is not None:
        store.set_domain_port(app.domain, name, port)
    try:
        adopted = _adopt_served_names(app, deployer, log)
        deployer.refresh_site(with_ssl=tls)
    except _APPLY_ERRORS:
        if not retry:
            store.remove_domain(app.domain, name)
        raise
    log.success(f"{app.domain} answers on {name} ({kind})")

    entries = _point_dns(app, name, log) if dns else ()
    issued, error = False, None
    if tls and issue_cert:
        issued, error = _try_certificate(deployer, log)
    return _change(app, tls, adopted, issued, error, dns=entries)


def remove_domain(
    app_domain: str,
    domain: str,
    *,
    reduce_cert: bool = True,
    logger: Logger | None = None,
    verbose: bool = False,
) -> DomainChange:
    """
    Stop an application answering on a name.

    The name goes from the certificate as well, and its DNS record is released
    when nothing else uses it. Nothing is revoked: the certificate that covered
    the name expires on its own, and the new one never covered it.

    Args:
        app_domain: The application's primary domain.
        domain: The alias or redirect to remove.
        reduce_cert: Order the certificate again without the name, when the site
            serves TLS. False leaves that to :func:`reduce_certificate` (the panel
            queues it: certbot never runs on a request).
        logger: Where progress and warnings go.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        DomainError: When the name is the primary or the canonical host, or not
            one of the application's domains.
        ValidationError: When the application's type has no site to give
            names to, or the web server refuses the new
            configuration; the row and the file are put back.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    with app_lock(validate_domain(app_domain), "domain change"):
        return _remove_domain(
            app_domain, domain, reduce_cert=reduce_cert, logger=logger, verbose=verbose
        )


def _remove_domain(
    app_domain: str, domain: str, *, reduce_cert: bool, logger: Logger | None, verbose: bool
) -> DomainChange:
    """:func:`remove_domain`, under the application's lock."""
    log = logger or Logger(verbose=verbose)
    app = _application(app_domain)
    name = domain.strip().lower()
    deployer = _site_deployer(app, verbose=verbose)
    store = get_store()

    canonical = SitePolicyManager().get(app.domain).canonical
    if canonical and name == canonical_host(app.domain, canonical):
        raise DomainError(
            f"{name} is the spelling {app.domain} is served on",
            details=f"Choose the other one first: noust domain canonical {app.domain} "
            f"{'apex' if canonical == 'www' else 'www'} (or none)",
        )

    tls = _serves_tls(app, deployer)
    adopted = _adopt_served_names(app, deployer, log)
    records = {record.domain: record for record in store.list_domains(app.domain)}
    if not store.remove_domain(app.domain, name):
        raise DomainError(
            f"{name} is not a domain of {app.domain}",
            details=f"See the domains it answers on with: noust domain list {app.domain}",
        )
    try:
        deployer.refresh_site(with_ssl=tls)
    except _APPLY_ERRORS:
        store.add_domain(app.domain, name, records[name].kind)
        if records[name].port:
            store.set_domain_port(app.domain, name, records[name].port)
        if not records[name].enabled:
            store.set_domain_enabled(app.domain, name, False)
        raise
    log.success(f"{app.domain} no longer answers on {name}")

    reduced, error = (False, None)
    if tls and reduce_cert:
        reduced, error = _try_reduce(deployer, log)
    entries = _release_dns(app, name, log)
    return _change(app, tls, adopted, False, error, reduced=reduced, dns=entries)


def disable_domain(
    app_domain: str,
    domain: str,
    *,
    reduce_cert: bool = True,
    logger: Logger | None = None,
    verbose: bool = False,
) -> DomainChange:
    """
    Let a name rest: the application stops answering on it, and the name is not forgotten.

    The row stays with its kind and its port, the DNS record Noust made for it is left where it
    is (the name is meant to come back), and the site and the certificate leave it out.
    :func:`enable_domain` brings it back. Switching off a name already off is a retry of the
    certificate.

    Args:
        app_domain: The application's primary domain.
        domain: The alias or redirect to switch off.
        reduce_cert: Order the certificate again without the name, when the site serves TLS.
            False leaves that to :func:`reduce_certificate` (the panel queues it).
        logger: Where progress and warnings go.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        DomainError: When the name is the primary or one of the canonical pair, or not
            one of the application's domains.
        ValidationError: When the web server refuses the new configuration; the name is on again.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    with app_lock(validate_domain(app_domain), "domain change"):
        return _switch(
            app_domain,
            domain,
            enabled=False,
            certificate=reduce_cert,
            logger=logger,
            verbose=verbose,
        )


def enable_domain(
    app_domain: str,
    domain: str,
    *,
    issue_cert: bool = True,
    logger: Logger | None = None,
    verbose: bool = False,
) -> DomainChange:
    """
    Serve a name that was switched off again.

    Args:
        app_domain: The application's primary domain.
        domain: The alias or redirect to switch on.
        issue_cert: Extend the certificate to cover it, when the site serves TLS. False leaves
            that to :func:`issue_certificate` (the panel queues it).
        logger: Where progress and warnings go.
        verbose: Verbosity of the managers.

    Returns:
        What was done. Switching on a name already on re-renders the site and retries the
        certificate.

    Raises:
        NoustError: When the application is unknown.
        DomainError: When the name is not one of the application's domains.
        ValidationError: When the web server refuses the new configuration; the name stays off.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    with app_lock(validate_domain(app_domain), "domain change"):
        return _switch(
            app_domain,
            domain,
            enabled=True,
            certificate=issue_cert,
            logger=logger,
            verbose=verbose,
        )


def _switch(
    app_domain: str,
    domain: str,
    *,
    enabled: bool,
    certificate: bool,
    logger: Logger | None,
    verbose: bool,
) -> DomainChange:
    """:func:`disable_domain` and :func:`enable_domain`, under the application's lock."""
    log = logger or Logger(verbose=verbose)
    app = _application(app_domain)
    name = domain.strip().lower()
    deployer = _site_deployer(app, verbose=verbose)
    store = get_store()

    canonical = SitePolicyManager().get(app.domain).canonical
    if not enabled and canonical:
        host = canonical_host(app.domain, canonical)
        if name in (host, counterpart(host)):
            raise DomainError(
                f"{name} belongs to the canonical pair {app.domain} is served on",
                details=f"Serve both as recorded first: noust domain canonical {app.domain} none",
            )

    tls = _serves_tls(app, deployer)
    adopted = _adopt_served_names(app, deployer, log)
    previous = {record.domain: record for record in store.list_domains(app.domain)}.get(name)
    if previous is None or not store.set_domain_enabled(app.domain, name, enabled):
        raise DomainError(
            f"{name} is not a domain of {app.domain}",
            details=f"See the domains it answers on with: noust domain list {app.domain}",
        )
    try:
        deployer.refresh_site(with_ssl=tls)
    except _APPLY_ERRORS:
        store.set_domain_enabled(app.domain, name, previous.enabled)
        raise
    audit.record(
        "domains.enabled",
        target=f"app:{app.domain}",
        details={"name": name, "enabled": enabled},
    )
    log.success(
        f"{app.domain} answers on {name} again"
        if enabled
        else f"{app.domain} no longer answers on {name}; it is switched off, not removed"
    )

    issued, reduced, error = False, False, None
    if tls and certificate:
        if enabled:
            issued, error = _try_certificate(deployer, log)
        else:
            reduced, error = _try_reduce(deployer, log)
    return _change(app, tls, adopted, issued, error, reduced=reduced)


def set_canonical(
    app_domain: str,
    canonical: str,
    *,
    issue_cert: bool = True,
    logger: Logger | None = None,
    verbose: bool = False,
) -> DomainChange:
    """
    Choose whether ``www.example.com`` or ``example.com`` is the one served.

    The other spelling redirects to it. Both names are recorded as domains of the
    application (the missing one is added), so the certificate covers both and the
    redirect is served over TLS too. ``none`` serves both as they are recorded.

    Args:
        app_domain: The application's primary domain.
        canonical: ``www``, ``apex`` or ``none``.
        issue_cert: Extend the certificate to cover the pair, when the site serves TLS.
        logger: Where progress and warnings go.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: When the name has no www spelling, the canonical host is
            another application's, or the web server refuses the new site (the
            rows and the policy are put back).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    choice = "" if canonical in ("", "none") else canonical
    if choice not in ("", "www", "apex"):
        raise ValidationError("The canonical spelling is www, apex or none", field="canonical")
    with app_lock(validate_domain(app_domain), "domain change"):
        return _set_canonical(
            app_domain, choice, issue_cert=issue_cert, logger=logger, verbose=verbose
        )


def _set_canonical(
    app_domain: str, choice: str, *, issue_cert: bool, logger: Logger | None, verbose: bool
) -> DomainChange:
    """:func:`set_canonical`, under the application's lock."""
    log = logger or Logger(verbose=verbose)
    app = _application(app_domain)
    deployer = _site_deployer(app, verbose=verbose)
    store = get_store()
    policies = SitePolicyManager()
    before = policies.get(app.domain)
    if choice:
        check_canonical_possible(app.domain)
    host = canonical_host(app.domain, choice)
    pair = counterpart(host)

    changed: list[tuple[str, DomainRecord | None]] = []
    wanted = {host: DomainKind.ALIAS.value, pair: DomainKind.REDIRECT.value} if choice else {}
    tls = _serves_tls(app, deployer)
    adopted = _adopt_served_names(app, deployer, log)
    records = {r.domain: r for r in store.list_domains(app.domain)}
    resting: list[str] = []
    try:
        for name, kind in wanted.items():
            existing = records.get(name)
            if existing is not None and not existing.enabled:
                # The pair is served whatever the rows say; a name that rests cannot be it.
                store.set_domain_enabled(app.domain, name, True)
                resting.append(name)
            if name == app.domain or (existing is not None and existing.kind == kind):
                continue
            if existing is not None:
                store.remove_domain(app.domain, name)
            store.add_domain(app.domain, name, kind)
            changed.append((name, existing))
        policies.save(app.domain, replace(before, canonical=choice), actor=_actor())
        deployer.refresh_site(with_ssl=tls)
    except _APPLY_ERRORS:
        policies.save(app.domain, before, actor=_actor())
        for name, existing in reversed(changed):
            store.remove_domain(app.domain, name)
            if existing is not None:
                store.add_domain(app.domain, name, existing.kind)
        for name in resting:
            store.set_domain_enabled(app.domain, name, False)
        raise
    audit.record(
        "domains.canonical",
        target=f"app:{app.domain}",
        details={"canonical": choice or "none", "serves": host if choice else "as recorded"},
    )
    log.success(
        f"{host} is the name {app.domain} is served on; {pair} redirects to it"
        if choice
        else f"{app.domain} serves the names it has as they are"
    )
    issued, error = False, None
    if tls and issue_cert and choice:
        issued, error = _try_certificate(deployer, log)
    return _change(app, tls, adopted, issued, error, canonical=choice)


def set_domain_port(
    app_domain: str, domain: str, port: int | None, *, verbose: bool = False
) -> DomainChange:
    """
    Choose which port of the application an alias is proxied to.

    For an application with a second listener (an admin interface, an API on its
    own port) that should answer on a name of its own.

    Args:
        app_domain: The application's primary domain.
        domain: The alias.
        port: The port, or None for the application's own.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        DomainError: When the name is not an alias of the application.
        ValidationError: When the application proxies nothing, the port is not
            one, or the web server refuses the new site (the previous port is back).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Domains")
    with app_lock(validate_domain(app_domain), "domain change"):
        app = _application(app_domain)
        name = domain.strip().lower()
        deployer = _site_deployer(app, verbose=verbose)
        store = get_store()
        previous = {r.domain: r for r in store.list_domains(app.domain)}.get(name)
        if previous is None:
            raise DomainError(
                f"{name} is not a domain of {app.domain}",
                details=f"See them with: noust domain list {app.domain}",
            )
        if port is not None:
            _check_port(app, previous.kind, port)
        if not store.set_domain_port(app.domain, name, port):
            raise DomainError(f"{name} is not a domain of {app.domain}")
        tls = _serves_tls(app, deployer)
        try:
            deployer.refresh_site(with_ssl=tls)
        except _APPLY_ERRORS:
            store.set_domain_port(app.domain, name, previous.port)
            raise
        audit.record(
            "sites.port",
            target=f"app:{app.domain}",
            details={"name": name, "port": port, "application_port": app.port},
        )
        return _change(app, tls)


def issue_certificate(
    app_domain: str, *, logger: Logger | None = None, verbose: bool = False
) -> DomainChange:
    """
    Make the certificate of an application cover every one of its domains.

    For the panel, which never runs certbot on the request path: it adds the
    domain with ``issue_cert=False`` and queues this.

    Args:
        app_domain: The application's primary domain.
        logger: Where progress goes.
        verbose: Verbosity of the managers.

    Returns:
        What was done.

    Raises:
        NoustError: When the application is unknown.
        DeploymentError: When its site does not serve TLS; there is no
            certificate to expand, and ``noust cert create`` is how one starts.
        CertificateError: When certbot fails, carrying its output verbatim.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    log = logger or Logger(verbose=verbose)
    with app_lock(validate_domain(app_domain), "certificate order"):
        app = _application(app_domain)
        deployer = _site_deployer(app, verbose=verbose)
        if not _serves_tls(app, deployer):
            raise DeploymentError(
                f"{app.domain} is not served over TLS",
                details=f"Obtain its first certificate with: noust cert create -d {app.domain}",
            )
        _cover_every_domain(deployer)
        log.success(f"The certificate of {app.domain} covers every domain")
        return _change(app, True, certificate_issued=True)


def reduce_certificate(
    app_domain: str, *, logger: Logger | None = None, verbose: bool = False
) -> DomainChange:
    """
    Order the certificate of an application again with only the names it answers on.

    For the panel, which never runs certbot on the request path: it removes the domain
    with ``reduce_cert=False`` and queues this.

    Args:
        app_domain: The application's primary domain.
        logger: Where progress goes.
        verbose: Verbosity of the managers.

    Returns:
        What was done: ``certificate_reduced`` says whether an order was placed.

    Raises:
        NoustError: When the application is unknown.
        DeploymentError: When its site does not serve TLS; there is no certificate to reduce.
        CertificateError: When certbot fails, carrying its output verbatim; the
            certificate it had keeps serving.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    log = logger or Logger(verbose=verbose)
    with app_lock(validate_domain(app_domain), "certificate order"):
        app = _application(app_domain)
        deployer = _site_deployer(app, verbose=verbose)
        if not _serves_tls(app, deployer):
            raise DeploymentError(
                f"{app.domain} is not served over TLS",
                details="There is no certificate to reduce.",
            )
        reduced = deployer.reduce_certificate()
        if reduced:
            deployer.refresh_site(with_ssl=True)
            log.success(f"The certificate of {app.domain} covers only the names it answers on")
        return _change(app, True, reduced=reduced)


def check_dns(
    domain: str,
    *,
    resolver: Resolver | None = None,
    local_addresses: Callable[[], Iterable[str]] | None = None,
    resolvers: Sequence[str] | None = None,
    transport: Transport | None = None,
) -> DnsCheck:
    """
    Resolve a name and compare it with this machine's addresses.

    The name is resolved through the public resolvers ``dns.resolvers`` names (what the
    certificate authority's validators ask), not the machine's own cache, which on a
    server often still holds the answer from before the record changed; when none of
    them answers, the machine's resolver is asked. A name whose every address is
    Cloudflare's is behind its proxy: that reaches this server by itself, so it is not
    a mismatch. A server that only sees private addresses (behind NAT) cannot say what
    its public one is, and says so instead of calling a correct record wrong; declare
    the public one with ``dns.public_addresses`` to compare.

    Args:
        domain: The name to resolve.
        resolver: Resolves a host like :func:`socket.getaddrinfo`; given, it is the only
            thing asked (tests, and callers that mean the machine's own resolver).
        local_addresses: Lists this machine's addresses. Defaults to
            :func:`machine_addresses`.
        resolvers: The resolvers to ask instead of ``dns.resolvers``.
        transport: Sends one DNS question (tests replace it).

    Returns:
        The comparison.

    Raises:
        DomainError: When the name is not a domain.
        DependencyError: When this machine's addresses cannot be listed.
    """
    name = validate_domain(domain)
    declared = [str(address) for address in Config().get("dns.public_addresses") or []]
    expected = _sorted_addresses([*(local_addresses or machine_addresses)(), *declared])
    asked = ""
    resolved: tuple[str, ...]
    if resolver is not None:
        resolved = _system_resolution(name, resolver)
    else:
        servers = tuple(
            resolvers if resolvers is not None else (Config().get("dns.resolvers") or ())
        )
        found: list[str] = []
        answered: str | None = None
        if servers and transport is not None:
            found, answered = dns_resolve(name, servers, transport=transport)
        elif servers:
            found, answered = dns_resolve(name, servers)
        if answered is not None:
            asked = answered
            resolved = _sorted_addresses(found)
        else:
            resolved = _system_resolution(name, socket.getaddrinfo)
    here = bool(resolved) and set(resolved) <= set(expected)
    cdn = None if here else _cdn_of(resolved)
    behind_nat = (
        bool(expected)
        and not declared
        and not any(is_public_address(address) for address in expected)
    )
    if here:
        verdict = "here"
    elif cdn:
        verdict = "cdn"
    elif not resolved:
        verdict = "none"
    elif behind_nat and all(is_public_address(address) for address in resolved):
        verdict = "unknown"
    else:
        verdict = "elsewhere"
    return DnsCheck(
        domain=name,
        expected_addresses=expected,
        resolved_addresses=resolved,
        points_here=verdict in ("here", "cdn"),
        verdict=verdict,
        cdn=cdn,
        resolver=asked,
    )


def _system_resolution(name: str, resolver: Resolver) -> tuple[str, ...]:
    """
    Resolve a name the way :func:`socket.getaddrinfo` does.

    Args:
        name: The name.
        resolver: The function to ask.

    Returns:
        The addresses; empty when the name does not resolve (no such name, no
        records, or no resolver at all).
    """
    try:
        infos = resolver(name, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        infos = []
    return _sorted_addresses(str(info[4][0]) for info in infos)


def _cdn_of(addresses: Sequence[str]) -> str | None:
    """
    Name the CDN every address belongs to.

    Args:
        addresses: What a name resolves to.

    Returns:
        ``cloudflare`` when there are addresses and every one is in Cloudflare's published
        ranges; ``fastly``, ``bunnycdn`` or ``arvancloud`` when every one is in that provider's
        (:mod:`noust.core.cdn_networks`); None otherwise, a name that mixes two CDNs included.
    """
    if not addresses:
        return None
    networks = [
        ipaddress.ip_network(entry, strict=False)
        for entry in trusted_proxies.current_ranges().networks
    ]
    parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    if all(
        any(parsed.version == network.version and parsed in network for network in networks)
        for parsed in parsed_addresses
    ):
        return "cloudflare"
    return cdn_networks.provider_of(addresses)


def machine_addresses() -> tuple[str, ...]:
    """
    List the addresses of this machine's network interfaces.

    Loopback, link-local, multicast and unspecified addresses are left out:
    no public DNS record should point at them. A server behind NAT sees only
    its private address here, and a domain pointing at its public one reads
    as not pointing here.

    Returns:
        The addresses, sorted, IPv4 first.

    Raises:
        DependencyError: When psutil is not installed.
    """
    try:
        import psutil
    except ImportError as exc:
        raise DependencyError(
            "psutil is needed to list this server's addresses",
            details="Install it with: apt install python3-psutil (or pip install psutil).",
        ) from exc

    found: list[str] = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family in (socket.AF_INET, socket.AF_INET6):
                found.append(str(entry.address))
    return _sorted_addresses(found)


# -- Internals ----------------------------------------------------------------


def _actor() -> str | None:
    """
    Returns:
        A label for who is acting, for the records' ``updated_by``.
    """
    actor = audit.current_actor()
    return None if actor is None else str(actor.name or actor.id or actor.kind)


#: Types whose site serves files or several servers of its own: no single port to choose.
_NO_PORT_TYPES = frozenset({"static", "monorepo", "php-fpm", "docker-compose"})


def _check_port(app: App, kind: str, port: int) -> None:
    """
    Refuse a per-name port the application's site cannot honour.

    Args:
        app: The application.
        kind: The role of the name.
        port: The port asked for.

    Raises:
        DomainError: The name is not an alias.
        ValidationError: The application proxies nothing, or the port is not one.
    """
    if kind != DomainKind.ALIAS.value:
        raise DomainError(
            "Only an alias has a port of its own",
            details="A redirect sends visitors to the primary; it has nothing to proxy.",
        )
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValidationError(
            f"{port!r} is not a port", details="A port is 1 to 65535.", field="port"
        )
    if app.app_type in _NO_PORT_TYPES or not app.port:
        reason = (
            "a Docker Compose stack routes by noust.nginx.yaml, where each route names its port"
            if app.app_type == "docker-compose"
            else f"{app.domain} does not proxy to a port"
        )
        raise ValidationError(
            f"A name cannot be sent to another port of {app.domain}: {reason}", field="port"
        )


def _point_dns(app: App, name: str, log: Logger) -> tuple[dict[str, Any], ...]:
    """
    Point a new name at this server in the DNS account that holds its zone.

    Args:
        app: The application.
        name: The name added.
        log: Where each outcome is said.

    Returns:
        One entry per record (see :meth:`~noust.managers.dns.records.ManagedDns.ensure_for_domain`);
        empty when no account holds the zone. A DNS failure never fails the addition.
    """
    from noust.managers.dns.records import ManagedDns

    try:
        entries = ManagedDns().ensure_for_domain(app.domain, name, actor=_actor())
    except (NoustError, sqlite3.Error) as exc:
        log.warning(f"DNS for {name} was not looked at: {exc}")
        return ()
    for entry in entries:
        outcome = entry.get("outcome")
        what = f"{entry.get('type', '')} {name} {entry.get('content', '')}".strip()
        if outcome in ("created", "adopted", "replaced", "exists"):
            log.info(f"DNS: {what} ({outcome}, {entry.get('account')})")
        elif outcome == "pending":
            log.info(
                f"DNS: {entry.get('account')} could create {what}; "
                f"say so with: noust dns ensure {app.domain} {name}"
            )
        else:
            log.warning(f"DNS for {name}: {entry.get('detail') or outcome}")
    return tuple(entries)


def _release_dns(app: App, name: str, log: Logger) -> tuple[dict[str, Any], ...]:
    """
    Let go of the DNS records of a name that is no longer the application's.

    Args:
        app: The application.
        name: The name removed.
        log: Where each outcome is said.

    Returns:
        One entry per record that was managed: ``hostname``, ``type`` and ``outcome``.
        Empty when none was; failures are reported and never fail the removal.
    """
    from noust.managers.dns.records import ManagedDns

    try:
        results = ManagedDns().release(app.domain, hostname=name)
    except (NoustError, sqlite3.Error) as exc:
        log.warning(f"DNS for {name} was not released: {exc}")
        return ()
    for result in results:
        log.info(f"DNS: {result.record_type} {result.hostname} {result.outcome.value}")
    return tuple(
        {
            "hostname": r.hostname,
            "type": r.record_type,
            "outcome": r.outcome.value,
            "account": r.provider,
        }
        for r in results
    )


def _try_reduce(deployer: ServesDomains, log: Logger) -> tuple[bool, str | None]:
    """
    Order the certificate again without the names that were removed, reporting a failure.

    Args:
        deployer: The application's deployer.
        log: Where the failure is reported.

    Returns:
        ``(True, None)`` when the certificate was ordered again; ``(False, None)``
        when it needed nothing; ``(False, output)`` with certbot's words when it
        failed. The certificate it had keeps serving either way.
    """
    try:
        reduced = deployer.reduce_certificate()
        if reduced:
            deployer.refresh_site(with_ssl=True)
    except CertificateError as exc:
        output = exc.output or exc.details or exc.message
        log.warning(f"The certificate still names the removed domain: {exc.message}")
        return False, output
    return reduced, None


def _application(app_domain: str) -> App:
    """
    Read the row of a deployed application.

    Args:
        app_domain: Its primary domain.

    Returns:
        The row.

    Raises:
        NoustError: When nothing is deployed there.
    """
    domain = validate_domain(app_domain)
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Run 'noust list' to see what is deployed.",
        )
    return app


def application(app_domain: str) -> App:
    """
    Read the row of a deployed application.

    Args:
        app_domain: Its primary domain.

    Returns:
        The row.

    Raises:
        NoustError: When nothing is deployed there.
    """
    return _application(app_domain)


def operator_wrote_site(app: App, *, verbose: bool = False) -> bool:
    """
    Tell whether an application's site is one the operator wrote by hand.

    Such a site is never rewritten (CONTRIBUTING, "An operator's site is
    never rewritten or deleted"), so nothing Noust would render into it - a
    policy, a canonical name, a port - can take effect.

    Args:
        app: The application.
        verbose: Verbosity of the deployer.

    Returns:
        True when the site exists and does not carry Noust's marker.

    Raises:
        ValidationError: The application's type has no site to render.
    """
    deployer = _site_deployer(app, verbose=verbose)
    return is_operator_site(deployer.webserver_manager(), app.domain)


def site_deployer(app: App, *, verbose: bool = False) -> ServesDomains:
    """
    Build the deployer that renders an application's site, configured from its row.

    Args:
        app: The application.
        verbose: Verbosity of the deployer.

    Returns:
        The deployer.

    Raises:
        DeploymentError: When the type is unknown.
        ValidationError: When the type cannot serve more than one name.
    """
    return _site_deployer(app, verbose=verbose)


def serves_tls(app: App, deployer: ServesDomains) -> bool:
    """
    Tell whether an application's site serves TLS now.

    Args:
        app: The application.
        deployer: Its deployer.

    Returns:
        True when the site records TLS and the certificate it would name is on disk.
    """
    return _serves_tls(app, deployer)


def refresh_site(app: App, *, verbose: bool = False) -> None:
    """
    Render an application's site again, exactly as a deploy would.

    For a change that lives in the web server's configuration only, such as
    blue/green moving the site to its upstream and back: the names, the
    redirects and TLS stay what they are.

    Args:
        app: The application.
        verbose: Whether the deployer reports its steps.

    Raises:
        ValidationError: The application's type has no site to render, or the web server refused the new site (the old
            file is back).
        AppBusyError: Another operation is running on the application.
    """
    # Reentrant: the blue/green switch calls this holding the lock already.
    with app_lock(app.domain, "site refresh"):
        deployer = _site_deployer(app, verbose=verbose)
        deployer.refresh_site(with_ssl=_serves_tls(app, deployer))


def _site_deployer(app: App, *, verbose: bool) -> ServesDomains:
    """
    Build the deployer that renders an application's site, configured from its row.

    Args:
        app: The application.
        verbose: Verbosity of the deployer.

    Returns:
        The deployer.

    Raises:
        DeploymentError: When the type is unknown.
        ValidationError: When the type cannot serve more than one name.
    """
    try:
        deployer = get_deployer(app.app_type, verbose=verbose)
    except ValueError as exc:
        raise DeploymentError(
            f"{app.domain} has an application type Noust does not know: {app.app_type}",
            details=f"Redeploy it with an explicit type: noust create -d {app.domain} --type ...",
        ) from exc
    # The capability, not a class: Compose stacks and monorepos render their
    # sites through the same helper and answer on aliases like the rest.
    if not isinstance(deployer, ServesDomains):
        raise ValidationError(
            f"{app.app_type} applications do not support aliases and redirects yet",
            details=(
                f"{app.domain} has no site of its own to give other names to. "
                "Serve another name with its own application or site instead."
            ),
        )
    deployer.configure(
        app.domain,
        app.source or str(app_root(app)),
        port=app.port,
        webserver=app.webserver,
        # The certificate step is asked for explicitly; ssl only has to let it run.
        ssl=True,
        branch=app.branch,
        app_path=app_root(app),
    )
    return deployer


def _serves_tls(app: App, deployer: ServesDomains) -> bool:
    """
    Tell whether an application's site serves TLS now.

    Args:
        app: The application.
        deployer: Its deployer.

    Returns:
        True when the site (or, without a site row, the application) records
        TLS and the certificate it would name is on disk.
    """
    site = get_store().get_site(app.domain)
    recorded = site.ssl_enabled if site is not None else app.ssl_enabled
    return bool(recorded) and deployer.has_certificate()


def _adopt_served_names(app: App, deployer: ServesDomains, log: Logger) -> tuple[str, ...]:
    """
    Record, as aliases, the names the live site answers on and the store does not know.

    Args:
        app: The application.
        deployer: Its deployer.
        log: Where each adoption is reported.

    Returns:
        The names adopted.
    """
    store = get_store()
    known = {record.domain for record in store.list_domains(app.domain)}
    adopted: list[str] = []
    for name in deployer.webserver_manager().served_names(app.domain):
        if name in known:
            continue
        try:
            store.add_domain(app.domain, name, DomainKind.ALIAS.value)
        except DomainConflictError as exc:
            log.warning(f"The site of {app.domain} also answers on {name}, which is not its: {exc}")
            continue
        adopted.append(name)
        log.substep(f"Kept {name}, which the site already answered on, as an alias")
    return tuple(adopted)


def _try_certificate(deployer: ServesDomains, log: Logger) -> tuple[bool, str | None]:
    """
    Expand the certificate, reporting a certbot failure instead of raising it.

    Args:
        deployer: The application's deployer.
        log: Where the failure is reported.

    Returns:
        ``(True, None)`` once it covers every domain, or ``(False, output)``
        with certbot's own words when the order failed.
    """
    try:
        _cover_every_domain(deployer)
    except CertificateError as exc:
        if exc.output and exc.details and exc.details.strip() != exc.output.strip():
            # A diagnosis (a record pointing elsewhere, say) goes above certbot's
            # own words, which stay verbatim underneath.
            output = f"{exc.message}\n{exc.details}\n\n{exc.output}"
        elif exc.output:
            output = exc.output
        else:
            output = exc.details or exc.message
        log.warning(f"The certificate was not extended: {exc.message}")
        return False, output
    return True, None


def _cover_every_domain(deployer: ServesDomains) -> None:
    """
    Order the certificate for every domain, then load it.

    ``obtain`` returns at once when the lineage already covers them all; the
    site is reloaded either way, so an expanded certificate is served.

    Args:
        deployer: The application's deployer.

    Raises:
        CertificateError: When certbot fails.
    """
    deployer.obtain_certificate()
    deployer.refresh_site(with_ssl=True)


def _change(
    app: App,
    tls: bool,
    adopted: tuple[str, ...] = (),
    certificate_issued: bool = False,
    certificate_error: str | None = None,
    *,
    reduced: bool = False,
    dns: Sequence[dict[str, Any]] = (),
    canonical: str | None = None,
) -> DomainChange:
    """
    Describe an application's domains after a change.

    Args:
        app: The application.
        tls: Whether its site serves TLS.
        adopted: Names adopted from the live site.
        certificate_issued: Whether the certificate covers every domain.
        certificate_error: Certbot's output when an order failed.
        reduced: Whether the certificate was ordered again without a removed name.
        dns: What happened to the DNS records.
        canonical: The spelling served; read from the policy when not given.

    Returns:
        The description.
    """
    return DomainChange(
        app=app.domain,
        domains=tuple(get_store().list_domains(app.domain)),
        tls=tls,
        adopted=adopted,
        certificate_issued=certificate_issued,
        certificate_error=certificate_error,
        certificate_reduced=reduced,
        dns=tuple(dns),
        canonical=SitePolicyManager().get(app.domain).canonical if canonical is None else canonical,
    )


def _sorted_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    """
    Parse, filter, deduplicate and sort addresses.

    Args:
        addresses: Address strings, possibly with an IPv6 ``%scope``.

    Returns:
        The usable ones - not loopback, link-local, multicast or unspecified -
        in canonical form, IPv4 first.
    """
    parsed: set[_Address] = set()
    for text in addresses:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            continue
        parsed.add(address)
    ordered = sorted(parsed, key=lambda address: (address.version, int(address)))
    return tuple(str(address) for address in ordered)
