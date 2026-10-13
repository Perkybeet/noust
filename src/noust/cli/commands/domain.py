# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust domain``: the names an application answers on.

A presentation layer over :mod:`noust.deployers.domains`, the functions the
panel's ``/api/apps/{domain}/domains`` endpoints call too. An alias serves the
application exactly like its primary domain; a redirect sends visitors to the
primary with a permanent redirect. Every change re-renders the site the way a
deploy does, is tested by the web server before it is reloaded, and extends
the certificate when the site serves TLS. ``noust create --www`` records
``www.<domain>`` as a redirect.

An application deployed before 2.0 starts with only its primary domain on
record: the ``www`` a 1.x ``--www`` deploy served lived only in the web
server's configuration. The first change made here keeps it, recording it as
an alias, and ``noust domain list`` shows it from then on.

A name can be a wildcard (``*.example.com``), can be proxied to another port of
the application, and the pair ``www`` and apex can have one that is served and one
that redirects (``noust domain canonical``). A name another site of the web server
already answers on is refused unless ``--force`` says it is meant.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import DependencyError, ValidationError
from noust.core.logger import Logger
from noust.core.store import DomainRecord
from noust.deployers.domains import (
    ADDABLE_KINDS,
    DomainChange,
    add_domain,
    check_dns,
    issue_certificate,
    list_domains,
    reduce_certificate,
    remove_domain,
    set_canonical,
    set_domain_port,
)
from noust.validators.domain import is_wildcard


def print_domains(logger: Logger, domains: list[DomainRecord]) -> None:
    """
    Render an application's domains for a human.

    Args:
        logger: Logger the command writes through.
        domains: Its domains, primary first.
    """
    logger.table(
        ["Domain", "Kind", "Port", "Added"],
        [
            [
                record.domain,
                record.kind,
                str(record.port) if record.port else "-",
                record.created_at or "-",
            ]
            for record in domains
        ],
    )


def _report(logger: Logger, change: DomainChange) -> None:
    """
    Say what a change adopted, and what the certificate now covers.

    Args:
        logger: Logger the command writes through.
        change: What the operation did.
    """
    for name in change.adopted:
        logger.info(f"{name} was already served by the site and is now recorded as an alias")
    if change.certificate_issued:
        logger.success("The certificate covers every domain")
    if change.certificate_reduced:
        logger.success("The certificate was ordered again without the name that was removed")
    if change.canonical:
        logger.info(f"Served on the {change.canonical} spelling; the other one redirects to it")
    logger.blank()
    print_domains(logger, list(change.domains))


def _warn_about_dns(logger: Logger, domain: str) -> None:
    """
    Warn, before a name is added, when it does not resolve to this server.

    Only a warning: the operator may be about to change the DNS record, and a
    server behind NAT cannot see its public address to compare.

    Args:
        logger: Logger the command writes through.
        domain: The name about to be added.
    """
    if is_wildcard(domain):
        return
    try:
        dns = check_dns(domain)
    except DependencyError as exc:
        logger.debug(f"DNS check skipped: {exc}")
        return
    if dns.points_here:
        return
    if dns.verdict == "unknown":
        logger.info(
            f"{dns.domain} resolves to {', '.join(dns.resolved_addresses)}; this server sits "
            "behind NAT and cannot tell whether that is its own address"
        )
        logger.info("Declare it to compare: noust config set dns.public_addresses ADDRESS")
        return
    logger.warning(f"{dns.domain} does not resolve to this server")
    logger.key_value("It resolves to", ", ".join(dns.resolved_addresses) or "nothing")
    logger.key_value("This server", ", ".join(dns.expected_addresses) or "no address found")
    logger.info("Visitors and the certificate authority reach it only once DNS points here.")


@click.group("domain", cls=NoustGroup)
def cli() -> None:
    """
    Serve an application on more domains: aliases and redirects.

    An alias serves the application like its primary domain; a redirect sends
    visitors to the primary with a permanent redirect. 'noust create --www'
    records www.<domain> as a redirect.

    An application deployed before 2.0 starts with only its primary domain on
    record. If it was deployed with --www, the first 'noust domain add' or
    'remove' keeps www, recording it as an alias.
    """


@cli.command("list")
@click.argument("app_domain", metavar="APP")
@json_option("Print the domains as JSON.")
@pass_context
def list_command(ctx: Context, app_domain: str) -> None:
    """
    List the domains APP answers on.

    The primary domain comes first, then the aliases, then the redirects.
    """
    domains = list_domains(app_domain)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "app": app_domain,
                    "items": [
                        {
                            "domain": r.domain,
                            "kind": r.kind,
                            "port": r.port,
                            "created_at": r.created_at,
                        }
                        for r in domains
                    ],
                }
            )
        )
        return
    print_domains(ctx.logger, domains)


@cli.command("add")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@click.option(
    "--kind",
    type=click.Choice(ADDABLE_KINDS),
    default=ADDABLE_KINDS[0],
    show_default=True,
    help="alias serves the application on DOMAIN; redirect sends DOMAIN to APP.",
)
@click.option(
    "--no-cert",
    is_flag=True,
    help="Do not extend the certificate now. Run the same command again to extend it.",
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    help="Proxy this name to another port of the application than its own (an alias).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Add the name although another site of the web server already answers on it.",
)
@click.option(
    "--no-dns",
    is_flag=True,
    help="Do not point the name at this server in the DNS account that holds its zone.",
)
@pass_context
def add_command(
    ctx: Context,
    app_domain: str,
    domain: str,
    kind: str,
    no_cert: bool,
    port: int | None,
    force: bool,
    no_dns: bool,
) -> None:
    """
    Make APP answer on DOMAIN too.

    DOMAIN can be a wildcard such as '*.example.com' (quote it): an alias that takes
    every name one label under it. Its certificate needs the DNS challenge, so choose
    it first: noust cert use APP --acme --dns ACCOUNT.

    The site is re-rendered and tested before the web server reloads it. When
    APP is served over TLS, its certificate is extended to cover DOMAIN; if
    that fails, DOMAIN stays and the command can be run again once DNS points
    here. Adding a name APP already has retries its certificate. When a DNS account
    that creates records without asking holds the zone, the record is made too.
    """
    logger = ctx.logger
    _warn_about_dns(logger, domain)
    change = add_domain(
        app_domain,
        domain,
        kind,
        issue_cert=not no_cert,
        force=force,
        dns=not no_dns,
        port=port,
        logger=logger,
        verbose=ctx.verbose,
    )
    if change.certificate_error is not None:
        logger.error(
            "The certificate was not extended to cover it", details=change.certificate_error
        )
        if is_wildcard(domain):
            logger.info(
                "A wildcard is proven with a DNS record: "
                f"noust cert use {change.app} --acme --dns ACCOUNT, then run this again"
            )
        else:
            logger.info(f"Once DNS points here, run again: noust domain add {change.app} {domain}")
    elif change.tls and no_cert:
        logger.info(f"Extend the certificate later with: noust domain add {change.app} {domain}")
    _report(logger, change)


@cli.command("remove")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@pass_context
def remove_command(ctx: Context, app_domain: str, domain: str) -> None:
    """
    Stop APP answering on DOMAIN.

    The primary domain cannot be removed; delete the application instead. When
    APP is served over TLS its certificate is ordered again without DOMAIN, and the
    DNS record Noust made for it is deleted if nothing else uses it. Nothing is
    revoked: the certificate that covered the name expires on its own.
    """
    logger = ctx.logger
    change = remove_domain(app_domain, domain, logger=logger, verbose=ctx.verbose)
    if change.certificate_error is not None:
        logger.error(
            "The certificate still names the removed domain", details=change.certificate_error
        )
        logger.info(f"Order it again later with: noust domain certificate {change.app} --reduce")
    elif change.tls and not change.certificate_reduced:
        logger.info("The certificate was not revoked; it covers the name until it is next issued")
    _report(logger, change)


@cli.command("canonical")
@click.argument("app_domain", metavar="APP")
@click.argument("spelling", type=click.Choice(["www", "apex", "none"]))
@click.option(
    "--no-cert", is_flag=True, help="Do not extend the certificate to cover both names now."
)
@pass_context
def canonical_command(ctx: Context, app_domain: str, spelling: str, no_cert: bool) -> None:
    """
    Choose whether www.<domain> or <domain> is served; the other one redirects to it.

    Both names are recorded, so the certificate covers both and the redirect is served
    over TLS too. 'none' serves both as they are recorded. Visitors and search engines
    then see one address for the site.
    """
    change = set_canonical(
        app_domain, spelling, issue_cert=not no_cert, logger=ctx.logger, verbose=ctx.verbose
    )
    if change.certificate_error is not None:
        ctx.logger.error(
            "The certificate was not extended to cover both names",
            details=change.certificate_error,
        )
        ctx.logger.info(f"Once DNS points here, run: noust domain certificate {change.app}")
    _report(ctx.logger, change)


@cli.command("port")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@click.argument("port", metavar="PORT|none")
@pass_context
def port_command(ctx: Context, app_domain: str, domain: str, port: str) -> None:
    """
    Proxy an alias to another port of the application, or back to its own with 'none'.

    For an application with a second listener (an admin interface, an API on its own
    port) that should answer on a name of its own.
    """
    chosen: int | None
    if port.lower() == "none":
        chosen = None
    else:
        try:
            chosen = int(port)
        except ValueError:
            raise click.UsageError(f"{port!r} is neither a port nor 'none'") from None
        if not 1 <= chosen <= 65535:
            raise ValidationError(
                f"{chosen} is not a port", details="A port is 1 to 65535.", field="port"
            )
    change = set_domain_port(app_domain, domain, chosen, verbose=ctx.verbose)
    ctx.logger.success(
        f"{domain} is proxied to port {chosen}"
        if chosen
        else f"{domain} uses the application's own port"
    )
    _report(ctx.logger, change)


@cli.command("certificate")
@click.argument("app_domain", metavar="APP")
@click.option(
    "--reduce",
    is_flag=True,
    help="Order it again with only the names the application answers on (after a removal).",
)
@pass_context
def certificate_command(ctx: Context, app_domain: str, reduce: bool) -> None:
    """
    Make APP's certificate cover exactly the names it answers on.

    Without --reduce the certificate is extended to every domain (what 'domain add'
    does when it is not told --no-cert). With it, a certificate that still names a
    domain that was removed is ordered again without it.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would bring the certificate of {app_domain} in line with its domains")
        return
    if reduce:
        change = reduce_certificate(app_domain, logger=ctx.logger, verbose=ctx.verbose)
        if not change.certificate_reduced:
            ctx.logger.info("The certificate names nothing that was removed")
    else:
        change = issue_certificate(app_domain, logger=ctx.logger, verbose=ctx.verbose)
    _report(ctx.logger, change)
