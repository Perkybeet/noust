# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust upload``: give Noust the code of an application as an archive.

``add`` takes a ``.zip``, ``.tar.gz``, ``.tar.bz2`` or ``.tar.xz`` of a project, extracts it
once with the checks every archive gets, and prints the source that names it
(``upload:<id>``); a Dockerfile (named ``Dockerfile`` or ``Containerfile``, or with
``--dockerfile``, which also reads standard input) becomes a project of that one file.
``noust create --source upload:<id>`` deploys it and
``noust app source change DOMAIN upload:<id>`` points an application at it. ``list`` shows what
was uploaded and whether an application uses it, ``remove`` deletes one nothing uses, and
``prune`` removes what was never used. Each is :mod:`noust.managers.source_uploads`, which
``/api/uploads`` calls too. This module only parses and presents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.managers.source_uploads import (
    DOCKERFILE_MAX_BYTES,
    DOCKERFILE_NAMES,
    SourceUploads,
    UploadedSource,
)
from noust.validators.source import check_upload_id


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _size(count: int) -> str:
    """
    Args:
        count: A number of bytes.

    Returns:
        It for a person: ``12.4 MiB``.
    """
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{count} B"


@click.group("upload", cls=NoustGroup)
def cli() -> None:
    """Give Noust the code of an application as an archive."""


@cli.command("add")
@click.argument("archive", type=click.Path(path_type=Path))
@click.option(
    "--dockerfile",
    is_flag=True,
    help="The file is a Dockerfile, not an archive (- reads it from standard input): "
    "a project of that one file, with nothing for COPY to read.",
)
@json_option("Print the upload as JSON.")
@pass_context
def add_command(ctx: Context, archive: Path, dockerfile: bool) -> None:
    """
    Extract an archive of a project and print the source that names it.

    The archive is a .zip, .tar, .tar.gz, .tgz, .tar.bz2 or .tar.xz. It is read like one from
    the browser: nothing is written outside the project's own directory, links that point out,
    device files and setuid bits are refused, and a bomb is stopped by the caps on members and
    bytes. A file named Dockerfile or Containerfile, or any file with --dockerfile, is a
    Dockerfile instead: the project is that file alone, so a COPY finds nothing and the
    Dockerfile fetches what it needs itself.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would take {archive} as the code of an application")
        return
    manager = SourceUploads()
    if dockerfile and str(archive) == "-":
        text = sys.stdin.read(DOCKERFILE_MAX_BYTES + 1)
        uploaded = manager.add_dockerfile(text, owner=_actor())
    elif dockerfile and archive.name not in DOCKERFILE_NAMES:
        try:
            text = archive.expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValidationError(f"Cannot read {archive}: {exc}", field="archive") from exc
        uploaded = manager.add_dockerfile(text, owner=_actor())
    else:
        uploaded = manager.add_file(archive.expanduser(), owner=_actor())
    if ctx.json_output:
        click.echo(json.dumps(uploaded.to_dict()))
        return
    if uploaded.file_name in DOCKERFILE_NAMES:
        ctx.logger.success(f"Stored the {uploaded.file_name} as a project of one file")
    else:
        ctx.logger.success(f"Extracted {uploaded.file_name}: {uploaded.files} files")
    ctx.logger.key_value("Source", uploaded.source)
    ctx.logger.info(f"Deploy it with: noust create --source {uploaded.source}")


@cli.command("list", read_only=True)
@json_option("Print the uploads as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """Show the archives that were uploaded, newest first, and which ones an application uses."""
    items = SourceUploads().items()
    if ctx.json_output:
        click.echo(json.dumps([item.to_dict() for item in items]))
        return
    if not items:
        ctx.logger.info("Nothing was uploaded")
        return
    for item in items:
        ctx.logger.key_value(
            item.source, f"{item.file_name}, {item.files} files, {_size(item.bytes)}" + _use(item)
        )


def _use(item: UploadedSource) -> str:
    """
    Args:
        item: An upload.

    Returns:
        Whether an application uses it, as the listing says it.
    """
    return ", in use" if item.in_use else ", not in use"


@cli.command("remove")
@click.argument("upload_id", metavar="ID")
@pass_context
def remove_command(ctx: Context, upload_id: str) -> None:
    """Delete an upload that no application deploys from."""
    identifier = check_upload_id(upload_id.removeprefix("upload:"))
    manager = SourceUploads()
    found = manager.get(identifier)
    if found is None:
        raise ValidationError(
            f"There is no upload {identifier}", details="'noust upload list' shows them."
        )
    if found.in_use:
        raise ValidationError(
            "An application deploys from that upload",
            details="Point it at another source first: noust app source change DOMAIN SOURCE",
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would remove {found.source}")
        return
    manager.forget(identifier)
    ctx.logger.success(f"Removed {found.source}")


@cli.command("prune")
@pass_context
def prune_command(ctx: Context) -> None:
    """Remove unfinished uploads and the ones no application used for a week."""
    if ctx.dry_run:
        ctx.logger.info("Would remove the unfinished and unused uploads")
        return
    removed = SourceUploads().prune()
    ctx.logger.success(f"Removed {len(removed)} unused upload(s)")
