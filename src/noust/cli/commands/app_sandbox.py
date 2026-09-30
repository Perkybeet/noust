# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app sandbox``: build an application without root.

``status`` shows an application's build regime and the last trial; ``test``
builds its current commit in the sandbox without activating anything
(:func:`noust.deployers.helpers.sandbox_trial.run_trial`); ``enable`` turns
the sandbox on, after a passing trial unless ``--force``; ``disable`` records
the decision to build it as root, with a reason; ``compose-exception`` allows a
Docker Compose stack privileged containers or the Docker socket;
``self-test`` proves the sandbox holds on this server. Each is
:mod:`noust.deployers.helpers.sandbox`, which ``/api/apps/{d}/sandbox`` calls
too, and which records every change in the audit trail. This module only
parses, presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.runner import get_runner
from noust.core.store import get_store
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.sandbox_trial import run_trial
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _print_state(ctx: Context, state: build_sandbox.SandboxState) -> None:
    """
    Show a build regime.

    Args:
        ctx: The CLI context.
        state: The regime.
    """
    logger = ctx.logger
    words = {
        "on": "on: installs and builds run as noust-build, confined",
        "off": f"off: builds run as root, by decision of {state.changed_by} ({state.reason})",
        "legacy": "not enabled: builds run as root, as before 3.1",
    }
    logger.key_value("Application", state.domain)
    logger.key_value("Sandbox", words.get(state.mode, state.mode))
    logger.key_value(
        "Network",
        "strict: install without secrets, build without a network"
        if state.network == "strict"
        else "full",
    )
    logger.key_value("Terminal mode", "on" if state.pty else "off")
    if state.tested_at:
        outcome = "passed" if state.test_passed else "failed"
        logger.key_value(
            "Last trial",
            f"{outcome} at {state.tested_at}, commit {(state.tested_commit or '')[:7]}",
        )
        if not state.test_passed and state.test_detail:
            logger.blank()
            logger.info(state.test_detail)
    else:
        logger.key_value("Last trial", "never")


@click.group("sandbox", cls=NoustGroup)
def sandbox() -> None:
    """Build an application without root: see, try, enable or disable its sandbox."""


@sandbox.command("status")
@click.argument("domain")
@json_option("Print the build regime as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """
    Show how an application builds: in the sandbox, or as root and why.
    """
    state = build_sandbox.get_state(validate_domain(domain))
    app = get_store().get_app(state.domain)
    warning = build_sandbox.sandbox_warning(app, state) if app is not None else None
    if ctx.json_output:
        click.echo(json.dumps({**state.to_dict(), "warning": warning}))
        return
    _print_state(ctx, state)
    if warning:
        ctx.logger.blank()
        ctx.logger.warning(warning)


@sandbox.command("test")
@click.argument("domain")
@json_option("Print the trial's outcome as JSON.")
@pass_context
def test_command(ctx: Context, domain: str) -> None:
    """
    Build the application's current commit in the sandbox, without activating it.

    The commit it runs now is exported to a scratch directory and installed
    and built as noust-build, exactly as an enabled sandbox would build it,
    then thrown away. Nothing is restarted and nothing is recorded in the
    deployment history; the outcome is kept, and enabling asks for a passing
    one.
    """
    result = run_trial(validate_domain(domain), logger=ctx.logger, verbose=ctx.verbose)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": result.domain,
                    "passed": result.passed,
                    "commit": result.commit,
                    "detail": result.detail,
                }
            )
        )
    elif not result.passed:
        ctx.logger.blank()
        ctx.logger.info(result.detail or "")
    if not result.passed:
        raise SystemExit(1)


@sandbox.command("enable")
@click.argument("domain")
@click.option("--force", is_flag=True, default=False, help="Enable without a passing trial.")
@click.option(
    "--network",
    type=click.Choice(["full", "strict"]),
    default=None,
    help="strict: install without the application's variables, build without a network.",
)
@click.option(
    "--pty/--no-pty",
    default=None,
    help="Build on a terminal, for build scripts that reopen /dev/stderr.",
)
@json_option("Print the new regime as JSON.")
@pass_context
def enable_command(
    ctx: Context, domain: str, force: bool, network: str | None, pty: bool | None
) -> None:
    """
    Build the application in the sandbox from its next deploy on.
    """
    state = build_sandbox.enable(
        validate_domain(domain), actor=_actor(), force=force, network=network, pty=pty
    )
    if ctx.json_output:
        click.echo(json.dumps(state.to_dict()))
        return
    ctx.logger.success(f"{state.domain} builds in the sandbox from its next deploy on")


@sandbox.command("disable")
@click.argument("domain")
@click.option("--reason", required=True, help="Why it cannot build in the sandbox; recorded.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print the new regime as JSON.")
@pass_context
def disable_command(ctx: Context, domain: str, reason: str, yes: bool) -> None:
    """
    Build the application as root: an explicit decision, recorded with a reason.

    Its install scripts and its build then run with root's access to this
    server, as before 3.1. The decision is kept with your name and shown
    wherever the application is, and in the audit trail.
    """
    validated = validate_domain(domain)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(
            f"Build {validated} as root? Its dependencies' install scripts get root's access",
            abort=True,
        )
    state = build_sandbox.disable(validated, actor=_actor(), reason=reason)
    if ctx.json_output:
        click.echo(json.dumps(state.to_dict()))
        return
    ctx.logger.warning(f"{state.domain} builds as root from its next deploy on ({state.reason})")


@sandbox.command("compose-exception")
@click.argument("domain")
@click.option("--reason", default=None, help="Why this stack needs it; recorded.")
@click.option("--revoke", is_flag=True, default=False, help="Stop allowing it.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_context
def compose_exception_command(
    ctx: Context, domain: str, reason: str | None, revoke: bool, yes: bool
) -> None:
    """
    Allow a Docker Compose stack privileged containers and the Docker socket.

    Either is root on this server, and the compose file comes from the
    repository, so a new stack is refused them. This records why this one
    needs them; it can be given before the stack's first deploy.
    """
    validated = validate_domain(domain)
    if revoke:
        build_sandbox.set_compose_exception(validated, allowed=False, actor=_actor())
        ctx.logger.success(f"{validated} may no longer run privileged containers")
        return
    if not (yes or ctx.dry_run):
        click.confirm(f"Allow {validated} containers that are root on this server?", abort=True)
    build_sandbox.set_compose_exception(validated, allowed=True, actor=_actor(), reason=reason)
    ctx.logger.warning(f"{validated} may run privileged containers and mount the Docker socket")


@sandbox.command("self-test")
@json_option("Print the checks as JSON.")
@pass_context
def self_test_command(ctx: Context) -> None:
    """
    Prove that the build sandbox holds on this server.

    A canary run as noust-build must write its own directory and must not
    write outside it, nor /root, nor read the configuration. A build refuses
    to run where this fails, rather than running as root.
    """
    runner = get_runner()
    build_sandbox.ensure_build_account(runner)
    build_sandbox.forget_self_test()
    outcome = build_sandbox.self_test(runner)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "passed": outcome.passed,
                    "checks": [
                        {"check": check, "held": held, "evidence": evidence}
                        for check, held, evidence in outcome.checks
                    ],
                }
            )
        )
    else:
        for check, held, evidence in outcome.checks:
            line = check + (f" ({evidence})" if evidence else "")
            (ctx.logger.success if held else ctx.logger.error)(line)
    if not outcome.passed:
        raise SystemExit(1)
