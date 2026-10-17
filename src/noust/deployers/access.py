# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one implementation of "what an application's site does besides serving it".

``noust site policy`` and ``/api/apps/{domain}/policy`` are presentation over the
functions here. A change goes through the same steps, in this order:

1. **The policy**, checked field by field (:func:`noust.managers.site_policy.validated`)
   and against the address of the operator making the change: an address list
   that would lock them out is refused unless they say they know.
2. **The store**, which keeps it where a redeploy cannot put the old one back.
3. **The site**, rendered again by the application's own deployer exactly as a
   deploy renders it (:func:`noust.deployers.domains.refresh_site`), tested by
   the web server before it is reloaded. A change the web server refuses is put
   back: the document and the file both.

A site the operator wrote by hand is never rewritten, so a policy for it is
refused, not stored: it would look applied and do nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.client_cert_store import ClientCertificate
from noust.core.exceptions import NoustError, ValidationError
from noust.core.runner import get_runner
from noust.core.store import App, get_store
from noust.deployers import domains
from noust.managers import trusted_proxies
from noust.managers.client_certs import ClientCertificates, IssuedClientCertificate
from noust.managers.http3 import Http3, Http3Support
from noust.managers.site_forward_auth import check_service
from noust.managers.site_policy import (
    MAX_USERS,
    SitePolicy,
    SitePolicyManager,
    check_canonical_possible,
    check_username,
    locks_out,
    response_headers,
    validated,
)
from noust.managers.stream_exposure import reserved_ports
from noust.managers.waf import Waf, WafEvent, WafSupport, noisy_rules
from noust.validators.domain import validate_domain

#: App types whose site is not one server block Noust renders for the application:
#: nothing in the policy reaches the file.
UNSUPPORTED_TYPES: frozenset[str] = frozenset({"monorepo"})

#: Host names that mean this machine.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: What can fail while a change is applied, and is undone before it is raised.
_APPLY_ERRORS = (NoustError, OSError)


@dataclass(frozen=True)
class AccessView:
    """
    An application's policy as an operator reads it.

    Attributes:
        app: The application's primary domain.
        policy: The policy in force.
        users: The names of the basic-authentication accounts.
        webserver: ``nginx`` or ``apache``.
        applicable: Whether the policy reaches the site: False for a site the
            operator wrote or a type with no site Noust renders.
        reason: Why it does not, when it does not.
        headers: The response headers the site sends, as worked out.
        real_ip_networks: The networks the client address is believed from.
        real_ip_source: ``fetched``, ``built-in`` or empty.
    """

    app: str
    policy: SitePolicy
    users: tuple[str, ...]
    webserver: str
    applicable: bool = True
    reason: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    real_ip_networks: tuple[str, ...] = ()
    real_ip_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the view for the API and the CLI.

        Returns:
            Every field, JSON-ready. Nothing secret is in it.
        """
        return {
            "app": self.app,
            "policy": self.policy.to_dict(),
            "users": list(self.users),
            "webserver": self.webserver,
            "applicable": self.applicable,
            "reason": self.reason,
            "headers": [{"name": name, "value": value} for name, value in self.headers],
            "real_ip_networks": list(self.real_ip_networks),
            "real_ip_source": self.real_ip_source,
        }


def ssh_client_address(environ: Mapping[str, str] | None = None) -> str | None:
    """
    Read the address the operator's SSH session comes from.

    Args:
        environ: The environment to read; the process's own by default.

    Returns:
        The client address, or None when the command was not started over SSH.
    """
    parts = (environ if environ is not None else os.environ).get("SSH_CONNECTION", "").split()
    return parts[0] if parts else None


def describe(app_domain: str, *, verbose: bool = False) -> AccessView:
    """
    Read an application's policy.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        The policy and what it comes to.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(app_domain)
    manager = SitePolicyManager()
    policy = manager.get(app.domain)
    reason = _unsupported(app, verbose=verbose)
    ssl = _serves_tls(app)
    ranges = trusted_proxies.current_ranges() if policy.real_ip == "cloudflare" else None
    return AccessView(
        app=app.domain,
        policy=policy,
        users=tuple(user.username for user in manager.users(app.domain)),
        webserver=app.webserver,
        applicable=not reason,
        reason=reason,
        headers=tuple(response_headers(policy, ssl=ssl)),
        real_ip_networks=ranges.networks if ranges else tuple(policy.trusted_proxies),
        real_ip_source=ranges.source if ranges else "",
    )


def set_policy(
    app_domain: str,
    changes: Mapping[str, Any],
    *,
    operator_addresses: Sequence[str] = (),
    accept_lockout: bool = False,
    accept_unreachable_auth: bool = False,
    verbose: bool = False,
) -> AccessView:
    """
    Change fields of an application's policy and render its site again.

    Args:
        app_domain: The application's primary domain.
        changes: Field to new value; a field not named keeps its value.
        operator_addresses: The addresses the operator is making the change from
            (their request, their SSH session), so a list that refuses them is caught.
        accept_lockout: Go ahead although the lists refuse the operator's address.
        accept_unreachable_auth: Go ahead although the authentication service does not
            answer; every visitor would be refused until it does.
        verbose: Verbosity of the deployer.

    Returns:
        The policy now in force.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: A value is not acceptable, the site cannot carry the
            policy, the lists would lock the operator out, or the web server
            refuses the new configuration (nothing is left changed).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    with app_lock(domain, "site policy"):
        return _set_policy(
            domain,
            changes,
            operator_addresses=operator_addresses,
            accept_lockout=accept_lockout,
            accept_unreachable_auth=accept_unreachable_auth,
            verbose=verbose,
        )


def _set_policy(
    domain: str,
    changes: Mapping[str, Any],
    *,
    operator_addresses: Sequence[str],
    accept_lockout: bool,
    accept_unreachable_auth: bool,
    verbose: bool,
) -> AccessView:
    """:func:`set_policy`, under the application's lock."""
    app = domains.application(domain)
    manager = SitePolicyManager()
    before = manager.get(app.domain)
    after = validated(before, changes)
    if after == before:
        return describe(app.domain, verbose=verbose)
    _require_applicable(app, after, verbose=verbose)
    if after.uses_accounts and not manager.users(app.domain):
        raise ValidationError(
            f"{app.domain} asks for a password and has no accounts",
            details=f"Add an account first: noust app policy user add {app.domain} NAME --password-stdin",
            field="basic_auth",
        )
    if after.canonical != before.canonical:
        _check_canonical(app, after.canonical)
    refused = locks_out(after, operator_addresses)
    if refused and not accept_lockout:
        raise ValidationError(
            f"These lists would refuse your own address, {refused[0]}",
            details="Add it to the allowed addresses, or accept the lockout knowingly "
            "(--accept-lockout; in the console, the confirmation box).",
            field="allow_ips",
        )
    if after.forward_auth is not None and after.forward_auth != before.forward_auth:
        _require_service_answers(after.forward_auth, accept_unreachable_auth)
    if after.client_certs and not before.client_certs:
        _require_a_way_in_by_certificate(app, accept_lockout)
        ClientCertificates().ensure_authority(app.domain)
    anchored = _hold_quic_socket(app, before, after, verbose=verbose)
    try:
        firewalled = _hold_waf_rules(app, before, after, verbose=verbose)
    except _APPLY_ERRORS:
        if anchored:
            _let_go_of_quic_socket(app, verbose=verbose)
        raise
    try:
        _apply(app, manager, before, after, verbose=verbose)
    except _APPLY_ERRORS:
        if anchored:
            _let_go_of_quic_socket(app, verbose=verbose)
        if firewalled:
            _let_go_of_waf_rules(app, verbose=verbose)
        raise
    if before.http3 and not after.http3:
        _let_go_of_quic_socket(app, verbose=verbose)
    if before.waf and not after.waf:
        _let_go_of_waf_rules(app, verbose=verbose)
    changed = _changed_fields(before, after)
    audit.record(
        "sites.policy",
        target=f"app:{app.domain}",
        details={"changed": sorted(changed), "policy": after.to_dict()},
    )
    if changed & {"real_ip", "trusted_proxies"}:
        _keep_brute_force_jails_true(operator_addresses)
    return describe(app.domain, verbose=verbose)


def reset_policy(app_domain: str, *, verbose: bool = False) -> AccessView:
    """
    Take every field of the policy back to its default.

    The accounts are kept: only the fields (password asked for or not, lists,
    headers...) go back. Remove accounts with :func:`remove_user`.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        The policy now in force.

    Raises:
        NoustError: When the application is unknown.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    with app_lock(domain, "site policy"):
        app = domains.application(domain)
        manager = SitePolicyManager()
        before = manager.get(app.domain)
        if before.is_default:
            return describe(app.domain, verbose=verbose)
        _apply(app, manager, before, SitePolicy(), verbose=verbose)
        if before.http3:
            _let_go_of_quic_socket(app, verbose=verbose)
        if before.waf:
            _let_go_of_waf_rules(app, verbose=verbose)
        audit.record("sites.policy", target=f"app:{app.domain}", details={"changed": ["reset"]})
        if before.real_ip != "off":
            _keep_brute_force_jails_true(())
        return describe(app.domain, verbose=verbose)


def set_user(app_domain: str, username: str, password: str, *, verbose: bool = False) -> AccessView:
    """
    Add an account to an application's basic authentication, or change its password.

    Args:
        app_domain: The application's primary domain.
        username: The login name.
        password: The password. Hashed at once; never stored, logged or audited.
        verbose: Verbosity of the deployer.

    Returns:
        The policy and its accounts.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: The name or password is not acceptable, or the web
            server refuses the rewritten site.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    name = check_username(username)
    manager = SitePolicyManager()
    hashed = manager.hash_password(password)
    with app_lock(domain, "site policy"):
        app = domains.application(domain)
        policy = manager.get(app.domain)
        existing = {user.username for user in manager.users(app.domain)}
        if len(existing) >= MAX_USERS and name not in existing:
            raise ValidationError(
                f"{app.domain} already has {MAX_USERS} accounts", field="username"
            )
        _require_applicable(app, policy, verbose=verbose)
        previous = {user.username: user for user in manager.users(app.domain)}.get(name)
        manager.records.set_user(app.domain, name, hashed, created_by=audit_actor())
        try:
            if policy.uses_accounts:
                domains.refresh_site(app, verbose=verbose)
        except _APPLY_ERRORS:
            if previous is None:
                manager.records.remove_user(app.domain, name)
            else:
                manager.records.set_user(
                    app.domain, name, previous.password_hash, created_by=previous.created_by
                )
            raise
        audit.record(
            "sites.policy.user",
            target=f"app:{app.domain}",
            details={"action": "set", "username": name},
        )
        return describe(app.domain, verbose=verbose)


def remove_user(app_domain: str, username: str, *, verbose: bool = False) -> AccessView:
    """
    Remove an account of an application's basic authentication.

    Args:
        app_domain: The application's primary domain.
        username: The login name.
        verbose: Verbosity of the deployer.

    Returns:
        The policy and its accounts.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: The account does not exist, it is the last one while a
            password is asked for, or the web server refuses the rewritten site.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Sites")
    domain = validate_domain(app_domain)
    with app_lock(domain, "site policy"):
        app = domains.application(domain)
        manager = SitePolicyManager()
        policy = manager.get(app.domain)
        users = {user.username: user for user in manager.users(app.domain)}
        if username not in users:
            raise ValidationError(
                f"{username} is not an account of {app.domain}",
                details=f"See them with: noust app policy show {app.domain}",
                field="username",
            )
        if policy.uses_accounts and len(users) == 1:
            raise ValidationError(
                f"{username} is the only account and the site asks for a password",
                details="Remove the password requirement first, or nobody can sign in: "
                f"noust app policy set {app.domain} --no-password"
                + (" and take it off its routes" if policy.routes else ""),
                field="username",
            )
        manager.records.remove_user(app.domain, username)
        try:
            if policy.uses_accounts:
                domains.refresh_site(app, verbose=verbose)
        except _APPLY_ERRORS:
            removed = users[username]
            manager.records.set_user(
                app.domain, username, removed.password_hash, created_by=removed.created_by
            )
            raise
        audit.record(
            "sites.policy.user",
            target=f"app:{app.domain}",
            details={"action": "remove", "username": username},
        )
        return describe(app.domain, verbose=verbose)


def refresh_trusted_ranges(*, verbose: bool = False) -> dict[str, Any]:
    """
    Fetch Cloudflare's published ranges again and render the sites that trust them.

    Args:
        verbose: Verbosity of the deployers.

    Returns:
        ``changed``: whether the ranges differ from the ones in force;
        ``source`` and ``count``: what is in force now; ``applied``: the
        applications whose site was rendered again; ``failed``: those that
        could not be, with the reason.

    Raises:
        IntegrationError: The ranges could not be fetched; the previous ones stay.
    """
    require_server_role("Sites")
    before = trusted_proxies.current_ranges()
    after = trusted_proxies.refresh()
    result: dict[str, Any] = {
        "changed": before.networks != after.networks,
        "source": after.source,
        "count": len(after.networks),
        "applied": [],
        "failed": {},
    }
    if not result["changed"]:
        return result
    manager = SitePolicyManager()
    for name in manager.records.domains_with_policy():
        if manager.get(name).real_ip != "cloudflare":
            continue
        try:
            with app_lock(name, "site policy"):
                domains.refresh_site(domains.application(name), verbose=verbose)
            result["applied"].append(name)
        except _APPLY_ERRORS as exc:
            result["failed"][name] = str(exc)
    _keep_brute_force_jails_true(())
    return result


def _keep_brute_force_jails_true(operator_addresses: Sequence[str]) -> None:
    """
    Tell fail2ban's nginx jails who stands in front of the sites now.

    The jails never ban Cloudflare's ranges nor a proxy a site declares; the list is written once,
    so a change to the proxies has to rewrite it, or the new proxy is banned at its first wrong
    password and every site behind it goes with it.

    Args:
        operator_addresses: The addresses the operator is making the change from.
    """
    from noust.managers.server.security_fail2ban import keep_web_jails_true

    keep_web_jails_true(operator_addresses)


def audit_actor() -> str | None:
    """
    Returns:
        A label for who is acting, for the records' ``created_by``.
    """
    actor = audit.current_actor()
    return None if actor is None else str(actor.name or actor.id or actor.kind)


def forget(app_domain: str) -> None:
    """
    Drop the policy and accounts of an application that has been deleted.

    Args:
        app_domain: The application's primary domain.
    """
    SitePolicyManager().forget(app_domain)
    ClientCertificates().forget(app_domain)


# -- Internals --------------------------------------------------------------------


def http3_support(app_domain: str, *, verbose: bool = False) -> Http3Support:
    """
    Say whether an application's server can serve it over HTTP/3.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        What is available, and what stands in the way.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(validate_domain(app_domain))
    return Http3(_web_server(app, verbose=verbose)).support()


def waf_support(app_domain: str, *, verbose: bool = False) -> WafSupport:
    """
    Say whether an application's server can run the web application firewall.

    Args:
        app_domain: The application's primary domain.
        verbose: Verbosity of the deployer.

    Returns:
        What is available, and what stands in the way.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(validate_domain(app_domain))
    return Waf(_web_server(app, verbose=verbose)).support()


def waf_events(
    app_domain: str, *, limit: int = 50, verbose: bool = False
) -> tuple[list[WafEvent], list[tuple[str, str, int]]]:
    """
    Read what the WAF's rules judged lately for an application.

    Args:
        app_domain: The application's primary domain.
        limit: The most events returned.
        verbose: Verbosity of the deployer.

    Returns:
        The events, newest first, and the rules that matched most often (the ones to look at
        when a legitimate request is refused).

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(validate_domain(app_domain))
    names = {record.domain for record in get_store().list_domains(app.domain)} | {app.domain}
    events = Waf(_web_server(app, verbose=verbose)).events(names, limit)
    return events, noisy_rules(events)


def install_waf(*, verbose: bool = False) -> list[str]:
    """
    Install the ModSecurity module and the Core Rule Set on this server.

    Args:
        verbose: Unused; kept so every operation here takes the same keyword.

    Returns:
        The packages installed.

    Raises:
        UnsupportedHostError: The server is not Debian or Ubuntu.
        ValidationError: The package manager failed.
    """
    from noust.managers.unknown_hosts import web_manager

    return Waf(web_manager("nginx")).install()


def _web_server(app: App, *, verbose: bool) -> Any:
    """
    Args:
        app: The application.
        verbose: Verbosity of the deployer.

    Returns:
        The manager of the web server that holds its site.
    """
    return domains.site_deployer(app, verbose=verbose).webserver_manager()


def _other_sites_use_http3(app: App) -> bool:
    """
    Args:
        app: The application being changed.

    Returns:
        Whether another application's site is served over HTTP/3.
    """
    manager = SitePolicyManager()
    return any(
        manager.get(name).http3
        for name in manager.records.domains_with_policy()
        if name != app.domain
    )


def _other_sites_use_waf(app: App) -> bool:
    """
    Args:
        app: The application being changed.

    Returns:
        Whether another application's site has the web application firewall on.
    """
    manager = SitePolicyManager()
    return any(
        manager.get(name).waf
        for name in manager.records.domains_with_policy()
        if name != app.domain
    )


def _hold_waf_rules(app: App, before: SitePolicy, after: SitePolicy, *, verbose: bool) -> bool:
    """
    Make sure the shared rules are loaded when a site turns the WAF on.

    Args:
        app: The application.
        before: The policy in force.
        after: The policy about to be kept.
        verbose: Verbosity of the deployer.

    Returns:
        True when this call wrote them, so a refused change can take them away again.

    Raises:
        ValidationError: This server cannot run the WAF, or nginx refused its rules.
    """
    if not after.waf or before.waf:
        return False
    return Waf(_web_server(app, verbose=verbose)).ensure_anchor()


def _let_go_of_waf_rules(app: App, *, verbose: bool) -> None:
    """
    Take the shared rules away when no application has the WAF on.

    Args:
        app: The application that stopped using it.
        verbose: Verbosity of the deployer.
    """
    if not _other_sites_use_waf(app):
        Waf(_web_server(app, verbose=verbose)).release_anchor()


def _require_a_way_in_by_certificate(app: App, accepted: bool) -> None:
    """
    Refuse to ask for client certificates while nobody holds one.

    With none valid the site answers no one, its owner included, until a certificate is made and
    given to someone. The application's own authority only exists once the first is made, so this
    asks for the certificate first.

    Args:
        app: The application.
        accepted: The operator knows nobody can get in yet.

    Raises:
        ValidationError: No certificate of this application is valid and the operator has not
            accepted that.
    """
    now = datetime.now(timezone.utc).isoformat()
    usable = [
        c for c in ClientCertificates().list(app.domain) if not c.revoked and c.not_after > now
    ]
    if usable or accepted:
        return
    raise ValidationError(
        f"No client certificate of {app.domain} is valid, so no one could open it",
        details="Make one first: noust app policy clients issue "
        f"{app.domain} NAME. Or accept that nobody gets in yet (--accept-lockout; in the "
        "console, the confirmation box).",
        field="client_certs",
    )


def client_certificates(app_domain: str) -> list[ClientCertificate]:
    """
    List the client certificates of an application.

    Args:
        app_domain: The application's primary domain.

    Returns:
        Its certificates, newest first, revoked ones included.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(validate_domain(app_domain))
    return ClientCertificates().list(app.domain)


def client_authority(app_domain: str) -> str | None:
    """
    Args:
        app_domain: The application's primary domain.

    Returns:
        The certificate of its client authority (PEM), or None before the first is issued.

    Raises:
        NoustError: When the application is unknown.
    """
    app = domains.application(validate_domain(app_domain))
    return ClientCertificates().ca_certificate(app.domain)


def issue_client_certificate(
    app_domain: str, name: str, *, days: int = 365, verbose: bool = False
) -> IssuedClientCertificate:
    """
    Sign a certificate for a client of an application's site.

    Args:
        app_domain: The application's primary domain.
        name: What it is for: its common name.
        days: How long it is valid.
        verbose: Verbosity of the deployer.

    Returns:
        The certificate, the key and the bundle a browser imports, once.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: The name or the validity is not acceptable.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    domain = validate_domain(app_domain)
    with app_lock(domain, "client certificate"):
        app = domains.application(domain)
        if _unsupported(app, verbose=verbose):
            raise ValidationError(
                f"A policy would change nothing on {app.domain}: {_unsupported(app, verbose=verbose)}",
                field="policy",
            )
        return ClientCertificates().issue(app.domain, name, days=days, actor=audit_actor())


def revoke_client_certificate(
    app_domain: str, serial: str, *, accept_lockout: bool = False, verbose: bool = False
) -> bool:
    """
    Revoke a client certificate: the site refuses it from the moment it is rendered again.

    Args:
        app_domain: The application's primary domain.
        serial: The certificate's serial.
        accept_lockout: Go ahead although it was the last valid one of a site that asks for
            certificates.
        verbose: Verbosity of the deployer.

    Returns:
        True when it was revoked now.

    Raises:
        NoustError: When the application is unknown.
        ValidationError: There is no such certificate, or it is the last valid one and the
            operator has not accepted that.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Certificates")
    domain = validate_domain(app_domain)
    with app_lock(domain, "client certificate"):
        app = domains.application(domain)
        certificates = ClientCertificates()
        manager = SitePolicyManager()
        if manager.get(app.domain).client_certs and not accept_lockout:
            now = datetime.now(timezone.utc).isoformat()
            others = [
                c
                for c in certificates.list(app.domain)
                if not c.revoked and c.not_after > now and c.serial != serial.upper()
            ]
            if not others:
                raise ValidationError(
                    f"That is the last valid client certificate of {app.domain}",
                    details="Revoked, no one could open the site. Make another first, or accept "
                    "it (--accept-lockout; in the console, the confirmation box).",
                    field="serial",
                )
        revoked = certificates.revoke(app.domain, serial, actor=audit_actor())
        if revoked and manager.get(app.domain).client_certs:
            domains.refresh_site(app, verbose=verbose)
        return revoked


def _require_service_answers(auth: Any, accepted: bool) -> None:
    """
    Refuse an authentication service that does not answer, unless the operator says go on.

    With a service that is down, nginx refuses every request: the site would stop for everyone,
    its owner included. The service is asked once, as nginx would for a visitor with no session.

    Args:
        auth: The forward-auth settings about to be kept.
        accepted: The operator knows it does not answer yet.

    Raises:
        ValidationError: The service does not answer and the operator has not accepted that.
    """
    answer = check_service(auth, get_runner())
    if answer.reachable or accepted:
        return
    raise ValidationError(
        f"The authentication service at {auth.url} does not answer: {answer.detail}",
        details="With it down every visitor is refused. Start it first, or accept that it is "
        "not up yet (--accept-unreachable-auth; in the console, the confirmation box).",
        field="forward_auth",
    )


def _hold_quic_socket(app: App, before: SitePolicy, after: SitePolicy, *, verbose: bool) -> bool:
    """
    Make sure the site that holds the QUIC socket exists when a site turns HTTP/3 on.

    Args:
        app: The application.
        before: The policy in force.
        after: The policy about to be kept.
        verbose: Verbosity of the deployer.

    Returns:
        True when this call wrote it, so a refused change can take it away again.

    Raises:
        ValidationError: This server cannot serve HTTP/3, or nginx refused the socket's site.
    """
    if not after.http3 or before.http3:
        return False
    return Http3(_web_server(app, verbose=verbose)).ensure_anchor()


def _let_go_of_quic_socket(app: App, *, verbose: bool) -> None:
    """
    Take the QUIC socket's site away when no application is served over HTTP/3.

    Args:
        app: The application that stopped using it.
        verbose: Verbosity of the deployer.
    """
    if not _other_sites_use_http3(app):
        Http3(_web_server(app, verbose=verbose)).release_anchor()


def _apply(
    app: App,
    manager: SitePolicyManager,
    before: SitePolicy,
    after: SitePolicy,
    *,
    verbose: bool,
) -> None:
    """
    Keep a policy and render the site from it, putting the old one back on failure.

    Args:
        app: The application.
        manager: The policy manager.
        before: The policy in force.
        after: The policy to keep.
        verbose: Verbosity of the deployer.

    Raises:
        ValidationError: The web server refused the new site; the policy is back.
        NoustError: Anything else the render raises; the policy is back.
    """
    actor = audit_actor()
    manager.save(app.domain, after, actor=actor)
    try:
        domains.refresh_site(app, verbose=verbose)
    except _APPLY_ERRORS:
        manager.save(app.domain, before, actor=actor)
        raise


def _changed_fields(before: SitePolicy, after: SitePolicy) -> set[str]:
    """
    Args:
        before: The policy before.
        after: The policy after.

    Returns:
        The names of the fields that differ.
    """
    old, new = before.to_dict(), after.to_dict()
    return {key for key in new if new[key] != old.get(key)}


def _unsupported(app: App, *, verbose: bool) -> str:
    """
    Say why a policy would not reach an application's site, if it would not.

    Args:
        app: The application.
        verbose: Verbosity of the deployer.

    Returns:
        The reason, or an empty string when it reaches the site.
    """
    if app.app_type in UNSUPPORTED_TYPES:
        return f"{app.app_type} applications have several server blocks of their own"
    try:
        if domains.operator_wrote_site(app, verbose=verbose):
            return (
                "the site was written by hand and Noust never rewrites it; "
                "put these directives in its own file"
            )
    except NoustError as exc:
        return exc.message
    return ""


def _require_applicable(app: App, policy: SitePolicy, *, verbose: bool) -> None:
    """
    Refuse a policy the application's site cannot carry.

    Args:
        app: The application.
        policy: The policy about to be kept.
        verbose: Verbosity of the deployer.

    Raises:
        ValidationError: The site is the operator's, is a monorepo's, or the
            policy asks nginx for what Apache does not have.
    """
    reason = _unsupported(app, verbose=verbose)
    if reason:
        raise ValidationError(
            f"A policy would change nothing on {app.domain}: {reason}",
            field="policy",
        )
    for route in policy.routes:
        if route.kind != "proxy":
            continue
        owner = reserved_ports(get_runner()).get(route.port)
        if owner is not None:
            raise ValidationError(
                f"{route.path}: port {route.port} is {owner}",
                details="Handing a prefix to it would expose that service on this site. "
                "Name the port of the service that should answer.",
                field="routes",
            )
    if policy.forward_auth is not None:
        if app.webserver == "apache":
            raise ValidationError(
                "Delegated authentication is nginx's here: Apache has no auth_request",
                details=f"{app.domain} is served by Apache. Move it to nginx to use an "
                "authentication service.",
                field="forward_auth",
            )
        host, port = policy.forward_auth.host_and_port
        owner = reserved_ports(get_runner()).get(port) if port and host in LOCAL_HOSTS else None
        if owner is not None:
            raise ValidationError(
                f"The authentication service cannot be on port {port}: it is {owner}",
                details="Asking it about every request would loop back into that service.",
                field="forward_auth",
            )
    if policy.http3 and app.webserver == "apache":
        raise ValidationError(
            "HTTP/3 is nginx's: Apache's stock builds have no QUIC",
            details=f"{app.domain} is served by Apache. Move it to nginx 1.25 or later to use HTTP/3.",
            field="http3",
        )
    if policy.client_certs:
        if app.webserver == "apache":
            raise ValidationError(
                "Client certificates are nginx's here: Noust drives them through nginx",
                details=f"{app.domain} is served by Apache. Move it to nginx to ask for them.",
                field="client_certs",
            )
        if not _serves_tls(app):
            raise ValidationError(
                "A client certificate is asked for in the TLS handshake: the site needs HTTPS first",
                details="Give the site a certificate: noust cert use APP --acme",
                field="client_certs",
            )
    if policy.waf and app.webserver == "apache":
        raise ValidationError(
            "The WAF is nginx's here: Noust drives ModSecurity through nginx",
            details=f"{app.domain} is served by Apache. Move it to nginx to use the WAF.",
            field="waf",
        )
    if app.webserver == "apache" and (policy.rate_limit or policy.connection_limit):
        raise ValidationError(
            "Rate and connection limits are nginx's: Apache has no equivalent in its core",
            details=f"{app.domain} is served by Apache. Move it to nginx to limit requests.",
            field="rate_limit" if policy.rate_limit else "connection_limit",
        )


def _check_canonical(app: App, canonical: str) -> None:
    """
    Refuse a canonical spelling that cannot be served.

    Args:
        app: The application.
        canonical: ``www``, ``apex`` or empty.

    Raises:
        ValidationError: The name has no www spelling.
    """
    if canonical:
        check_canonical_possible(app.domain)


def _serves_tls(app: App) -> bool:
    """
    Args:
        app: The application.

    Returns:
        Whether its site is recorded as serving TLS.
    """
    site = get_store().get_site(app.domain)
    return bool(site.ssl_enabled if site is not None else app.ssl_enabled)


__all__ = [
    "AccessView",
    "client_authority",
    "client_certificates",
    "describe",
    "forget",
    "http3_support",
    "install_waf",
    "issue_client_certificate",
    "refresh_trusted_ranges",
    "remove_user",
    "reset_policy",
    "revoke_client_certificate",
    "set_policy",
    "set_user",
    "ssh_client_address",
    "waf_events",
    "waf_support",
]
