# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet builder``: which server builds an application for the others (checklist A-49).

A small server is a poor place to compile an application. A central that manages several can have
one of them build, and the others receive the release it built (``noust fleet placement ship``). The
choice is the central's own record, like a placement: ``set`` records it, ``show`` reads it, ``clear``
forgets it, and none of them touches a server. The commands are clients of the module the API is
(:mod:`noust.fleet.builders`).
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.fleet import cli as fleet_cli
from noust.core.audit import record as record_audit
from noust.core.exceptions import ValidationError


@fleet_cli.group("builder", cls=NoustGroup)
def builder_cli() -> None:
    """The server that builds an application for the others."""


@builder_cli.command("list", read_only=True)
@json_option("Print the builders as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the applications that are built on a server other than the ones that run them."""
    from noust.fleet.builders import Builders

    listed = Builders().list()
    if ctx.json_output:
        click.echo(json.dumps({"builders": [builder.to_dict() for builder in listed]}))
        return
    if not listed:
        ctx.logger.info("No application is built on a server of its own")
        ctx.logger.info("Choose one: noust fleet builder set shop.example.com build-1")
        return
    ctx.logger.table(
        ["Application", "Built on", "Chosen by", "Since"],
        [[b.domain, b.node, b.created_by or "-", b.created_at] for b in listed],
    )


@builder_cli.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the builder as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Say which server builds DOMAIN."""
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    found = Builders().get(checked_domain(domain))
    if found is None:
        raise ValidationError(
            f"{domain} is built where it runs",
            details=f"Choose a server: noust fleet builder set {domain} build-1",
            field="domain",
        )
    if ctx.json_output:
        click.echo(json.dumps(found.to_dict()))
        return
    ctx.logger.key_value("Application", found.domain)
    ctx.logger.key_value("Built on", found.node)
    ctx.logger.info(
        f"Ship its build to the servers that run it: noust fleet placement ship {domain}"
    )


@builder_cli.command("set")
@click.argument("domain")
@click.argument("node")
@json_option("Print the builder as JSON.")
@pass_context
def set_command(ctx: Context, domain: str, node: str) -> None:
    """
    Choose NODE as the server that builds DOMAIN. Touches no server.

    NODE needs the application (its source, variables and build settings) on the release
    layout, as the same type as the servers that run it; shipping checks that before it builds.
    """
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    key = checked_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would have {node} build {key}")
        return
    saved = Builders().set(key, node, actor="cli:root")
    record_audit("fleet.builder.set", target=f"app:{saved.domain}", details={"node": saved.node})
    if ctx.json_output:
        click.echo(json.dumps(saved.to_dict()))
        return
    ctx.logger.success(f"{saved.node} builds {saved.domain}")
    ctx.logger.info(f"Ship its build: noust fleet placement ship {saved.domain}")


@builder_cli.command("clear")
@click.argument("domain")
@pass_context
def clear_command(ctx: Context, domain: str) -> None:
    """Go back to building DOMAIN where it runs. Touches no server."""
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    key = checked_domain(domain)
    builders = Builders()
    found = builders.get(key)
    if found is None:
        ctx.logger.info(f"{key} is built where it runs already")
        return
    if ctx.dry_run:
        ctx.logger.info(f"Would stop having {found.node} build {key}")
        return
    builders.clear(key)
    record_audit("fleet.builder.clear", target=f"app:{key}", details={"node": found.node})
    ctx.logger.success(f"{key} is built where it runs again")
