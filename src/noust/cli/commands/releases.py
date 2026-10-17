# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust releases``: what an application on the release layout has built, and
going back to one of them.

A presentation layer over :func:`noust.deployers.lifecycle.list_releases`,
:func:`noust.deployers.lifecycle.activate_release` and
:func:`noust.deployers.lifecycle.set_release_retention`, the functions the
panel's ``/api/apps/{domain}/releases`` endpoints call too. Going back is instant
because nothing is rebuilt: ``current`` is re-pointed, the unit restarted,
and the release kept only if it passes the same health gate as a deploy.
"""

from __future__ import annotations

import dataclasses
import json

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import DeploymentTrigger, get_store
from noust.deployers.lifecycle import (
    ReleaseInfo,
    activate_release,
    list_releases,
    set_release_retention,
)
from noust.deployers.recorder import CapturingLogger


def print_releases(logger: Logger, domain: str, releases: list[ReleaseInfo]) -> None:
    """
    Render an application's releases for a human.

    Args:
        logger: Logger the command writes through.
        domain: The application's domain.
        releases: Its releases, newest first.
    """
    if not releases:
        logger.info(f"{domain} has no releases yet")
        return
    logger.table(
        ["", "Release", "Commit", "Status", "Activated"],
        [
            [
                "*" if release.active else "",
                release.id,
                release.commit or "-",
                release.status if release.on_disk else f"{release.status} (removed)",
                release.activated_at or "-",
            ]
            for release in releases
        ],
    )
    logger.blank()
    logger.info(f"Go back to one with: noust releases rollback {domain} <release>")
    waiting = [release for release in releases if release.waiting]
    if waiting:
        logger.info(
            f"{len(waiting)} built and not serving yet: noust releases promote {domain} "
            f"{waiting[0].id}"
        )


@click.group("releases", cls=NoustGroup)
def cli() -> None:
    """List an application's releases and go back to one instantly."""


@cli.command("list")
@click.argument("domain")
@json_option("Print the releases as JSON.")
@pass_context
def list_command(ctx: Context, domain: str) -> None:
    """
    List the releases of an application, newest first.

    The active one is marked with an asterisk. A release that failed is
    listed for a while after its directory was removed, and cannot be
    activated.
    """
    releases = list_releases(domain)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"domain": domain, "items": [dataclasses.asdict(r) for r in releases]},
                default=str,
            )
        )
        return
    print_releases(ctx.logger, domain, releases)


@cli.command("rollback")
@click.argument("domain")
@click.argument("release", required=False)
@click.option(
    "--schema-changed-ok",
    is_flag=True,
    default=False,
    help="Go back even past deployments that changed the database schema.",
)
@pass_context
def rollback(ctx: Context, domain: str, release: str | None, schema_changed_ok: bool) -> None:
    """
    Make an earlier release the one that serves, without rebuilding anything.

    Without RELEASE, the release created just before the active one. The unit
    is restarted and the release kept only if it answers; if it does not, the
    release that was serving is put back and the command fails with the
    probe's and the journal's own output.

    Going back past a deployment that changed the database schema is refused,
    naming it, unless --schema-changed-ok says the older code works with the
    schema as it is now: Noust puts code back, never a database.
    """
    logger = CapturingLogger(verbose=ctx.verbose)
    outcome = activate_release(
        domain,
        release,
        trigger=DeploymentTrigger.CLI.value,
        logger=logger,
        schema_changed_ok=schema_changed_ok,
    )
    if not outcome.changed:
        logger.info(f"Release {outcome.release.id} is already the active one")
        return
    verb = "Rolled back to" if outcome.went_back else "Activated"
    logger.success(f"{verb} release {outcome.release.id} of {outcome.domain}")
    if outcome.went_back:
        logger.info("The database was left as it is: going back puts code back, never data.")
    if outcome.previous is not None:
        logger.info(
            f"Release {outcome.previous.id} stays on disk: "
            f"noust releases rollback {outcome.domain} {outcome.previous.id}"
        )


@cli.command("promote")
@click.argument("domain")
@click.argument("release", required=False)
@click.option(
    "--schema-changed-ok",
    is_flag=True,
    default=False,
    help="Switch even though later deployments changed the database schema.",
)
@click.option(
    "--during-freeze",
    is_flag=True,
    default=False,
    help="Switch although deploys are frozen for the application (noust deploy freeze).",
)
@json_option("Print what was done as JSON.")
@pass_context
def promote_command(
    ctx: Context, domain: str, release: str | None, schema_changed_ok: bool, during_freeze: bool
) -> None:
    """
    Make a release that was built with 'noust update --build-only' the one that serves.

    Without RELEASE, the newest one that waits. Nothing is built again: the release is linked to
    the application's own .env and shared folders, its pre-deploy hooks and Prisma's migrations
    run against the application's database, and it is switched to behind the health gate, which
    puts back what served when it does not answer. A release that already served is switched to
    with 'noust releases rollback' instead; that one runs no hook.
    """
    from noust.deployers.staged import promote_staged

    logger = CapturingLogger(verbose=ctx.verbose)
    promoted = promote_staged(
        domain,
        release,
        trigger=DeploymentTrigger.CLI.value,
        on_step=logger.substep,
        logger=logger,
        verbose=ctx.verbose,
        frozen_ok=during_freeze,
        schema_changed_ok=schema_changed_ok,
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": promoted.domain,
                    "release_id": promoted.release_id,
                    "previous_id": promoted.previous_id,
                    "deployment_id": promoted.deployment_id,
                    "warnings": promoted.warnings,
                }
            )
        )
        return
    logger.success(f"Release {promoted.release_id} of {promoted.domain} is serving")
    if promoted.warnings:
        logger.warning(f"Deployed with warnings: {promoted.warnings}")
    if promoted.previous_id is not None:
        logger.info(
            f"Release {promoted.previous_id} stays on disk: "
            f"noust releases rollback {promoted.domain} {promoted.previous_id}"
        )


@cli.command("keep")
@click.argument("domain")
@click.argument("count", required=False, type=int, metavar="[N]")
@global_flags
@json_option("Print the retention and what was pruned as JSON.")
@pass_context
def keep_command(ctx: Context, domain: str, count: int | None) -> None:
    """
    Show or set how many releases an application keeps on disk.

    N is from 1 to 50. Releases beyond it
    are removed now, oldest first; the active release and the one a rollback
    goes to are always kept, whatever N is.
    """
    if count is None:
        app = get_store().get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        if ctx.json_output:
            click.echo(json.dumps({"domain": app.domain, "keep_releases": app.keep_releases}))
        else:
            ctx.logger.key_value("Keeps", f"{app.keep_releases} releases")
        return

    logger = CapturingLogger(verbose=ctx.verbose)
    change = set_release_retention(domain, count, logger=logger)
    if ctx.json_output:
        click.echo(json.dumps(dataclasses.asdict(change) | {"pruned": list(change.pruned)}))
        return
    logger.success(f"{change.domain} keeps {change.keep_releases} releases")
    if change.pruned:
        logger.info(f"Removed {len(change.pruned)}: {', '.join(change.pruned)}")
    else:
        logger.info("Nothing to remove")
