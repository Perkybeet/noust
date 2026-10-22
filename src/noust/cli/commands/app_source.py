# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app source``: how an application's repository is fetched, and which pushes deploy it.

``show`` says what applies; ``set`` changes the options it is given and leaves
the others; ``reset`` goes back to the defaults. Each is
:mod:`noust.core.app_source_store`, which ``/api/apps/{d}/source-options``
calls too, validates and audits. ``change`` points the application at another
repository or directory without creating it again
(:mod:`noust.deployers.source_change`). This module only parses and presents.
"""

from __future__ import annotations

import json
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import app_source_store
from noust.core.app_source_store import SourceOptions
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers import source_change
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _known(domain: str) -> str:
    """
    Validate a domain and require an application behind it.

    Args:
        domain: What the operator typed.

    Returns:
        The validated domain.

    Raises:
        NoustError: Nothing is deployed there.
    """
    validated = validate_domain(domain)
    if get_store().get_app(validated) is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Run 'noust list' to see what is deployed.",
        )
    return validated


def _on_off(value: bool) -> str:
    """
    Args:
        value: A switch.

    Returns:
        ``on`` or ``off``.
    """
    return "on" if value else "off"


def _print(ctx: Context, domain: str, options: SourceOptions) -> None:
    """
    Show the options, for a human or as JSON.

    Args:
        ctx: The CLI context.
        domain: The application.
        options: What applies.
    """
    if ctx.json_output:
        click.echo(json.dumps({"domain": domain, **options.to_dict()}))
        return
    logger = ctx.logger
    logger.key_value("Application", domain)
    logger.key_value("Submodules", _on_off(options.submodules))
    logger.key_value("Git LFS", options.lfs)
    logger.key_value("Shallow clone", _on_off(options.shallow))
    logger.key_value("Skip markers", _on_off(options.skip_markers) + " ([skip ci], [skip cd])")
    if options.watch_paths:
        logger.key_value("Watch paths", options.watch_paths[0])
        for pattern in options.watch_paths[1:]:
            logger.key_value("", pattern)
    else:
        logger.key_value("Watch paths", "none: every push deploys")


@click.group("source", cls=NoustGroup)
def source() -> None:
    """How an application's repository is fetched, and which pushes deploy it."""


@source.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the source options as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show submodules, Git LFS, shallow clone, watch paths and skip markers of an application."""
    validated = _known(domain)
    _print(ctx, validated, app_source_store.get_options(validated))


@source.command("set")
@click.argument("domain")
@click.option(
    "--submodules/--no-submodules", default=None, help="Initialise and export the submodules."
)
@click.option(
    "--lfs",
    type=click.Choice(app_source_store.LFS_MODES),
    help="Git LFS: auto fetches the files when the repository tracks any, on always, off never.",
)
@click.option(
    "--shallow/--no-shallow",
    default=None,
    help="Fetch only the latest commit (--no-shallow keeps the full history).",
)
@click.option(
    "--watch-path",
    "watch_paths",
    multiple=True,
    metavar="GLOB",
    help="A glob a push must touch to deploy (apps/web/**); repeat it, or prefix '!' to exclude. "
    "The last pattern that matches wins.",
)
@click.option("--clear-watch-paths", is_flag=True, help="Remove every watch path.")
@click.option(
    "--skip-markers/--no-skip-markers",
    default=None,
    help="Skip a push whose commits all say [skip ci] or [skip cd].",
)
@json_option("Print the options now in force as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    submodules: bool | None,
    lfs: str | None,
    shallow: bool | None,
    watch_paths: tuple[str, ...],
    clear_watch_paths: bool,
    skip_markers: bool | None,
) -> None:
    """
    Change some source options of an application, keeping the others.

    They apply to the next fetch and to the next delivery; nothing is rebuilt now.
    """
    validated = _known(domain)
    if clear_watch_paths and watch_paths:
        raise click.UsageError("Give --watch-path, or --clear-watch-paths, not both")
    watch: list[str] | None = [] if clear_watch_paths else list(watch_paths) or None
    given = [
        name
        for name, value in (
            ("submodules", submodules),
            ("lfs", lfs),
            ("shallow", shallow),
            ("skip_markers", skip_markers),
            ("watch_paths", watch),
        )
        if value is not None
    ]
    if not given:
        raise click.UsageError("Give at least one option to set; see 'noust app source set --help'")
    if ctx.dry_run:
        ctx.logger.info(f"Would set {', '.join(sorted(given))} for {validated}")
        return
    options = app_source_store.set_options(
        validated,
        submodules=submodules,
        lfs=lfs,
        shallow=shallow,
        watch_paths=watch,
        skip_markers=skip_markers,
        actor=_actor(),
    )
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, **options.to_dict()}))
        return
    ctx.logger.success(f"Updated the source options of {validated}")
    _print(ctx, validated, options)


@source.command("reset")
@click.argument("domain")
@pass_context
def reset_command(ctx: Context, domain: str) -> None:
    """Go back to the default source options."""
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would reset the source options of {validated}")
        return
    app_source_store.reset_options(validated, actor=_actor())
    ctx.logger.success(f"{validated} uses the default source options again")


def _can_ask() -> bool:
    """
    Report whether someone is at a terminal to answer a question.

    Returns:
        True when standard input is a terminal.
    """
    return sys.stdin.isatty()


def _print_plan(ctx: Context, plan: source_change.SourcePlan) -> None:
    """
    Say what a change of source would do.

    Args:
        ctx: The CLI context.
        plan: What it would do.
    """
    logger = ctx.logger
    logger.key_value("Application", plan.domain)
    logger.key_value("From", plan.current or "(none)")
    logger.key_value("To", plan.target)
    if plan.target_branch or plan.current_branch:
        logger.key_value("Branch", f"{plan.current_branch or '-'} -> {plan.target_branch or '-'}")
    if plan.head:
        logger.key_value("Head", plan.head[:12])
    if plan.pinned and plan.target_kind == "git":
        logger.key_value("Pinned", "the pin follows the new branch")
    for warning in plan.warnings:
        logger.warning(warning)


@source.command("change")
@click.argument("domain")
@click.argument("new_source", metavar="SOURCE")
@click.option(
    "-b",
    "--branch",
    help="The branch to follow. By default the one it follows now when SOURCE is the same "
    "repository at another address, else the remote's default.",
)
@click.option(
    "--no-check",
    is_flag=True,
    help="Do not ask the remote whether it can be read and has the branch (needs --branch).",
)
@click.option("-y", "--yes", is_flag=True, help="Change it without asking.")
@json_option("Print the plan, or what was changed, as JSON.")
@pass_context
def change_command(
    ctx: Context,
    domain: str,
    new_source: str,
    branch: str | None,
    no_check: bool,
    yes: bool,
) -> None:
    """
    Make an application deploy from another repository or directory.

    For a repository that moved, or a move from one code host to another. The
    application keeps its history, its variables, its domain and its releases.
    The remote is asked first (can it be read, does the branch exist) and what
    the change does not carry is listed: the old repository's webhook, history
    the new one may not have, previews of the old source. Nothing is fetched or
    built; the next 'noust update' builds the new source and the release that
    serves keeps serving until then.
    """
    validated = _known(domain)
    plan = source_change.plan_change(validated, new_source, branch, check=not no_check)
    if ctx.json_output and (ctx.dry_run or not plan.changed):
        click.echo(json.dumps({**plan.to_dict(), "applied": False}))
        return
    if not ctx.json_output:
        _print_plan(ctx, plan)
    if not plan.changed:
        raise click.UsageError(f"{validated} already deploys from that source and branch")
    if ctx.dry_run:
        ctx.logger.info(f"Would change the source of {validated}")
        return
    if not yes:
        if not _can_ask():
            raise click.UsageError("Nobody to ask: pass --yes to change it")
        if not click.confirm(f"Deploy {validated} from {plan.target} from now on?", default=False):
            ctx.logger.info("Nothing changed")
            return
    done = source_change.change_source(
        validated, new_source, branch, check=not no_check, actor=_actor()
    )
    if ctx.json_output:
        click.echo(json.dumps({**done.to_dict(), "applied": True}))
        return
    ctx.logger.success(f"{validated} deploys from {done.target} now")
    ctx.logger.info(f"Build it with: noust update {validated}")
