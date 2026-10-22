# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app files``: the files of an application, as its own account.

``list`` shows a directory, ``get`` copies a file out, ``put`` copies one in,
``mkdir``, ``move`` and ``remove`` do what they say. All of it is
:mod:`noust.managers.app_files`, which ``/api/apps/{d}/files`` calls too: the
commands run as the application's account, never through a link in its tree, and
leave the environment's files to ``noust env``. This module only parses and presents.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.managers import app_files
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _files(domain: str) -> app_files.AppFiles:
    """
    Build the operations for an application.

    Args:
        domain: The application.

    Returns:
        The operations.
    """
    return app_files.files_for(validate_domain(domain), actor=_actor())


@click.group("files", cls=NoustGroup)
def files_group() -> None:
    """The files of an application, as its own account."""


@files_group.command("list", read_only=True)
@click.argument("domain")
@click.argument("path", default="")
@json_option("Print the listing as JSON.")
@pass_context
def list_command(ctx: Context, domain: str, path: str) -> None:
    """
    List a directory of the application (its own directory by default).
    """
    listing = _files(domain).list_directory(path)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "path": listing.path,
                    "truncated": listing.truncated,
                    "entries": [entry.to_dict() for entry in listing.entries],
                }
            )
        )
        return
    rows = [
        [
            entry.mode,
            entry.owner,
            str(entry.size) if entry.kind == "file" else "-",
            entry.name
            + (
                "/"
                if entry.kind == "dir"
                else f" -> {entry.target}"
                if entry.kind == "link"
                else ""
            ),
        ]
        for entry in listing.entries
    ]
    if not rows:
        ctx.logger.info("Empty")
        return
    ctx.logger.table(["Mode", "Owner", "Size", "Name"], rows)
    if listing.truncated:
        ctx.logger.warning(f"Only the first {app_files.LIST_MAX} entries are shown")


@files_group.command("get")
@click.argument("domain")
@click.argument("path")
@click.argument("destination", required=False, type=click.Path(path_type=Path))
@pass_context
def get_command(ctx: Context, domain: str, path: str, destination: Path | None) -> None:
    """
    Copy a file out of the application: to DESTINATION, or to standard output.
    """
    operations = _files(domain)
    with operations.staged() as copy:
        size = operations.download(path, copy)
        if destination is None:
            sys.stdout.buffer.write(copy.read_bytes())
            return
        shutil.copyfile(copy, destination)
    ctx.logger.success(f"Copied {size} bytes to {destination}")


@files_group.command("put")
@click.argument("domain")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("path")
@click.option("--replace", is_flag=True, default=False, help="Replace a file that is there.")
@pass_context
def put_command(ctx: Context, domain: str, source: Path, path: str, replace: bool) -> None:
    """
    Copy a local file into the application at PATH, as the application's account.
    """
    operations = _files(domain)
    destination = app_files.clean_path(path, allow_empty=False)
    if not replace and operations.resolve(destination).exists():
        raise ValidationError(
            f"{destination} exists already", details="Use --replace to overwrite it.", field="path"
        )
    if source.stat().st_size > app_files.UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"{source} is larger than {app_files.UPLOAD_MAX_BYTES // 1024 // 1024} MiB",
            field="path",
        )
    with operations.staged(source.read_bytes()) as staged:
        written = operations.save_bytes(path, staged, create=True, action="upload")
    ctx.logger.success(f"Wrote {written} bytes to {destination}")


@files_group.command("mkdir")
@click.argument("domain")
@click.argument("path")
@pass_context
def mkdir_command(ctx: Context, domain: str, path: str) -> None:
    """
    Create a directory whose parent exists.
    """
    _files(domain).make_directory(path)
    ctx.logger.success(f"Created {path}")


@files_group.command("move")
@click.argument("domain")
@click.argument("source")
@click.argument("destination")
@click.option("--replace", is_flag=True, default=False, help="Replace what is at DESTINATION.")
@pass_context
def move_command(ctx: Context, domain: str, source: str, destination: str, replace: bool) -> None:
    """
    Rename or move something inside the application.
    """
    _files(domain).move(source, destination, overwrite=replace)
    ctx.logger.success(f"Moved {source} to {destination}")


@files_group.command("remove")
@click.argument("domain")
@click.argument("path")
@click.option(
    "--recursive", "-r", is_flag=True, default=False, help="Delete a directory and what is in it."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, domain: str, path: str, recursive: bool, yes: bool) -> None:
    """
    Delete a file, a link, or a directory. A link is removed, never what it points at.
    """
    if not (yes or ctx.dry_run):
        click.confirm(f"Delete {path} from {domain}?", abort=True)
    _files(domain).remove(path, recursive=recursive)
    ctx.logger.success(f"Deleted {path}")
