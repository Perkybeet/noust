# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app mount``: files Noust keeps for an application, and server paths bound into a container.

``list`` shows them (never their content), ``show`` prints a file's text, ``file``
keeps a file for the application (its text from a file or from standard input),
``bind`` binds a path of the server into a container application, ``remove`` removes
one. All of it is :mod:`noust.managers.app_mounts`, which ``/api/apps/{d}/mounts``
calls too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers.app_mounts import MountManager
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


@click.group("mount", cls=NoustGroup)
def mount_group() -> None:
    """Files kept for an application, and server paths bound into its container."""


@mount_group.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the mounts as JSON.")
@pass_context
def list_command(ctx: Context, domain: str) -> None:
    """
    List the files and server paths mounted into an application.
    """
    found = MountManager(log=ctx.logger).list_mounts(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps({"mounts": [view.to_dict() for view in found]}))
        return
    if not found:
        ctx.logger.info("Nothing is mounted")
        return
    ctx.logger.table(
        ["Kind", "Target", "Where", "Mode", "There"],
        [
            [
                view.record.kind,
                view.record.target,
                view.path,
                "ro"
                if view.record.kind == "path" and view.record.read_only
                else f"{view.record.mode:o}",
                "yes" if view.exists else "NO",
            ]
            for view in found
        ],
    )


@mount_group.command("show")
@click.argument("domain")
@click.argument("target")
@pass_context
def show_command(ctx: Context, domain: str, target: str) -> None:
    """
    Print the text of a mounted file. It may hold a secret: the read is on record.
    """
    click.echo(
        MountManager(log=ctx.logger).read_file(validate_domain(domain), target, actor=_actor()),
        nl=False,
    )


@mount_group.command("file")
@click.argument("domain")
@click.argument("target")
@click.option(
    "--from",
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the text from this file instead of standard input.",
)
@click.option("--mode", default="600", show_default=True, help="Permissions in octal digits.")
@pass_context
def file_command(ctx: Context, domain: str, target: str, source: Path | None, mode: str) -> None:
    """
    Keep a file for the application at TARGET, linked into every release.

    TARGET is a path inside the application (config/production.json), or the path
    in the container of a container application. The text is read from --from, or
    from standard input. The file is the application's: handed to its account, with
    the mode named. A running application reads it at its next restart.
    """
    text = source.read_text(encoding="utf-8") if source is not None else sys.stdin.read()
    view = MountManager(log=ctx.logger).put_file(
        validate_domain(domain), target, text, mode=int(mode, 8), actor=_actor()
    )
    ctx.logger.success(f"Kept {view.record.target} ({view.size} bytes) at {view.path}")


@mount_group.command("bind")
@click.argument("domain")
@click.argument("source")
@click.argument("target")
@click.option("--writable", is_flag=True, default=False, help="Mount it read-write.")
@pass_context
def bind_command(ctx: Context, domain: str, source: str, target: str, writable: bool) -> None:
    """
    Bind SOURCE, a path of the server, at TARGET in a container application.

    Read-only unless --writable. A container that writes a path of the server owns
    it, so the server's own state and the Docker socket are refused.
    """
    view = MountManager(log=ctx.logger).put_path(
        validate_domain(domain), source, target, read_only=not writable, actor=_actor()
    )
    ctx.logger.success(
        f"Bound {view.record.source} at {view.record.target} "
        f"({'read-write' if writable else 'read-only'})"
    )


@mount_group.command("remove")
@click.argument("domain")
@click.argument("target")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, domain: str, target: str, yes: bool) -> None:
    """
    Remove a mount. A file Noust kept is deleted with it.
    """
    if not (yes or ctx.dry_run):
        click.confirm(f"Remove the mount at {target} from {domain}?", abort=True)
    MountManager(log=ctx.logger).remove(validate_domain(domain), target, actor=_actor())
    ctx.logger.success(f"Removed {target}")
