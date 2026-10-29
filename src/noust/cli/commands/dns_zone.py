# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust dns zone``: the zones Noust DNS serves, their records, DNSSEC and the delegation.

Presentation over :mod:`noust.managers.authdns.zones` and
:mod:`noust.managers.authdns.delegation`, which ``/api/dns/noust/zones`` calls too. Attached to the
``dns`` group (:mod:`noust.cli.commands.dns`).

A zone is made on the primary and every secondary copies it. The domain answers from this server
only once its registrar says so: ``noust dns zone delegation ZONE`` prints exactly what to do
there and ``noust dns zone check ZONE`` looks at the world and says which step is missing.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.audit.actor import cli_actor
from noust.core.exceptions import ValidationError
from noust.managers.authdns.delegation import DelegationGuide
from noust.managers.authdns.zones import ZoneEditor
from noust.validators.dns_zone import RECORD_TYPES


def _glue(pairs: tuple[str, ...]) -> dict[str, list[str]]:
    """
    Args:
        pairs: ``name=address`` as typed.

    Returns:
        The addresses by name.

    Raises:
        click.UsageError: A pair is not ``name=address``.
    """
    found: dict[str, list[str]] = {}
    for pair in pairs:
        name, separator, address = pair.partition("=")
        if not separator or not name or not address:
            raise click.UsageError(f"--glue wants name=address, not {pair!r}")
        found.setdefault(name.strip().lower().rstrip("."), []).append(address.strip())
    return found


@click.group("zone", cls=NoustGroup)
def zone() -> None:
    """The zones Noust DNS serves: records, DNSSEC, and how to delegate a domain to them."""


@zone.command("list", read_only=True)
@json_option("Print the zones as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the zones this server serves."""
    zones = ZoneEditor().zones()
    if ctx.json_output:
        click.echo(json.dumps({"zones": [item.to_dict() for item in zones]}))
        return
    if not zones:
        ctx.logger.info("This server serves no zone. Make one: noust dns zone create example.com")
        return
    ctx.logger.table(
        ["Zone", "Role", "Records", "Serial", "DNSSEC"],
        [
            [
                item.name,
                item.role,
                str(item.records),
                str(item.serial) if item.serial is not None else "-",
                "signed" if item.dnssec else "no",
            ]
            for item in zones
        ],
    )


@zone.command("show", read_only=True)
@click.argument("name")
@json_option("Print the zone as JSON.")
@pass_context
def show_command(ctx: Context, name: str) -> None:
    """Show a zone and its records."""
    detail = ZoneEditor().get(name)
    if ctx.json_output:
        click.echo(json.dumps(detail.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Zone", detail.zone.name)
    logger.key_value("Name servers", ", ".join(detail.zone.nameservers))
    logger.key_value("Contact", detail.zone.contact)
    logger.key_value("Default TTL", str(detail.zone.default_ttl))
    logger.key_value("Serial", str(detail.serial) if detail.serial is not None else "-")
    logger.key_value("DNSSEC", "signed" if detail.zone.dnssec else "no")
    logger.table(
        ["Id", "Name", "Type", "TTL", "Content"],
        [[str(r.id), r.name, r.type, str(r.ttl or "-"), r.content] for r in detail.records],
    )


@zone.command("create")
@click.argument("name")
@click.option(
    "--nameserver",
    "nameservers",
    multiple=True,
    metavar="NAME",
    help="A name server of the zone. Repeatable. This server and its secondaries by default.",
)
@click.option(
    "--contact", metavar="EMAIL", help="The address the SOA names. hostmaster@ZONE by default."
)
@click.option("--ttl", type=int, default=3600, show_default=True, help="The default TTL.")
@click.option(
    "--glue",
    "glue",
    multiple=True,
    metavar="NAME=ADDRESS",
    help="The address of a name server inside the zone. Repeatable.",
)
@click.option("--dnssec", is_flag=True, help="Sign the zone.")
@json_option("Print the zone as JSON.")
@pass_context
def create_command(
    ctx: Context,
    name: str,
    nameservers: tuple[str, ...],
    contact: str | None,
    ttl: int,
    glue: tuple[str, ...],
    dnssec: bool,
) -> None:
    """
    Make a zone and serve it.

    The domain answers from here once its registrar delegates it: the steps are in
    ``noust dns zone delegation``.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would make the zone {name}")
        return
    with ctx.quiet():
        detail = ZoneEditor().create_zone(
            name,
            nameservers=list(nameservers) or None,
            contact=contact,
            default_ttl=ttl,
            glue=_glue(glue),
            dnssec=dnssec,
            actor=cli_actor().label,
        )
    if ctx.json_output:
        click.echo(json.dumps(detail.to_dict()))
        return
    ctx.logger.success(f"{detail.zone.name} is served by this server")
    ctx.logger.info(f"Add records: noust dns zone record add {detail.zone.name} www A 203.0.113.7")
    ctx.logger.info(f"Delegate it: noust dns zone delegation {detail.zone.name}")


@zone.command("set")
@click.argument("name")
@click.option(
    "--nameserver",
    "nameservers",
    multiple=True,
    metavar="NAME",
    help="Replace the name servers. Repeatable.",
)
@click.option("--contact", metavar="EMAIL", help="The address the SOA names.")
@click.option("--ttl", type=int, help="The default TTL.")
@click.option(
    "--glue",
    "glue",
    multiple=True,
    metavar="NAME=ADDRESS",
    help="The address of a name server inside the zone.",
)
@json_option("Print the zone as JSON.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    nameservers: tuple[str, ...],
    contact: str | None,
    ttl: int | None,
    glue: tuple[str, ...],
) -> None:
    """Change a zone's name servers, contact or default TTL."""
    if ctx.dry_run:
        ctx.logger.info(f"Would change the zone {name}")
        return
    with ctx.quiet():
        detail = ZoneEditor().update_zone(
            name,
            nameservers=list(nameservers) or None,
            contact=contact,
            default_ttl=ttl,
            glue=_glue(glue),
            actor=cli_actor().label,
        )
    if ctx.json_output:
        click.echo(json.dumps(detail.to_dict()))
        return
    ctx.logger.success(f"{detail.zone.name} is changed")


@zone.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Do not ask.")
@pass_context
def delete_command(ctx: Context, name: str, yes: bool) -> None:
    """Stop serving a zone and delete it with all its records."""
    if ctx.dry_run:
        ctx.logger.info(f"Would delete the zone {name}")
        return
    if not yes:
        click.confirm(
            f"Delete {name} and all its records? Resolvers that still point at this server will "
            "stop finding the domain.",
            abort=True,
        )
    with ctx.quiet():
        removed = ZoneEditor().delete_zone(name, actor=cli_actor().label)
    if removed:
        ctx.logger.success(f"{name} is deleted")
    else:
        ctx.logger.info(f"{name} was not a zone of this server")


@zone.group("record", cls=NoustGroup)
def record() -> None:
    """The records of a zone."""


@record.command("add")
@click.argument("zone_name", metavar="ZONE")
@click.argument("name")
@click.argument(
    "record_type", metavar="TYPE", type=click.Choice(RECORD_TYPES, case_sensitive=False)
)
@click.argument("content")
@click.option("--ttl", type=int, help="Seconds to live. The zone's by default.")
@json_option("Print the record as JSON.")
@pass_context
def record_add_command(
    ctx: Context, zone_name: str, name: str, record_type: str, content: str, ttl: int | None
) -> None:
    """
    Add a record: NAME is relative to the zone (www, @ for the zone itself, *.apps).

    Examples: ``www A 203.0.113.7``, ``@ MX "10 mail.example.net"``, ``@ TXT "v=spf1 -all"``.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would add {name} {record_type} to {zone_name}")
        return
    with ctx.quiet():
        added = ZoneEditor().add_record(
            zone_name, name, record_type, content, ttl=ttl, actor=cli_actor().label
        )
    if ctx.json_output:
        click.echo(json.dumps(added.to_dict()))
        return
    ctx.logger.success(f"{added.name} {added.type} {added.content} (record {added.id})")


@record.command("set")
@click.argument("zone_name", metavar="ZONE")
@click.argument("record_id", metavar="ID", type=int)
@click.option("--content", metavar="TEXT", help="What the record holds.")
@click.option("--ttl", type=int, help="Seconds to live.")
@click.option("--default-ttl", is_flag=True, help="Go back to the zone's TTL.")
@json_option("Print the record as JSON.")
@pass_context
def record_set_command(
    ctx: Context,
    zone_name: str,
    record_id: int,
    content: str | None,
    ttl: int | None,
    default_ttl: bool,
) -> None:
    """Change what a record holds, or its TTL."""
    if content is None and ttl is None and not default_ttl:
        raise ValidationError(
            "Say what to change", details="Use --content, --ttl or --default-ttl."
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would change record {record_id} of {zone_name}")
        return
    with ctx.quiet():
        changed = ZoneEditor().update_record(
            zone_name,
            record_id,
            content=content,
            ttl=ttl,
            clear_ttl=default_ttl,
            actor=cli_actor().label,
        )
    if ctx.json_output:
        click.echo(json.dumps(changed.to_dict()))
        return
    ctx.logger.success(f"{changed.name} {changed.type} {changed.content} (record {changed.id})")


@record.command("remove")
@click.argument("zone_name", metavar="ZONE")
@click.argument("record_id", metavar="ID", type=int)
@pass_context
def record_remove_command(ctx: Context, zone_name: str, record_id: int) -> None:
    """Remove a record. Its number is in ``noust dns zone show``."""
    if ctx.dry_run:
        ctx.logger.info(f"Would remove record {record_id} of {zone_name}")
        return
    with ctx.quiet():
        removed = ZoneEditor().delete_record(zone_name, record_id, actor=cli_actor().label)
    if removed:
        ctx.logger.success(f"Record {record_id} is removed")
    else:
        ctx.logger.info(f"There was no record {record_id}")


@zone.command("dnssec")
@click.argument("name")
@click.option("--enable", "enable", flag_value="on", help="Sign the zone.")
@click.option("--disable", "enable", flag_value="off", help="Stop signing the zone.")
@click.option(
    "--ds-removed",
    is_flag=True,
    help="With --disable: the DS record is already gone from the registrar.",
)
@click.option(
    "--ds-published",
    is_flag=True,
    help="Tell the server the registrar now has the DS record.",
)
@json_option("Print the state as JSON.")
@pass_context
def dnssec_command(
    ctx: Context, name: str, enable: str | None, ds_removed: bool, ds_published: bool
) -> None:
    """
    Show a zone's signing, or turn it on or off.

    The DS record it prints is what the registrar needs so that resolvers can trust the signatures.
    Turning signing off while the registrar still has the DS makes every validating resolver
    refuse the domain: remove the DS first.
    """
    editor = ZoneEditor()
    if (enable or ds_published) and ctx.dry_run:
        ctx.logger.info(f"Would change the signing of {name}")
        return
    actor = cli_actor().label
    with ctx.quiet():
        if enable:
            info = editor.set_dnssec(name, enable == "on", ds_removed=ds_removed, actor=actor)
        elif ds_published:
            info = editor.confirm_ds(name, actor=actor)
        else:
            info = editor.dnssec(name)
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    ctx.logger.key_value("Zone", info.zone)
    ctx.logger.key_value("Signed", f"yes ({info.algorithm})" if info.enabled else "no")
    for record_ in info.ds:
        ctx.logger.key_value("DS record", record_.text)
    if info.enabled and not info.ds:
        ctx.logger.info("The key is being made: ask again in a moment.")
    if info.ds:
        ctx.logger.info(
            f"Give it to your registrar after the name servers changed: noust dns zone delegation {name}"
        )


def _plan_lines(ctx: Context, plan: dict[str, Any]) -> None:
    """
    Args:
        ctx: The CLI context.
        plan: A delegation plan as the API shows it.
    """
    for number, step in enumerate(plan["steps"], start=1):
        ctx.logger.info(f"{number}. {step['text']}")
        if step["hint"]:
            ctx.logger.info(f"   {step['hint']}")
    for warning in plan["warnings"]:
        ctx.logger.warning(warning["text"])


@zone.command("delegation", read_only=True)
@click.argument("name")
@json_option("Print the steps as JSON.")
@pass_context
def delegation_command(ctx: Context, name: str) -> None:
    """Say exactly what to do at the registrar to delegate a domain to this server."""
    plan = DelegationGuide().plan(name).to_dict()
    if ctx.json_output:
        click.echo(json.dumps(plan))
        return
    _plan_lines(ctx, plan)


@zone.command("check", read_only=True)
@click.argument("name")
@json_option("Print the result as JSON.")
@pass_context
def check_command(ctx: Context, name: str) -> None:
    """
    Look at the world and say whether the domain is delegated to this server.

    Exits with 1 while something is missing.
    """
    result = DelegationGuide().check(name)
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
    else:
        for server in result.servers:
            state = (
                "answers as the authority"
                if server.authoritative
                else ("answers, not as the authority" if server.answers else "does not answer")
            )
            ctx.logger.key_value(f"{server.name} ({server.address})", state)
        if result.public_nameservers:
            ctx.logger.key_value("Resolvers find", ", ".join(result.public_nameservers))
        for problem in result.problems:
            ctx.logger.warning(problem["text"])
            if problem["hint"]:
                ctx.logger.info(f"  {problem['hint']}")
        if result.ready:
            ctx.logger.success(f"{result.zone} is delegated to this server")
    if not result.ready:
        raise click.exceptions.Exit(1)
