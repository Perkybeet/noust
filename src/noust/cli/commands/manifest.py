# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust manifest`` command group: a server written down (J-15).

    noust manifest validate noust-manifest.yaml
    noust --context prod manifest plan noust-manifest.yaml
    noust --context prod manifest apply noust-manifest.yaml --yes
    noust --context prod manifest export > noust-manifest.yaml

A manifest lives in git. ``plan`` says what applying it would change, with no
variable's value in it; ``apply`` makes the server match it, one API call at a
time, as the token that applies (so the role, the team and the audit trail are
the server's); ``export`` writes down a server that already runs. Nothing is
ever deleted. See ``docs/manifest.md``.

The engine is :mod:`noust.manifest`; this is the front end. ``validate`` runs
anywhere; the others need a server: ``--context NAME`` or ``--remote URL``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, json_option, pass_context
from noust.cli.remote import RemoteCommand, RemoteGroup, api, emit, remote
from noust.client.errors import RemoteError
from noust.client.jobs import DEFAULT_WAIT
from noust.core.fs import get_fs
from noust.manifest import Applier, EnvironmentResolver, Manifest, load, plan
from noust.manifest.export import export
from noust.manifest.plan import LABELS, Plan

#: Exit code of ``plan --check`` when there is something to apply.
EXIT_CHANGES = 2


@click.group("manifest", cls=RemoteGroup)
def cli() -> None:
    """Write a server down, preview what would change, and apply it."""


def _load(path: Path) -> Manifest:
    """
    Args:
        path: The manifest file; ``-`` is standard input.

    Returns:
        The manifest.
    """
    if str(path) == "-":
        from noust.manifest import parse

        return parse(sys.stdin.read())
    return load(path)


def _print_plan(ctx: Context, result: Plan) -> None:
    """
    Args:
        ctx: The command context.
        result: A plan.
    """
    logger = ctx.logger
    if result.empty:
        logger.success("The server already matches the manifest.")
    else:
        logger.table(
            ["Change", "What", "Needs"],
            [
                [
                    LABELS.get(action.kind, action.kind),
                    action.summary,
                    ", ".join(
                        part
                        for part in (
                            "sudo mode" if action.elevated else "",
                            "unverified" if action.unverified else "",
                        )
                        if part
                    ),
                ]
                for action in result.actions
            ],
        )
    for warning in result.warnings:
        logger.warning(warning)
    if result.unmanaged:
        logger.info(
            "Not in the manifest, and left as they are: "
            + ", ".join(result.unmanaged[:12])
            + (f" and {len(result.unmanaged) - 12} more" if len(result.unmanaged) > 12 else "")
        )


@cli.command("validate", read_only=True)
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@json_option("Print what the manifest holds as JSON.")
@pass_context
def validate_command(ctx: Context, file: Path) -> None:
    """Check a manifest and say what it holds. Needs no server."""
    manifest = _load(file)
    secrets = sorted(f"{domain}:{name}" for domain, name, _ in manifest.secrets())
    summary: dict[str, Any] = {
        "valid": True,
        "projects": len(manifest.projects),
        "applications": [app.domain for app in manifest.applications],
        "secrets_read_from_here": secrets,
    }
    if ctx.json_output:
        click.echo(json.dumps(summary))
        return
    ctx.logger.success(
        f"Valid: {len(manifest.projects)} project(s), {len(manifest.applications)} application(s)."
    )
    if secrets:
        ctx.logger.info(
            f"{len(secrets)} variable(s) are read from this machine when it is applied."
        )


@cli.command("plan", read_only=True)
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@click.option(
    "--check",
    is_flag=True,
    help=f"Exit {EXIT_CHANGES} when there is something to apply (for a pipeline).",
)
@json_option("Print the plan as JSON.")
@pass_context
def plan_command(ctx: Context, file: Path, check: bool) -> None:
    """
    Say what applying a manifest would change. Changes nothing.

    Reads the server as the token, compares, and prints one line per change. No
    value of a variable is ever printed. Variables that need the token to
    confirm sudo mode to be compared are listed as unverified.
    """
    manifest = _load(file)
    base = Path.cwd() if str(file) == "-" else file.resolve().parent
    result = plan(manifest, api(ctx), EnvironmentResolver(base=base))
    if not emit(ctx, result.to_dict()):
        _print_plan(ctx, result)
    if check and not result.empty:
        raise click.exceptions.Exit(EXIT_CHANGES)


@cli.command("apply")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@click.option("--yes", "-y", is_flag=True, help="Apply without asking (a pipeline).")
@click.option(
    "--wait/--no-wait", default=True, show_default=True, help="Wait for the deployments it queues."
)
@click.option("--timeout", type=float, default=DEFAULT_WAIT, show_default=True)
@click.option(
    "--restart",
    is_flag=True,
    help="Restart the applications whose variables changed, so they use them.",
)
@json_option("Print the outcome of each change as JSON.")
@pass_context
def apply_command(
    ctx: Context, file: Path, yes: bool, wait: bool, timeout: float, restart: bool
) -> None:
    """
    Make the server match a manifest.

    Shows the plan first and asks, unless --yes. Creates what is missing and
    brings what exists to what is written; never deletes anything. A change that
    fails stops the run: fix the cause and apply again, the plan then holds only
    what is left. Exit 0 when everything is in place, 1 when a change failed.
    """
    manifest = _load(file)
    client = api(ctx)
    base = Path.cwd() if str(file) == "-" else file.resolve().parent
    result = plan(manifest, client, EnvironmentResolver(base=base))
    if not ctx.json_output:
        _print_plan(ctx, result)
    if result.empty:
        if ctx.json_output:
            click.echo(json.dumps({"ok": True, "outcomes": [], "restart": []}))
        return
    if not yes and not ctx.json_output:
        if not sys.stdin.isatty():
            raise click.UsageError("Apply asks first; in a pipeline say --yes.")
        click.confirm("Apply these changes?", abort=True)
    logger = ctx.logger
    applier = Applier(
        client,
        wait=wait,
        timeout=timeout,
        on_line=None if ctx.json_output else logger.info,
    )
    outcome = applier.apply(
        result,
        on_start=None if ctx.json_output else lambda action: logger.info(f"-> {action.summary}"),
    )
    if restart:
        for domain in outcome.restart:
            try:
                client.post(f"/apps/{domain}/restart")
            except RemoteError as exc:
                logger.warning(f"{domain}: could not restart it: {exc.message}")
    if ctx.json_output:
        click.echo(json.dumps(outcome.to_dict()))
    else:
        for item in outcome.outcomes:
            line = f"{item.action.target}: {item.message or item.status}"
            if item.status == "failed":
                logger.error(line)
            elif item.status == "skipped":
                logger.warning(f"{item.action.target}: skipped, {item.message}")
            else:
                logger.success(line)
        if outcome.restart and not restart:
            logger.info(
                "Restart to use the new variables: "
                + ", ".join(f"noust restart {d}" for d in outcome.restart)
            )
    if not outcome.ok:
        raise click.exceptions.Exit(1)


@cli.command("export", read_only=True)
@click.option("--output", "-o", type=click.Path(path_type=Path, dir_okay=False), default=None)
@pass_context
def export_command(ctx: Context, output: Path | None) -> None:
    """Write down the applications and projects you can see as a manifest."""
    text = export(api(ctx))
    if output is None:
        click.echo(text, nl=False)
        return
    fs = get_fs()
    fs.write_text(output, text, mode=0o644)
    ctx.logger.success(f"Wrote {output}. Add the variables under 'env:', then plan it.")


# It runs on another server like the other remote commands, and runs here too (validate).
remote.add_command(cli, "manifest")

__all__ = ["RemoteCommand", "cli"]
