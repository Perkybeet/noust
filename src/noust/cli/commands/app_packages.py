# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app packages``: the system packages an application needs on the host.

``show`` says what an application declares and what the host has of it; ``set`` replaces the
list; ``add`` and ``remove`` change it by names; ``install`` installs what is missing. Each is
:mod:`noust.managers.app_packages`, which ``/api/apps/{d}/packages`` calls too: the names are
validated and judged against ``security.app_packages`` there, and only the operator can write
them (a repository's ``noust.yaml`` has no key for them). This module only parses and presents.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.store import get_store
from noust.managers.app_packages import AppPackages, PackageChange, PackageState
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
    from noust.core.exceptions import NoustError

    validated = validate_domain(domain)
    if get_store().get_app(validated) is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Run 'noust list' to see what is deployed.",
        )
    return validated


def _print_state(ctx: Context, state: PackageState) -> None:
    """
    Say what an application declares and what the host has.

    Args:
        ctx: The CLI context.
        state: How it stands.
    """
    logger = ctx.logger
    logger.key_value("Application", state.domain)
    logger.key_value(
        "Package manager", state.manager if state.supported else f"{state.manager} (not managed)"
    )
    if not state.declared:
        logger.key_value("Packages", "none declared")
    for name in state.declared:
        if name in state.refused:
            status = "not allowed by security.app_packages"
        elif name in state.installed:
            status = "installed"
        else:
            status = "missing" if state.supported else "not checked"
        logger.key_value(name, status)
    if state.policy:
        logger.key_value("Policy", ", ".join(state.policy))
    if state.missing and state.supported and not state.refused:
        logger.info(
            f"The next update installs {', '.join(state.missing)}, or: noust app packages install {state.domain}"
        )


def _print_change(ctx: Context, change: PackageChange, done: str) -> None:
    """
    Say what a change did.

    Args:
        ctx: The CLI context.
        change: What was done.
        done: The sentence for the list having changed.
    """
    if ctx.json_output:
        click.echo(
            json.dumps({**change.state.to_dict(), "installed_now": list(change.installed_now)})
        )
        return
    ctx.logger.success(done)
    if change.installed_now:
        ctx.logger.success(f"Installed on this server: {', '.join(change.installed_now)}")
    _print_state(ctx, change.state)


@click.group("packages", cls=NoustGroup)
def packages() -> None:
    """The system packages an application needs on the host (a library, a font, a converter)."""


@packages.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the packages as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show what an application declares, what the host has of it, and the server's policy."""
    state = AppPackages().state(_known(domain))
    if ctx.json_output:
        click.echo(json.dumps(state.to_dict()))
        return
    _print_state(ctx, state)


@packages.command("set")
@click.argument("domain")
@click.argument("names", nargs=-1)
@click.option("--no-install", is_flag=True, help="Only record the list: the next update installs.")
@json_option("Print the packages as JSON.")
@pass_context
def set_command(ctx: Context, domain: str, names: tuple[str, ...], no_install: bool) -> None:
    """
    Declare the packages an application needs, replacing its list, and install them.

    Names are distribution packages (libvips-dev, fonts-noto-core, ffmpeg), one word each: no
    versions and no options. Without names the list is emptied; what is installed stays. A
    package outside security.app_packages, when the server sets it, is refused.
    """
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would declare {', '.join(names) or 'no packages'} for {validated}")
        return
    change = AppPackages().set(
        validated,
        names,
        actor=_actor(),
        install=not no_install,
        logger=None if ctx.json_output else ctx.logger,
    )
    _print_change(
        ctx, change, f"{validated} now needs: {', '.join(change.state.declared) or 'no packages'}"
    )


@packages.command("add")
@click.argument("domain")
@click.argument("names", nargs=-1, required=True)
@click.option("--no-install", is_flag=True, help="Only record the list: the next update installs.")
@json_option("Print the packages as JSON.")
@pass_context
def add_command(ctx: Context, domain: str, names: tuple[str, ...], no_install: bool) -> None:
    """Add packages to what an application declares, keeping the others, and install them."""
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would add {', '.join(names)} to {validated}")
        return
    manager = AppPackages()
    declared = manager.state(validated).declared
    change = manager.set(
        validated,
        (*declared, *names),
        actor=_actor(),
        install=not no_install,
        logger=None if ctx.json_output else ctx.logger,
    )
    _print_change(ctx, change, f"{validated} now needs: {', '.join(change.state.declared)}")


@packages.command("remove")
@click.argument("domain")
@click.argument("names", nargs=-1, required=True)
@json_option("Print the packages as JSON.")
@pass_context
def remove_command(ctx: Context, domain: str, names: tuple[str, ...]) -> None:
    """
    Stop declaring packages. They stay installed: another application, or the system, may use them.
    """
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would stop declaring {', '.join(names)} for {validated}")
        return
    manager = AppPackages()
    declared = manager.state(validated).declared
    unknown = [name for name in names if name not in declared]
    if unknown:
        raise click.UsageError(f"{validated} does not declare: {', '.join(unknown)}")
    kept = tuple(name for name in declared if name not in names)
    change = manager.set(validated, kept, actor=_actor(), install=False)
    _print_change(ctx, change, f"{validated} no longer declares: {', '.join(names)}")


@packages.command("install")
@click.argument("domain")
@json_option("Print the packages as JSON.")
@pass_context
def install_command(ctx: Context, domain: str) -> None:
    """Install the packages an application declares that the host lacks."""
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would install what {validated} declares and the host lacks")
        return
    change = AppPackages().install(
        validated, actor=_actor(), logger=None if ctx.json_output else ctx.logger
    )
    _print_change(ctx, change, f"The packages {validated} needs are on this server")
