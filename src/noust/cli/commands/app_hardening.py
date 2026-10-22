# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app hardening``: how much of systemd's sandbox an application's unit runs in.

``status`` says the profile, the directories the unit may write to and what
``systemd-analyze security`` scores it; ``set`` changes them, restarting the
application behind its health gate and putting the previous sandbox back when it
does not answer. Both are :mod:`noust.deployers.hardening`, which
``/api/apps/{d}/hardening`` calls too. This module only parses, presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.unit_hardening_store import PROFILES
from noust.deployers import hardening
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


@click.group("hardening", cls=NoustGroup)
def hardening_group() -> None:
    """The sandbox an application's unit runs in: see it, or change it."""


@hardening_group.command("status", read_only=True)
@click.argument("domain")
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """
    Show the sandbox of the application's unit and how exposed systemd finds it.
    """
    info = hardening.status(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", info.domain)
    if not info.eligible:
        logger.key_value("Sandbox", f"not applicable: {info.reason}")
        return
    suffix = "" if info.configured else " (it predates hardening: its unit is as it was)"
    logger.key_value("Profile", info.profile + suffix)
    if info.writable_paths:
        logger.key_value("Also writable", ", ".join(info.writable_paths))
    if info.exposure is not None:
        logger.key_value("Exposure", f"{info.exposure:.1f} {info.exposure_label or ''}".strip())
    for directive in info.directives:
        logger.substep(directive)
    if not info.configured:
        logger.blank()
        logger.info(f"Harden it with: noust app hardening set {info.domain} default")


@hardening_group.command("set")
@click.argument("domain")
@click.argument("profile", type=click.Choice(PROFILES))
@click.option(
    "--writable",
    "writable",
    multiple=True,
    metavar="PATH",
    help="A directory the unit may write to besides the application's own tree; repeat it. "
    "Without any, the ones it has are kept.",
)
@click.option(
    "--no-writable",
    is_flag=True,
    default=False,
    help="Forget the extra writable directories it has.",
)
@click.option(
    "--no-restart",
    is_flag=True,
    default=False,
    help="Write the unit but do not restart: the running process keeps its sandbox.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print what was done as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    profile: str,
    writable: tuple[str, ...],
    no_writable: bool,
    no_restart: bool,
    yes: bool,
) -> None:
    """
    Run the application's unit in this sandbox: off, default or strict.

    default makes the filesystem read-only but for the application's own tree
    (and the directories named with --writable), hides the home directories and
    the devices, takes away every capability and filters system calls. strict
    also bans the privileged and resource calls and, for runtimes without a
    JIT, writable executable memory. off is the unit as it was before 4.0.

    The application is restarted behind its health gate; when it does not
    answer, the previous unit is put back and it is restarted as it was.
    """
    if writable and no_writable:
        raise click.UsageError("--writable and --no-writable contradict each other.")
    validated = validate_domain(domain)
    # Before the question: a sandbox the unit cannot have is not something to be asked about.
    state = hardening.status(validated, analyze=False)
    if not state.eligible:
        raise ValidationError(
            f"{validated} cannot be hardened: {state.reason}", details="Nothing was changed."
        )
    if not (yes or ctx.json_output or ctx.dry_run or no_restart):
        click.confirm(
            f"Restart {validated} in the {profile} sandbox? It is restarted behind its "
            "health check",
            abort=True,
        )
    change = hardening.set_hardening(
        validated,
        profile,
        writable_paths=() if no_writable else (list(writable) if writable else None),
        restart=not no_restart,
        actor=_actor(),
        logger=ctx.logger,
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    "profile": change.profile,
                    "writable_paths": list(change.writable_paths),
                    "units": list(change.units),
                    "restarted": change.restarted,
                    "exposure_before": change.exposure_before,
                    "exposure_after": change.exposure_after,
                }
            )
        )
        return
    if change.exposure_before is not None and change.exposure_after is not None:
        ctx.logger.key_value(
            "Exposure", f"{change.exposure_before:.1f} -> {change.exposure_after:.1f}"
        )
    if not change.restarted:
        ctx.logger.warning(
            f"{change.domain} was not restarted: it keeps its sandbox until it is "
            f"(noust restart {change.domain})"
        )
