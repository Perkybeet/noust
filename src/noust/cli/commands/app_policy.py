# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app policy``: who may reach an application's site, and how.

A password for the whole site, lists of addresses allowed or refused, the real
client address behind Cloudflare or another proxy, a rate limit, the response
headers, and which of ``www.example.com`` and ``example.com`` is the one served.
Each is :mod:`noust.deployers.access`, which ``/api/apps/{d}/policy`` calls too:
the change is checked, the site rendered again exactly as a deploy renders it,
and tested by the web server before it is reloaded (a change it refuses is put
back). This module only parses, presents and asks.
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.deployers import access
from noust.managers import trusted_proxies
from noust.managers.site_policy import MAX_PASSWORD
from noust.managers.site_routes import SiteRoute, normal_path, route_from
from noust.validators.domain import validate_domain


def _print(ctx: Context, view: access.AccessView) -> None:
    """
    Show a policy, for a human or as JSON.

    Args:
        ctx: The CLI context.
        view: What the operation returned.
    """
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    logger: Logger = ctx.logger
    policy = view.policy
    logger.key_value("Application", view.app)
    logger.key_value("Web server", view.webserver)
    if not view.applicable:
        logger.warning(f"A policy does not reach this site: {view.reason}")
    logger.key_value(
        "Password",
        f"asked ({policy.realm!r}), accounts: {', '.join(view.users) or 'none'}"
        if policy.basic_auth
        else f"not asked (accounts kept: {', '.join(view.users) or 'none'})",
    )
    logger.key_value("Allowed addresses", ", ".join(policy.allow_ips) or "everyone")
    if policy.deny_ips:
        logger.key_value("Refused addresses", ", ".join(policy.deny_ips))
    if policy.basic_auth and policy.allow_ips:
        logger.key_value(
            "Both lists apply", "both must pass" if policy.access == "all" else "either lets in"
        )
    if policy.real_ip == "off":
        logger.key_value("Client address", "as the connection says")
    else:
        source = policy.real_ip_header or "CF-Connecting-IP"
        logger.key_value(
            "Client address",
            f"from the {source} header, believed from {len(view.real_ip_networks)} networks "
            f"({view.real_ip_source or 'yours'})",
        )
    logger.key_value(
        "Rate limit",
        f"{policy.rate_limit} (burst {policy.rate_burst})" if policy.rate_limit else "none",
    )
    logger.key_value("Connections per address", str(policy.connection_limit or "no limit"))
    logger.key_value(
        "Canonical name",
        {"www": "www", "apex": "without www", "": "both as they are"}[policy.canonical],
    )
    logger.key_value("Search engines", "told not to index" if policy.noindex else "may index")
    logger.blank()
    logger.table(["Header", "Value"], [[name, value] for name, value in view.headers])
    if policy.routes:
        logger.blank()
        _print_routes(ctx, view)


def _route_summary(route: SiteRoute) -> str:
    """
    Args:
        route: A route.

    Returns:
        What happens at its prefix, in a few words.
    """
    if route.kind == "proxy":
        text = f"served by port {route.port}" + (", prefix removed" if route.strip else "")
    elif route.kind == "redirect":
        text = f"{route.code} to {route.to}" + ("" if route.keep_path else " (path dropped)")
    else:
        text = "the site"
    parts = [text]
    if route.auth != "inherit":
        parts.append("password asked" if route.auth == "password" else "no password")
    if route.headers:
        parts.append("headers: " + ", ".join(route.headers))
    return "; ".join(parts)


def _print_routes(ctx: Context, view: access.AccessView) -> None:
    """
    Show the routes of a policy.

    Args:
        ctx: The CLI context.
        view: What the operation returned.
    """
    ctx.logger.table(
        ["Prefix", "What happens"],
        [[route.path, _route_summary(route)] for route in view.policy.routes],
    )


def _headers(
    base: dict[str, Any], header: tuple[str, ...], remove: tuple[str, ...], hide: tuple[str, ...]
) -> dict[str, Any]:
    """
    Work out the operator's headers after the flags.

    Args:
        base: The headers now set.
        header: ``Name: value`` to set.
        remove: Names of operator headers to stop setting.
        hide: Names of default headers to stop sending.

    Returns:
        The new mapping, a value of None meaning a default is not sent.

    Raises:
        click.UsageError: A ``--header`` is not ``Name: value``.
    """
    merged = dict(base)
    for text in header:
        name, separator, value = text.partition(":")
        if not separator:
            raise click.UsageError(f"--header wants 'Name: value', not {text!r}")
        merged[name.strip()] = value.strip()
    for name in remove:
        merged.pop(name, None)
    for name in hide:
        merged[name] = None
    return merged


@click.group("policy", cls=NoustGroup)
def policy() -> None:
    """Who may reach an application's site and how: password, addresses, limits, headers."""


@policy.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the policy as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show an application's policy, its accounts and the headers its site sends."""
    _print(ctx, access.describe(validate_domain(domain)))


@policy.command("set")
@click.argument("domain")
@click.option("--password/--no-password", "basic_auth", default=None, help="Ask for a password.")
@click.option("--realm", help="The sign-in prompt's title.")
@click.option(
    "--access",
    "access_mode",
    type=click.Choice(["all", "any"]),
    help="all: the password and the address list must both pass; any: either lets in.",
)
@click.option("--allow", "allow", multiple=True, metavar="ADDR", help="Allow only these (repeat).")
@click.option("--clear-allow", is_flag=True, help="Allow everyone again.")
@click.option("--deny", "deny", multiple=True, metavar="ADDR", help="Refuse these (repeat).")
@click.option("--clear-deny", is_flag=True, help="Refuse no one.")
@click.option(
    "--real-ip",
    type=click.Choice(["off", "cloudflare", "custom"]),
    help="Read the client address from a header, believed from Cloudflare or your own proxies.",
)
@click.option("--real-ip-header", help="The header (custom): X-Forwarded-For, X-Real-IP...")
@click.option("--trusted-proxy", "trusted", multiple=True, metavar="NET", help="A proxy (custom).")
@click.option(
    "--rate-limit", metavar="RATE", help="10r/s or 300r/m per client address; none clears."
)
@click.option("--rate-burst", type=int, help="Requests above the rate let through first.")
@click.option(
    "--connection-limit", type=int, help="Concurrent connections per address; 0 for none."
)
@click.option("--header", "header", multiple=True, metavar="'NAME: VALUE'", help="Send a header.")
@click.option(
    "--remove-header", "remove", multiple=True, metavar="NAME", help="Stop sending yours."
)
@click.option("--hide-header", "hide", multiple=True, metavar="NAME", help="Do not send a default.")
@click.option(
    "--hsts", type=click.Choice(["default", "on", "off"]), help="Strict-Transport-Security."
)
@click.option(
    "--noindex/--index", "noindex", default=None, help="Tell search engines not to index."
)
@click.option(
    "--canonical",
    type=click.Choice(["www", "apex", "none"]),
    help="Serve www or the bare name, redirecting the other.",
)
@click.option(
    "--accept-lockout",
    is_flag=True,
    help="Go ahead although the lists refuse the address you are connected from.",
)
@json_option("Print the policy now in force as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    basic_auth: bool | None,
    realm: str | None,
    access_mode: str | None,
    allow: tuple[str, ...],
    clear_allow: bool,
    deny: tuple[str, ...],
    clear_deny: bool,
    real_ip: str | None,
    real_ip_header: str | None,
    trusted: tuple[str, ...],
    rate_limit: str | None,
    rate_burst: int | None,
    connection_limit: int | None,
    header: tuple[str, ...],
    remove: tuple[str, ...],
    hide: tuple[str, ...],
    hsts: str | None,
    noindex: bool | None,
    canonical: str | None,
    accept_lockout: bool,
) -> None:
    """
    Change an application's policy: what you name changes, the rest stays.

    The site is rendered again and tested by the web server before it is
    reloaded; a change it refuses is put back. When you are connected over SSH,
    your address is checked against the lists: one that would refuse you is
    rejected unless you pass --accept-lockout.
    """
    validated = validate_domain(domain)
    changes: dict[str, Any] = {}
    for name, value in (
        ("basic_auth", basic_auth),
        ("realm", realm),
        ("access", access_mode),
        ("real_ip", real_ip),
        ("real_ip_header", real_ip_header),
        ("rate_burst", rate_burst),
        ("connection_limit", connection_limit),
        ("noindex", noindex),
    ):
        if value is not None:
            changes[name] = value
    if allow or clear_allow:
        changes["allow_ips"] = [] if clear_allow else list(allow)
    if deny or clear_deny:
        changes["deny_ips"] = [] if clear_deny else list(deny)
    if trusted:
        changes["trusted_proxies"] = list(trusted)
    if rate_limit is not None:
        changes["rate_limit"] = "" if rate_limit.lower() == "none" else rate_limit
    if canonical is not None:
        changes["canonical"] = "" if canonical == "none" else canonical
    if hsts is not None:
        changes["hsts"] = {"default": None, "on": True, "off": False}[hsts]
    if header or remove or hide:
        current = access.describe(validated).policy.headers
        changes["headers"] = _headers(dict(current), header, remove, hide)
    if not changes:
        raise click.UsageError("Name at least one thing to change (see --help)")
    if ctx.dry_run:
        ctx.logger.info(f"Would change the policy of {validated}: {', '.join(sorted(changes))}")
        return
    address = access.ssh_client_address()
    view = access.set_policy(
        validated,
        changes,
        operator_addresses=[address] if address else [],
        accept_lockout=accept_lockout,
        verbose=ctx.verbose,
    )
    if not ctx.json_output:
        ctx.logger.success(f"The site of {validated} is rendered with this policy")
    _print(ctx, view)


@policy.command("reset")
@click.argument("domain")
@json_option("Print the policy now in force as JSON.")
@pass_context
def reset_command(ctx: Context, domain: str) -> None:
    """Take every field of the policy back to its default. The accounts are kept."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would reset the policy of {validated}")
        return
    view = access.reset_policy(validated, verbose=ctx.verbose)
    if not ctx.json_output:
        ctx.logger.success(f"{validated} is served without a policy")
    _print(ctx, view)


@policy.group("route", cls=NoustGroup)
def route() -> None:
    """What happens at a prefix of the site: a password, headers, another service, a redirect."""


@route.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the routes as JSON.")
@pass_context
def route_list_command(ctx: Context, domain: str) -> None:
    """List the routes of an application's site."""
    view = access.describe(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps([item.to_dict() for item in view.policy.routes]))
    elif view.policy.routes:
        _print_routes(ctx, view)
    else:
        ctx.logger.info(f"{view.app} has no routes")


@route.command("add")
@click.argument("domain")
@click.argument("path")
@click.option("--password", "ask", flag_value="password", help="Ask for the password here.")
@click.option("--open", "ask", flag_value="open", help="Lift the site's password here.")
@click.option(
    "--header", "header", multiple=True, metavar="'NAME: VALUE'", help="Send a header here."
)
@click.option("--proxy-port", type=int, metavar="PORT", help="Hand the prefix to this local port.")
@click.option("--strip/--keep-prefix", "strip", default=False, help="Take the prefix off (proxy).")
@click.option("--redirect-to", metavar="URL", help="Redirect the prefix to an address or a path.")
@click.option(
    "--code",
    type=click.Choice(["301", "302", "307", "308"]),
    default="301",
    help="Redirect status.",
)
@click.option("--drop-path", is_flag=True, help="Redirect without the rest of the path.")
@json_option("Print the policy now in force as JSON.")
@pass_context
def route_add_command(
    ctx: Context,
    domain: str,
    path: str,
    ask: str | None,
    header: tuple[str, ...],
    proxy_port: int | None,
    strip: bool,
    redirect_to: str | None,
    code: str,
    drop_path: bool,
) -> None:
    """
    Set what happens at a prefix of the site; a route at the same prefix is replaced.

    With no --proxy-port and no --redirect-to the prefix is served as the rest of the
    site is, with the password asked or lifted and the headers given.
    """
    validated_domain = validate_domain(domain)
    if proxy_port is not None and redirect_to is not None:
        raise click.UsageError("A prefix is handed to a port or redirected, not both")
    data: dict[str, Any] = {"path": path}
    if proxy_port is not None:
        data.update(kind="proxy", port=proxy_port, strip=strip)
    elif redirect_to is not None:
        data.update(kind="redirect", to=redirect_to, code=int(code), keep_path=not drop_path)
    if ask is not None:
        data["auth"] = ask
    if header:
        data["headers"] = {
            name.strip(): value.strip()
            for name, separator, value in (text.partition(":") for text in header)
            if separator
        }
        if len(data["headers"]) != len(header):
            raise click.UsageError("--header wants 'Name: value'")
    current = [item.to_dict() for item in access.describe(validated_domain).policy.routes]
    wanted = route_from(data)
    routes = [item for item in current if item["path"] != wanted.path]
    routes.append(wanted.to_dict())
    _apply_routes(ctx, validated_domain, routes, f"{wanted.path}: {_route_summary(wanted)}")


@route.command("remove")
@click.argument("domain")
@click.argument("path")
@json_option("Print the policy now in force as JSON.")
@pass_context
def route_remove_command(ctx: Context, domain: str, path: str) -> None:
    """Take a route away; the prefix is served as the rest of the site again."""
    validated_domain = validate_domain(domain)
    wanted = normal_path(path)
    current = [item.to_dict() for item in access.describe(validated_domain).policy.routes]
    routes = [item for item in current if item["path"] != wanted]
    if len(routes) == len(current):
        raise ValidationError(
            f"{wanted} is not a route of {validated_domain}",
            details=f"See them with: noust app policy route list {validated_domain}",
            field="routes",
        )
    _apply_routes(ctx, validated_domain, routes, f"{wanted} is the site's again")


def _apply_routes(ctx: Context, domain: str, routes: list[dict[str, Any]], what: str) -> None:
    """
    Keep a new list of routes and render the site.

    Args:
        ctx: The CLI context.
        domain: The application's primary domain.
        routes: The whole list.
        what: What changed, for the confirmation.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would change the routes of {domain}: {what}")
        return
    address = access.ssh_client_address()
    view = access.set_policy(
        domain,
        {"routes": routes},
        operator_addresses=[address] if address else [],
        verbose=ctx.verbose,
    )
    if not ctx.json_output:
        ctx.logger.success(f"{domain}: {what}")
    _print(ctx, view)


@policy.group("user", cls=NoustGroup)
def user() -> None:
    """The accounts of an application's password."""


def _password(from_stdin: bool) -> str:
    """
    Read a password without it appearing in an argument.

    Args:
        from_stdin: Read one line from standard input instead of asking.

    Returns:
        The password.

    Raises:
        ValidationError: Nothing was read, or the two entries differ.
    """
    if from_stdin:
        line = sys.stdin.readline(MAX_PASSWORD + 2)
        if not line.strip("\r\n"):
            raise ValidationError("Nothing was read from standard input", field="password")
        return line.rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Again: "):
        raise ValidationError("The two passwords differ", field="password")
    return first


@user.command("add")
@click.argument("domain")
@click.argument("username")
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input.")
@json_option("Print the policy as JSON.")
@pass_context
def add_user_command(ctx: Context, domain: str, username: str, password_stdin: bool) -> None:
    """Add an account to the application's password, or change its password. Asks for it."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would set the account {username} of {validated}")
        return
    view = access.set_user(validated, username, _password(password_stdin), verbose=ctx.verbose)
    if not ctx.json_output:
        ctx.logger.success(f"{username} can sign in to {validated}")
    _print(ctx, view)


@user.command("remove")
@click.argument("domain")
@click.argument("username")
@json_option("Print the policy as JSON.")
@pass_context
def remove_user_command(ctx: Context, domain: str, username: str) -> None:
    """Remove an account of the application's password."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would remove the account {username} of {validated}")
        return
    view = access.remove_user(validated, username, verbose=ctx.verbose)
    if not ctx.json_output:
        ctx.logger.success(f"{username} no longer signs in to {validated}")
    _print(ctx, view)


@policy.command("ranges", read_only=lambda params: not params["refresh"])
@click.option("--refresh", is_flag=True, help="Fetch Cloudflare's published ranges and apply them.")
@json_option("Print the ranges as JSON.")
@pass_context
def ranges_command(ctx: Context, refresh: bool) -> None:
    """
    Show the networks a site believes Cloudflare's client-address header from.

    With --refresh the list is fetched from cloudflare.com again, and the sites
    that trust it are rendered with the new one. Without it, nothing changes.
    """
    outcome: dict[str, Any] = {}
    if refresh:
        if ctx.dry_run:
            ctx.logger.info("Would fetch Cloudflare's ranges again")
            return
        outcome = access.refresh_trusted_ranges(verbose=ctx.verbose)
    current = trusted_proxies.current_ranges()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "networks": list(current.networks),
                    "source": current.source,
                    "fetched_at": current.fetched_at,
                    "stale": trusted_proxies.is_stale(),
                    **{key: outcome[key] for key in ("changed", "applied", "failed") if outcome},
                }
            )
        )
        return
    ctx.logger.key_value("Source", current.source)
    ctx.logger.key_value("Fetched", current.fetched_at or "never (the built-in list)")
    ctx.logger.key_value("Networks", ", ".join(current.networks))
    if outcome:
        ctx.logger.success(
            "The list changed" if outcome["changed"] else "The list is the same as before"
        )
        for name in outcome["applied"]:
            ctx.logger.substep(f"Rendered {name} again")
        for name, why in outcome["failed"].items():
            ctx.logger.warning(f"{name} was not rendered again: {why}")
