# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app compose``: which file, profiles and extra arguments a Compose stack runs with.

``show`` says what the operator chose; ``set`` changes the options it is given
and writes the unit again when the file or the profiles changed; ``reset``
forgets them. The arguments are a closed list of flags of ``docker compose
build`` and ``up`` (``noust app compose set --help`` names them), never a shell
line. Each is :mod:`noust.deployers.helpers.compose_settings`, which
``/api/apps/{d}/compose`` calls too. ``document`` and ``replace`` read and change the
Compose document of a stack that was deployed from one
(:mod:`noust.deployers.compose_document`). This module only parses and presents.
"""

from __future__ import annotations

import json
from typing import TextIO

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
from noust.core.exceptions import ValidationError
from noust.deployers import compose_document
from noust.deployers.helpers import compose_variables
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


_STATE_WORDS = {
    compose_variables.SET: "set",
    compose_variables.DEFAULT: "default",
    compose_variables.UNSET: "not set, no default",
    compose_variables.MISSING: "REQUIRED and missing",
}


@compose.command("variables", read_only=True)
@click.argument("domain")
@json_option("Print the variables as JSON.")
@pass_context
def variables_command(ctx: Context, domain: str) -> None:
    """
    Show the variables the compose file asks for and which of them the stack has.

    Reads the file and the .env and runs nothing; no value is printed. A variable the file
    requires (${NAME:?message}) with no value stops a deploy or an update before the build.
    Set one with: noust env set DOMAIN NAME=value
    """
    validated = validate_domain(domain)
    from noust.core.store import get_store

    app = get_store().get_app(validated)
    if app is None or app.app_type != "docker-compose":
        raise click.UsageError(f"{validated} is not a Docker Compose application.")
    report = compose_variables.report_for(app)
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, **report.to_dict()}))
        return
    logger = ctx.logger
    logger.key_value("Stack", validated)
    logger.key_value("Compose file", report.file)
    logger.key_value("Values read from", report.env_file or "no .env")
    if not report.variables:
        logger.info("The file asks for no variable.")
        return
    for variable in report.variables:
        where = ", ".join(variable.used_in) or "-"
        extra = f" ({variable.message})" if variable.message else ""
        logger.key_value(variable.name, f"{_STATE_WORDS[variable.status]}{extra}; used by {where}")
    if report.missing:
        logger.blank()
        logger.warning(
            f"{', '.join(v.name for v in report.missing)} must be set before the next deploy: "
            f"noust env set {validated} " + " ".join(f"{v.name}=<value>" for v in report.missing)
        )


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


@compose.command("document", read_only=True)
@click.argument("domain")
@json_option("Print the document and where Noust keeps it as JSON.")
@pass_context
def document_command(ctx: Context, domain: str) -> None:
    """
    Print the Compose document a stack runs from.

    Only a stack that was deployed from a document (pasted, or converted from a template) has
    one: a stack from a repository keeps its compose file in git, and a recipe's is the recipe's.
    The document can hold a value typed into it, so reading it is recorded.
    """
    found = compose_document.read_document(validate_domain(domain), actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps({"domain": found.domain, "path": found.path, "document": found.text}))
        return
    click.echo(found.text, nl=False)


def _print_check(ctx: Context, domain: str, check: compose_document.DocumentCheck) -> None:
    """
    Say what a document is and what is wrong with it, for a person or as JSON.

    Args:
        ctx: The CLI context.
        domain: The stack.
        check: What :func:`~noust.deployers.compose_document.check_document` made of it.
    """
    analysis = check.analysis
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": domain,
                    "changed": check.changed,
                    "deployable": check.deployable,
                    "missing": check.missing,
                    "refused": analysis.refused,
                    "warned": analysis.warned,
                    "services": [service.name for service in analysis.services],
                }
            )
        )
        return
    logger = ctx.logger
    logger.key_value("Stack", domain)
    logger.key_value("Different from the stack's", "yes" if check.changed else "no")
    for service in analysis.services:
        shown = ", ".join(part for part in (service.image or "builds", *service.publishes) if part)
        logger.key_value(f"Service {service.name}", shown)
    for problem in analysis.refused:
        logger.error(problem)
    if check.missing:
        logger.error(
            f"{', '.join(check.missing)} required and not set: "
            + f"noust env set {domain} "
            + " ".join(f"{name}=<value>" for name in check.missing)
        )
    for note in analysis.warned:
        logger.warning(note)


@compose.command("replace")
@click.argument("domain")
@click.option(
    "--from",
    "source",
    required=True,
    type=click.File("r", encoding="utf-8"),
    metavar="FILE",
    help="The new document; - reads standard input.",
)
@click.option("--check", is_flag=True, help="Only judge it: write nothing and run nothing.")
@click.option(
    "--apply",
    "apply_now",
    is_flag=True,
    help="Update the stack to run it now, behind the health gate; without it the stack runs it "
    "at its next update.",
)
@json_option("Print the outcome as JSON.")
@pass_context
def replace_command(
    ctx: Context, domain: str, source: TextIO, check: bool, apply_now: bool
) -> None:
    """
    Replace the Compose document a stack runs from.

    The guard a creation runs refuses what it refuses (root on the host, the Docker socket, files
    a document does not have), and so does a variable the document requires that the stack has no
    value for. Nothing is written until it passes, and nothing is rebuilt by writing: the stack
    runs the document at its next update, or now with --apply, which puts the previous document
    back when the new one does not come up. See what a document would do with --check.
    """
    validated = validate_domain(domain)
    text = source.read()
    judged = compose_document.check_document(validated, text)
    if check or ctx.dry_run:
        _print_check(ctx, validated, judged)
        if not judged.deployable:
            raise ValidationError(
                "The Compose document cannot be deployed to this stack as it is",
                details="Fix what is reported above.",
                field="document",
            )
        if ctx.dry_run and not check:
            ctx.logger.info(f"Would replace the document of {validated}")
        return
    logger = ctx.logger
    change = compose_document.replace_document(
        validated,
        text,
        apply=apply_now,
        actor=_actor(),
        on_phase=lambda index, total, message: logger.step(index, total, message),
        on_step=logger.substep,
        logger=logger,
        verbose=logger.verbose,
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    "changed": change.changed,
                    "applied": change.applied,
                    "services": list(change.services),
                    "warnings": list(change.warnings),
                    "deployment_id": change.deployment_id,
                }
            )
        )
        return
    for note in change.warnings:
        logger.warning(note)
    if not change.changed:
        logger.info(f"{validated} already has this document")
    elif change.applied:
        logger.success(f"{validated} now runs the new document")
    else:
        logger.success(
            f"Kept the new document of {validated}. It runs it from the next update: "
            f"noust update {validated}"
        )
