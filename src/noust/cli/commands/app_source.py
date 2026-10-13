# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app source``: how an application's repository is fetched, and which pushes deploy it.

``show`` says what applies; ``set`` changes the options it is given and leaves
the others; ``reset`` goes back to the defaults. Each is
:mod:`noust.core.app_source_store`, which ``/api/apps/{d}/source-options``
calls too, validates and audits. This module only parses and presents.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import app_source_store
from noust.core.app_source_store import SourceOptions
from noust.core.exceptions import NoustError
from noust.core.store import get_store
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
