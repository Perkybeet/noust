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
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import NoustError, ValidationError
from noust.core.runner import get_runner
from noust.core.store import App
from noust.deployers import domains
from noust.managers import trusted_proxies
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
from noust.validators.domain import validate_domain

#: App types whose site is not one server block Noust renders for the application:
#: nothing in the policy reaches the file.
UNSUPPORTED_TYPES: frozenset[str] = frozenset({"monorepo"})

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
            verbose=verbose,
        )


def _set_policy(
    domain: str,
    changes: Mapping[str, Any],
    *,
    operator_addresses: Sequence[str],
    accept_lockout: bool,
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
    _apply(app, manager, before, after, verbose=verbose)
    audit.record(
        "sites.policy",
        target=f"app:{app.domain}",
        details={"changed": sorted(_changed_fields(before, after)), "policy": after.to_dict()},
    )
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
        audit.record("sites.policy", target=f"app:{app.domain}", details={"changed": ["reset"]})
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
    return result


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


# -- Internals --------------------------------------------------------------------


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
    from noust.core.store import get_store

    site = get_store().get_site(app.domain)
    return bool(site.ssl_enabled if site is not None else app.ssl_enabled)


__all__ = [
    "AccessView",
    "describe",
    "forget",
    "refresh_trusted_ranges",
    "remove_user",
    "reset_policy",
    "set_policy",
    "set_user",
    "ssh_client_address",
]
