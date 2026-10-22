# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust dns``: DNS accounts, the records Noust manages in them, and the DNS-01 challenge.

A presentation layer over :mod:`noust.managers.dns`, which ``/api/dns`` calls too.
An account is a provider and a credential; the credential is proven when it is added
and kept only in the secret store. Noust creates and deletes only records it can prove
are its own (see :mod:`noust.managers.dns.records`), so every command that could touch a
record says what it would do before it does it (``--plan``), and replacing a record that
somebody else made is a confirmation that names the record.

Secrets never come as arguments - everything on a command line is visible in ``ps`` to
every local user. They come from a hidden prompt, or from standard input.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.deployers.domains import check_dns
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.catalog import KINDS, ProviderKind
from noust.managers.dns.challenge import DnsChallenge, read_environment
from noust.managers.dns.records import DnsRecordConflict, ManagedDns, server_addresses
from noust.validators.domain import validate_domain


@click.group("dns", cls=NoustGroup)
def cli() -> None:
    """DNS accounts, the records Noust manages in them, and the DNS-01 challenge."""


@cli.group("provider", cls=NoustGroup)
def provider() -> None:
    """The DNS accounts Noust holds: Cloudflare, DigitalOcean, Hetzner, Gandi and Route 53 (records and the challenge, Cloudflare also tunnels), and certbot's plugins."""


def _credentials(
    kind: str, pairs: tuple[str, ...], token_stdin: bool, from_stdin: bool
) -> dict[str, str]:
    """
    Gather an account's credentials without any secret reaching a command line.

    Args:
        kind: The provider.
        pairs: ``name=value`` for the fields that are not secret.
        token_stdin: Read the kind's first secret field from one line of standard input.
        from_stdin: Read every field from a JSON object on standard input.

    Returns:
        The fields; secret ones not given are asked for on a hidden prompt.

    Raises:
        click.UsageError: The kind is unknown, a pair is not ``name=value``, or the input is not JSON.
    """
    spec = KINDS.get(kind)
    if spec is None:
        raise click.UsageError(
            f"Not a provider Noust knows: {kind}. Known: {', '.join(sorted(KINDS))}"
        )
    given: dict[str, str] = {}
    if from_stdin:
        try:
            loaded = json.loads(sys.stdin.read(65536))
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"Standard input is not a JSON object: {exc}") from exc
        if not isinstance(loaded, dict):
            raise click.UsageError("Standard input is not a JSON object")
        given.update({str(k): str(v) for k, v in loaded.items()})
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator:
            raise click.UsageError(f"--set wants name=value, not {pair!r}")
        field = next((f for f in spec.fields if f.name == name), None)
        if field is not None and field.secret:
            raise click.UsageError(
                f"{name} is a credential: it does not go in an argument. "
                "Use --token-stdin, --credentials-stdin or the prompt."
            )
        given[name] = value
    secrets = [f for f in spec.fields if f.secret]
    if token_stdin and secrets:
        line = sys.stdin.readline(4096).rstrip("\r\n")
        if not line:
            raise click.UsageError("Nothing was read from standard input")
        given[secrets[0].name] = line
    for field in secrets:
        if field.name not in given and field.required and sys.stdin.isatty():
            given[field.name] = getpass.getpass(f"{field.label}: ")
    return given


@provider.command("list", read_only=True)
@json_option("Print the accounts as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the DNS accounts and when each one's credential last worked."""
    accounts = DnsAccounts()
    rows = accounts.accounts()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "accounts": [
                        {
                            "name": a.name,
                            "kind": a.kind,
                            "automatic": a.automatic,
                            "verified_at": a.verified_at,
                            "last_error": a.last_error,
                        }
                        for a in rows
                    ]
                }
            )
        )
        return
    if not rows:
        ctx.logger.info(
            "There are no DNS accounts. Add one: noust dns provider add NAME --kind cloudflare"
        )
        return
    ctx.logger.table(
        ["Name", "Provider", "Records", "Last worked"],
        [
            [
                a.name,
                accounts.kind(a).label,
                "created without asking" if a.automatic else "asks first",
                a.verified_at or (f"failing: {a.last_error}" if a.last_error else "-"),
            ]
            for a in rows
        ],
    )


def _does(spec: ProviderKind) -> str:
    """
    Args:
        spec: A provider kind.

    Returns:
        What an account of it can do, in a few words.
    """
    if not spec.native:
        return "challenge (certbot plugin)"
    return "records, challenge, tunnels" if spec.tunnels else "records, challenge"


@provider.command("kinds", read_only=True)
@json_option("Print the providers as JSON.")
@pass_context
def kinds_command(ctx: Context) -> None:
    """List the providers Noust knows, and what an account of each needs."""
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    k: {
                        "native": s.native,
                        "tunnels": s.tunnels,
                        "comments": s.comments,
                        "fields": [f.name for f in s.fields],
                        "package": s.package,
                    }
                    for k, s in KINDS.items()
                }
            )
        )
        return
    ctx.logger.table(
        ["Provider", "Does", "Needs", "Where from"],
        [
            [
                spec.kind,
                _does(spec),
                ", ".join(f.name for f in spec.fields),
                spec.help,
            ]
            for spec in KINDS.values()
        ],
    )


@provider.command("add")
@click.argument("name")
@click.option("--kind", required=True, type=click.Choice(sorted(KINDS)), help="The provider.")
@click.option(
    "--set", "pairs", multiple=True, metavar="NAME=VALUE", help="A field that is not secret."
)
@click.option(
    "--token-stdin", is_flag=True, help="Read the credential from one line of standard input."
)
@click.option(
    "--credentials-stdin",
    is_flag=True,
    help="Read every field from a JSON object on standard input.",
)
@click.option(
    "--automatic/--ask",
    default=False,
    help="Create the records of a domain when it is added, without asking (providers Noust speaks to).",
)
@pass_context
def add_command(
    ctx: Context,
    name: str,
    kind: str,
    pairs: tuple[str, ...],
    token_stdin: bool,
    credentials_stdin: bool,
    automatic: bool,
) -> None:
    """
    Add a DNS account. Its credential is proven first, and kept only in the secret store.

    A secret is asked for on a hidden prompt, or read from standard input:
    printf %s "$TOKEN" | noust dns provider add cf --kind cloudflare --token-stdin
    """
    fields = _credentials(kind, pairs, token_stdin, credentials_stdin)
    if ctx.dry_run:
        ctx.logger.info(f"Would add the {kind} account {name}")
        return
    from noust.core.audit.actor import cli_actor

    account = DnsAccounts().add(
        name, kind, fields, automatic=automatic, created_by=cli_actor().label
    )
    ctx.logger.success(
        f"{name} works" + (" and creates records without asking" if account.automatic else "")
    )
    ctx.logger.info("Use it for a certificate: noust cert use APP --acme --dns " + name)


@provider.command("verify")
@click.argument("name")
@json_option("Print the zones as JSON.")
@pass_context
def verify_command(ctx: Context, name: str) -> None:
    """Try an account's credential again and list the zones it manages."""
    zones = DnsAccounts().verify(name)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"account": name, "zones": [{"id": zone.id, "name": zone.name} for zone in zones]}
            )
        )
        return
    ctx.logger.success(f"{name} works")
    # Printed, not logged as sub-steps: those are shown with --verbose only, and the zones are
    # the answer this command exists to give.
    for zone in zones:
        ctx.logger.key_value("Zone", zone.name)


@provider.command("set")
@click.argument("name")
@click.option("--automatic/--ask", "automatic", default=None, help="Create records without asking.")
@click.option(
    "--propagation", type=click.IntRange(0, 600), help="Seconds to wait for a challenge record."
)
@pass_context
def set_command(ctx: Context, name: str, automatic: bool | None, propagation: int | None) -> None:
    """Change whether an account creates records on its own, or how long a challenge waits."""
    if automatic is None and propagation is None:
        raise click.UsageError("Name something to change: --automatic/--ask or --propagation")
    accounts = DnsAccounts()
    if automatic is not None:
        accounts.set_automatic(name, automatic)
    if propagation is not None:
        accounts.set_propagation(name, propagation)
    ctx.logger.success(f"{name} updated")


@provider.command("remove")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """Remove an account and its credential. The records stay at the provider."""
    if not yes and not click.confirm(
        f"Remove the DNS account {name}? Its records stay where they are."
    ):
        return
    DnsAccounts().remove(name)
    ctx.logger.success(f"{name} removed")


@cli.command("records", read_only=True)
@json_option("Print the records as JSON.")
@pass_context
def records_command(ctx: Context) -> None:
    """List the DNS records Noust manages, who made them and who uses them."""
    dns = ManagedDns()
    rows = dns.records.list_records()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "records": [
                        {**r.to_dict(), "applications": dns.records.refs(r.id or 0)} for r in rows
                    ]
                }
            )
        )
        return
    if not rows:
        ctx.logger.info("Noust manages no DNS records.")
        return
    ctx.logger.table(
        ["Name", "Type", "Holds", "Account", "Made by", "Used by"],
        [
            [
                r.hostname,
                r.record_type,
                r.content,
                r.provider,
                "Noust" if r.owned else "adopted",
                ", ".join(dns.records.refs(r.id or 0)) or "nobody",
            ]
            for r in rows
        ],
    )


@cli.command("ensure")
@click.argument("app_domain", metavar="APP")
@click.argument("hostname")
@click.option(
    "--content",
    help="What it holds: an address, or a name for a CNAME. This server's public address by default.",
)
@click.option(
    "--type", "record_type", type=click.Choice(["A", "AAAA", "CNAME"]), help="The record type."
)
@click.option("--account", help="The account, when more than one holds the zone.")
@click.option("--plan", is_flag=True, help="Say what would be done and change nothing.")
@click.option(
    "--replace-record",
    metavar="ID",
    help="Replace the record in the way: its id, as the conflict named it.",
)
@click.option("--replace-current", metavar="VALUE", help="What that record held when you looked.")
@json_option("Print the outcome as JSON.")
@pass_context
def ensure_command(
    ctx: Context,
    app_domain: str,
    hostname: str,
    content: str | None,
    record_type: str | None,
    account: str | None,
    plan: bool,
    replace_record: str | None,
    replace_current: str | None,
) -> None:
    """
    Make HOSTNAME point at this server, creating its record in the account that holds the zone.

    A record somebody else made is never overwritten by surprise: a different one in the
    way is a conflict that names it, and --replace-record ID --replace-current VALUE
    confirms replacing exactly that one (only its content changes). One that already
    holds the right value is adopted as it is, and left alone when the name goes.
    """
    validated = validate_domain(app_domain)
    target = content
    if not target:
        addresses = server_addresses()
        if not addresses:
            raise ValidationError(
                "This server has no public address to point the name at",
                details="Declare it: noust config set dns.public_addresses ADDRESS",
                field="content",
            )
        target = addresses[0]
    dns = ManagedDns()
    if plan or ctx.dry_run:
        said = dns.plan(hostname, target, record_type=record_type, account=account)
        if ctx.json_output:
            click.echo(json.dumps(said.to_dict()))
            return
        ctx.logger.key_value("Would", said.action)
        ctx.logger.key_value("Record", f"{said.record_type} {said.hostname} {said.content}")
        ctx.logger.key_value("Account", f"{said.account} ({said.zone})")
        if said.current:
            ctx.logger.key_value("Now holds", f"{said.current} (record {said.record_id})")
        return
    from noust.core.audit.actor import cli_actor

    try:
        result = dns.ensure(
            hostname,
            target,
            app_domain=validated,
            record_type=record_type,
            account=account,
            replace_record=replace_record,
            replace_current=replace_current,
            actor=cli_actor().label,
        )
    except DnsRecordConflict as exc:
        ctx.logger.error(exc.message, details=exc.details)
        ctx.logger.info(
            f"To replace it: noust dns ensure {validated} {hostname} --replace-record "
            f"{exc.record_id} --replace-current {exc.current}"
        )
        raise SystemExit(1) from None
    if ctx.json_output:
        click.echo(json.dumps({"outcome": result.outcome.value, **result.record.to_dict()}))
        return
    ctx.logger.success(
        f"{result.record_type} {result.hostname} {result.content}: {result.outcome.value}"
    )


@cli.command("release")
@click.argument("app_domain", metavar="APP")
@click.argument("hostname", required=False)
@click.option("--keep", is_flag=True, help="Only forget that APP uses the records; delete nothing.")
@json_option("Print the outcomes as JSON.")
@pass_context
def release_command(ctx: Context, app_domain: str, hostname: str | None, keep: bool) -> None:
    """Let APP go of its records (or of HOSTNAME's): the last user's release deletes what Noust made."""
    results = ManagedDns().release(validate_domain(app_domain), hostname=hostname, delete=not keep)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "released": [
                        {"hostname": r.hostname, "type": r.record_type, "outcome": r.outcome.value}
                        for r in results
                    ]
                }
            )
        )
        return
    for r in results:
        ctx.logger.info(f"{r.record_type} {r.hostname}: {r.outcome.value}")
    if not results:
        ctx.logger.info("Nothing to release.")


@cli.command("sweep")
@json_option("Print the outcomes as JSON.")
@pass_context
def sweep_command(ctx: Context) -> None:
    """Release the records nobody has used for an hour. The monitor does this every hour."""
    results = ManagedDns().sweep()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "released": [
                        {"hostname": r.hostname, "outcome": r.outcome.value} for r in results
                    ]
                }
            )
        )
        return
    for r in results:
        ctx.logger.info(f"{r.record_type} {r.hostname}: {r.outcome.value}")
    if not results:
        ctx.logger.info("Nothing to sweep.")


@cli.command("check", read_only=True)
@click.argument("name")
@click.option(
    "--resolver",
    "resolvers",
    multiple=True,
    metavar="IP",
    help="Ask this resolver instead of dns.resolvers.",
)
@json_option("Print the verdict as JSON.")
@pass_context
def check_command(ctx: Context, name: str, resolvers: tuple[str, ...]) -> None:
    """Does NAME point at this server? Asked of the public DNS, as the certificate authority will."""
    result = check_dns(name, resolvers=resolvers or None)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": result.domain,
                    "verdict": result.verdict,
                    "points_here": result.points_here,
                    "cdn": result.cdn,
                    "resolver": result.resolver,
                    "resolved": list(result.resolved_addresses),
                    "expected": list(result.expected_addresses),
                }
            )
        )
        return
    said = {
        "here": "points at this server",
        "cdn": f"is behind {result.cdn}'s proxy, which reaches this server",
        "elsewhere": "points somewhere else",
        "none": "does not resolve",
        "unknown": "resolves, but this server cannot tell its own public address (it sits behind NAT)",
    }
    ctx.logger.key_value(result.domain, said.get(result.verdict, result.verdict))
    ctx.logger.key_value("Resolves to", ", ".join(result.resolved_addresses) or "nothing")
    ctx.logger.key_value("This server", ", ".join(result.expected_addresses) or "no address found")
    ctx.logger.key_value("Asked", result.resolver or "this machine's resolver")
    if result.verdict == "unknown":
        ctx.logger.info("Declare it to compare: noust config set dns.public_addresses ADDRESS")


@cli.group("challenge", cls=NoustGroup, hidden=True)
def challenge() -> None:
    """The two hooks certbot runs for a DNS-01 order. Not for humans."""


def _hook(verb: str, account: str) -> None:
    """
    Run one of certbot's two DNS-01 hooks.

    Args:
        verb: ``auth`` or ``cleanup``.
        account: The DNS account that holds the zone.
    """
    domain, validation = read_environment(os.environ)
    runner: Any = DnsChallenge()
    if verb == "auth":
        runner.present(account, domain, validation)
    else:
        runner.cleanup(account, domain, validation)


@challenge.command("auth", hidden=True)
@click.option("--account", required=True, help="The DNS account.")
@pass_context
def auth_command(ctx: Context, account: str) -> None:
    """Publish the record certbot asks for and wait for it."""
    _hook("auth", account)


@challenge.command("cleanup", hidden=True)
@click.option("--account", required=True, help="The DNS account.")
@pass_context
def cleanup_command(ctx: Context, account: str) -> None:
    """Remove the record this order published."""
    _hook("cleanup", account)
