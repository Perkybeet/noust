# Copyright (c) 2024-2026 Yago López Prado
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
    disable_domain,
    enable_domain,
    issue_certificate,
    list_domains,
    reduce_certificate,
    remove_domain,
    set_canonical,
    set_domain_port,
)
from noust.deployers.name_certificates import NameBinding, bindings, set_certificate
from noust.deployers.temporary_domain import is_temporary, suggest
from noust.validators.domain import is_wildcard


def _shown(record: DomainRecord) -> str:
    """
    Args:
        record: One of the domains.

    Returns:
        Its name, with what the operator should know beside it: that it is a temporary name,
        that it is switched off.
    """
    marks = []
    if is_temporary(record.domain):
        marks.append("temporary")
    if not record.enabled:
        marks.append("off")
    return f"{record.domain} ({', '.join(marks)})" if marks else record.domain


def _covered_by(binding: NameBinding | None, record: DomainRecord) -> str:
    """
    Args:
        binding: Which certificate serves the name, when it is known.
        record: The name's row.

    Returns:
        What the Certificate column says: the certificate that serves it, and that it is the
        name's own, or that the name asked for one that is not there yet.
    """
    if binding is None or binding.served_by is None:
        return record.certificate
    if binding.pending:
        return f"{binding.served_by} (own, not issued yet)"
    if binding.certificate == "own":
        return f"{binding.served_by} (own)"
    return binding.served_by


def print_domains(logger: Logger, domains: list[DomainRecord], app: str | None = None) -> None:
    """
    Render an application's domains for a human.

    Args:
        logger: Logger the command writes through.
        domains: Its domains, primary first.
        app: The application's primary domain, to say which certificate serves each name.
    """
    served = {binding.name: binding for binding in bindings(app)} if app else {}
    logger.table(
        ["Domain", "Kind", "Port", "Certificate", "Added"],
        [
            [
                _shown(record),
                record.kind,
                str(record.port) if record.port else "-",
                _covered_by(served.get(record.domain), record),
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
    if change.lineage_removed:
        logger.success("The certificate the name held on its own was deleted (nothing is revoked)")
    if change.canonical:
        logger.info(f"Served on the {change.canonical} spelling; the other one redirects to it")
    logger.blank()
    print_domains(logger, list(change.domains), change.app)


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
        served = {binding.name: binding for binding in bindings(app_domain)}
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
                            "temporary": is_temporary(r.domain),
                            "enabled": r.enabled,
                            "certificate": r.certificate,
                            "served_by": served[r.domain].served_by if r.domain in served else None,
                            "certificate_pending": served[r.domain].pending
                            if r.domain in served
                            else False,
                        }
                        for r in domains
                    ],
                }
            )
        )
        return
    print_domains(ctx.logger, domains, app_domain)


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
@click.option(
    "--own-certificate",
    is_flag=True,
    help="Give DOMAIN a certificate of its own instead of covering it with APP's.",
)
@click.option(
    "--dns",
    "dns_account",
    metavar="ACCOUNT",
    help="With --own-certificate: prove it with a DNS record in ACCOUNT (a wildcard needs it).",
)
@click.option(
    "--http", is_flag=True, help="With --own-certificate: prove it over HTTP (the default)."
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
    own_certificate: bool,
    dns_account: str | None,
    http: bool,
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

    With --own-certificate the name is covered by a certificate of its own, kept under its
    own name, and APP's shared certificate never names it: a name that should not appear
    beside the others, or whose DNS may stop pointing here without failing their renewal.
    """
    logger = ctx.logger
    if (dns_account or http) and not own_certificate:
        raise click.UsageError("--dns and --http choose how --own-certificate is proven")
    if dns_account and http:
        raise click.UsageError("Choose --dns ACCOUNT or --http, not both")
    _warn_about_dns(logger, domain)
    change = add_domain(
        app_domain,
        domain,
        kind,
        issue_cert=not no_cert,
        force=force,
        dns=not no_dns,
        port=port,
        certificate="own" if own_certificate else "shared",
        challenge=_challenge(dns_account, http),
        dns_provider=dns_account,
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


@cli.command(
    "temporary", read_only=lambda params: bool(params["show"]) or params["app_domain"] is None
)
@click.argument("app_domain", metavar="APP", required=False)
@click.option("--show", is_flag=True, help="Only say the name, without adding it to anything.")
@click.option(
    "--no-cert",
    is_flag=True,
    help="Do not extend the certificate now. Run the same command again to extend it.",
)
@json_option("Print the name as JSON (with --show).")
@pass_context
def temporary_command(ctx: Context, app_domain: str | None, show: bool, no_cert: bool) -> None:
    """
    Give APP a temporary name made of this server's address, for a server with no domain yet.

    The name is the public address written as a host name under sslip.io
    (203-0-113-10.sslip.io), which resolves to that address, so a certificate can be ordered
    for it. It is an alias like any other and the console and 'noust domain list' mark it
    temporary: a certificate authority limits certificates per registered domain, and
    sslip.io is everybody's, so use it to try things out. A server behind NAT declares its
    public address first: noust config set dns.public_addresses ADDRESS.
    """
    found = suggest()
    if show or app_domain is None:
        if ctx.json_output:
            click.echo(
                json.dumps(
                    {
                        "address": found.address,
                        "domain": found.domain,
                        "service": found.service,
                        "declared": found.declared,
                    }
                )
            )
        else:
            ctx.logger.key_value("Public address", found.address)
            ctx.logger.key_value("Temporary name", found.domain)
            if app_domain is None:
                ctx.logger.info("Give it to an application: noust domain temporary APP")
        return
    ctx.logger.info(f"Using {found.domain}, made of {found.address} ({found.service})")
    change = add_domain(
        app_domain,
        found.domain,
        ADDABLE_KINDS[0],
        issue_cert=not no_cert,
        logger=ctx.logger,
        verbose=ctx.verbose,
    )
    if change.certificate_error is not None:
        ctx.logger.error(
            "The certificate was not extended to cover it", details=change.certificate_error
        )
    _report(ctx.logger, change)


@cli.command("remove")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@pass_context
def remove_command(ctx: Context, app_domain: str, domain: str) -> None:
    """
    Stop APP answering on DOMAIN.

    The primary domain cannot be removed; delete the application instead. When
    APP is served over TLS its certificate is ordered again without DOMAIN, and the
    DNS record Noust made for it is deleted if nothing else uses it. A name that held a
    certificate of its own takes only that one with it, and APP's is left alone. Nothing is
    revoked: the certificate that covered the name expires on its own.
    """
    logger = ctx.logger
    change = remove_domain(app_domain, domain, logger=logger, verbose=ctx.verbose)
    if change.certificate_error is not None:
        logger.error(
            "The certificate still names the removed domain", details=change.certificate_error
        )
        logger.info(f"Order it again later with: noust domain certificate {change.app} --reduce")
    elif change.tls and not change.certificate_reduced and not change.lineage_removed:
        logger.info("The certificate was not revoked; it covers the name until it is next issued")
    _report(logger, change)


@cli.command("disable")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@pass_context
def disable_command(ctx: Context, app_domain: str, domain: str) -> None:
    """
    Stop APP answering on DOMAIN without forgetting it.

    The name keeps its kind, its port and its DNS record; the site and the certificate leave
    it out until 'noust domain enable'. The primary domain and the canonical www/apex pair
    cannot be switched off. Nothing is revoked.
    """
    logger = ctx.logger
    change = disable_domain(app_domain, domain, logger=logger, verbose=ctx.verbose)
    if change.certificate_error is not None:
        logger.error(
            "The certificate still names the switched-off domain", details=change.certificate_error
        )
        logger.info(f"Order it again later with: noust domain certificate {change.app} --reduce")
    _report(logger, change)


@cli.command("enable")
@click.argument("app_domain", metavar="APP")
@click.argument("domain")
@click.option(
    "--no-cert", is_flag=True, help="Do not extend the certificate now. Run the command again to."
)
@pass_context
def enable_command(ctx: Context, app_domain: str, domain: str, no_cert: bool) -> None:
    """
    Serve DOMAIN again, after 'noust domain disable'.

    When APP is served over TLS its certificate is extended to cover DOMAIN; if that fails the
    name stays on and the command can be run again once DNS points here.
    """
    logger = ctx.logger
    change = enable_domain(
        app_domain, domain, issue_cert=not no_cert, logger=logger, verbose=ctx.verbose
    )
    if change.certificate_error is not None:
        logger.error(
            "The certificate was not extended to cover it", details=change.certificate_error
        )
        logger.info(f"Once DNS points here, run again: noust domain enable {change.app} {domain}")
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


def _challenge(dns_account: str | None, http: bool) -> str | None:
    """
    Args:
        dns_account: The DNS account asked for, if any.
        http: Whether HTTP was asked for.

    Returns:
        The challenge those options choose, or None to leave it to the application's own way.
    """
    if dns_account:
        return "dns-01"
    return "http-01" if http else None


def _name_certificate_is_read(params: dict[str, object]) -> bool:
    """
    Args:
        params: The command's parsed parameters.

    Returns:
        True when the command only says how a name is covered, so it is not audited as a change.
    """
    return bool(params.get("name")) and not params.get("mode") and not params.get("reduce")


@cli.command("certificate", read_only=_name_certificate_is_read)
@click.argument("app_domain", metavar="APP")
@click.argument("name", required=False, metavar="[NAME]")
@click.option(
    "--own",
    "mode",
    flag_value="own",
    help="Give NAME a certificate of its own, kept under its own name.",
)
@click.option(
    "--shared",
    "mode",
    flag_value="shared",
    help="Cover NAME with APP's certificate again, and delete the one it had.",
)
@click.option(
    "--dns",
    "dns_account",
    metavar="ACCOUNT",
    help="With --own: prove it with a DNS record in ACCOUNT (a wildcard needs it).",
)
@click.option("--http", is_flag=True, help="With --own: prove it over HTTP.")
@click.option(
    "--reduce",
    is_flag=True,
    help="Order it again with only the names the application answers on (after a removal).",
)
@pass_context
def certificate_command(
    ctx: Context,
    app_domain: str,
    name: str | None,
    mode: str | None,
    dns_account: str | None,
    http: bool,
    reduce: bool,
) -> None:
    """
    Make APP's certificate cover exactly the names it answers on, or say how one name is covered.

    Without NAME and --reduce the certificate is extended to every domain (what 'domain add'
    does when it is not told --no-cert). With --reduce, a certificate that still names a
    domain that was removed is ordered again without it.

    With NAME alone it says which certificate serves that name. With --own, NAME gets a
    certificate of its own: it is ordered while the site goes on serving the name as it
    did, and only then does the site get a server block for it; if the order fails nothing
    changes. APP's certificate is ordered again without it. --shared does the opposite: APP's
    certificate is extended to cover NAME first, and the one NAME had is deleted at the end.
    """
    if name is None:
        if mode or dns_account or http:
            raise click.UsageError("--own, --shared, --dns and --http need the NAME of a domain")
        _certificate_of_the_application(ctx, app_domain, reduce)
        return
    if reduce:
        raise click.UsageError("--reduce is for the whole application, not for one name")
    if http and dns_account:
        raise click.UsageError("Choose --dns ACCOUNT or --http, not both")
    if mode != "own" and (dns_account or http):
        raise click.UsageError("--dns and --http choose how --own is proven")
    if mode is None:
        _say_how_it_is_covered(ctx, app_domain, name)
        return
    if ctx.dry_run:
        ctx.logger.info(
            f"Would give {name} {'a certificate of its own' if mode == 'own' else 'the shared certificate'}"
        )
        return
    change = set_certificate(
        app_domain,
        name,
        mode,
        challenge=_challenge(dns_account, http),
        dns_provider=dns_account,
        logger=ctx.logger,
        verbose=ctx.verbose,
    )
    for warning in change.warnings:
        ctx.logger.warning(warning)
    if mode == "own" and not change.tls:
        ctx.logger.info(
            f"{app_domain} serves no TLS yet: its first certificate orders this one too: "
            f"noust cert create -d {app_domain}"
        )
    ctx.logger.blank()
    print_domains(ctx.logger, list_domains(app_domain), app_domain)


def _certificate_of_the_application(ctx: Context, app_domain: str, reduce: bool) -> None:
    """
    Bring the certificate of the application in line with its names.

    Args:
        ctx: The command's context.
        app_domain: The application's primary domain.
        reduce: Order it again with only the names it answers on.
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


def _say_how_it_is_covered(ctx: Context, app_domain: str, name: str) -> None:
    """
    Say which certificate serves one name.

    Args:
        ctx: The command's context.
        app_domain: The application's primary domain.
        name: The name.

    Raises:
        ValidationError: The name is not one of the application's.
    """
    found = {binding.name: binding for binding in bindings(app_domain)}
    binding = found.get(name.strip().lower())
    if binding is None:
        raise ValidationError(
            f"{name} is not a domain of {app_domain}",
            details=f"See the domains it answers on with: noust domain list {app_domain}",
            field="name",
        )
    logger = ctx.logger
    logger.key_value("Name", binding.name)
    logger.key_value("Certificate", binding.certificate)
    logger.key_value("Served by", binding.served_by or "no certificate")
    if binding.pending:
        logger.info(
            "It asked for a certificate of its own that is not issued yet, so the shared one "
            f"serves it meanwhile: noust domain certificate {app_domain}"
        )
