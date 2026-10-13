# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app compose``: which file, profiles and extra arguments a Compose stack runs with.

``show`` says what the operator chose; ``set`` changes the options it is given
and writes the unit again when the file or the profiles changed; ``reset``
forgets them. The arguments are a closed list of flags of ``docker compose
build`` and ``up`` (``noust app compose set --help`` names them), never a shell
line. Each is :mod:`noust.deployers.helpers.compose_settings`, which
``/api/apps/{d}/compose`` calls too. This module only parses and presents.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.app_compose_store import (
    BUILD_SWITCHES,
    BUILD_VALUED,
    UP_SWITCHES,
    UP_VALUED,
    ComposeOptions,
    words,
)
from noust.deployers.helpers.compose_settings import change_compose, describe_compose, reset_compose
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _print(ctx: Context, domain: str, options: ComposeOptions) -> None:
    """
    Show the options, for a person or as JSON.

    Args:
        ctx: The CLI context.
        domain: The stack.
        options: What it is run with.
    """
    if ctx.json_output:
        click.echo(json.dumps({"domain": domain, **options.to_dict()}))
        return
    logger = ctx.logger
    logger.key_value("Stack", domain)
    logger.key_value("Compose file", options.compose_file or "the unit's, or the default names")
    logger.key_value("Profiles", ", ".join(options.profiles) or "none")
    logger.key_value("Build arguments", words(options.build_args) or "none")
    logger.key_value("Up arguments", words(options.up_args) or "none")
    logger.key_value("Services", ", ".join(options.services) or "all")


@click.group("compose", cls=NoustGroup)
def compose() -> None:
    """The compose file, profiles and extra arguments of a Docker Compose stack."""


@compose.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the settings as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show what a Compose stack is run with."""
    validated = validate_domain(domain)
    _print(ctx, validated, describe_compose(validated))


@compose.command("set")
@click.argument("domain")
@click.option(
    "--file", "compose_file", metavar="PATH", help="The compose file, relative to the project."
)
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    metavar="NAME",
    help="A profile to activate; repeat it. Replaces the profiles.",
)
@click.option("--no-profiles", is_flag=True, help="Activate no profile.")
@click.option(
    "--build-args",
    metavar="ARGS",
    help="Extra arguments of 'docker compose build', as one quoted line. Allowed: "
    + ", ".join(sorted(BUILD_SWITCHES | set(BUILD_VALUED)))
    + ".",
)
@click.option(
    "--up-args",
    metavar="ARGS",
    help="Extra arguments of 'docker compose up', as one quoted line. Allowed: "
    + ", ".join(sorted(UP_SWITCHES | set(UP_VALUED)))
    + ".",
)
@click.option(
    "--service",
    "services",
    multiple=True,
    metavar="NAME",
    help="Build and start only this service (and what it depends on); repeat it.",
)
@click.option("--all-services", is_flag=True, help="Build and start every service again.")
@json_option("Print the settings now in force as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    compose_file: str | None,
    profiles: tuple[str, ...],
    no_profiles: bool,
    build_args: str | None,
    up_args: str | None,
    services: tuple[str, ...],
    all_services: bool,
) -> None:
    """
    Change what a Compose stack is run with, keeping what is not named.

    Takes effect on the next update; a new file or new profiles are written to the
    stack's unit too, so a start at boot brings up the same stack.
    """
    validated = validate_domain(domain)
    if profiles and no_profiles:
        raise click.UsageError("Give --profile, or --no-profiles, not both")
    if services and all_services:
        raise click.UsageError("Give --service, or --all-services, not both")
    given: dict[str, object] = {}
    if compose_file is not None:
        given["compose_file"] = compose_file
    if profiles or no_profiles:
        given["profiles"] = list(profiles)
    if build_args is not None:
        given["build_args"] = build_args
    if up_args is not None:
        given["up_args"] = up_args
    if services or all_services:
        given["services"] = list(services)
    if not given:
        raise click.UsageError("Give at least one setting; see 'noust app compose set --help'")
    if ctx.dry_run:
        ctx.logger.info(f"Would set {', '.join(sorted(given))} of {validated}")
        return
    options = change_compose(validated, given, actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, **options.to_dict()}))
        return
    ctx.logger.success(f"Updated the compose settings of {validated}")
    _print(ctx, validated, options)


@compose.command("reset")
@click.argument("domain")
@pass_context
def reset_command(ctx: Context, domain: str) -> None:
    """Forget the choices; the unit's own record of the file applies again."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would reset the compose settings of {validated}")
        return
    reset_compose(validated)
    ctx.logger.success(f"{validated} uses the compose settings its unit records again")
