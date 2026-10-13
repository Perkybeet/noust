# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust compose``: look at a Docker Compose document before deploying it.

``noust compose check FILE`` is what the console's preview shows
(:func:`noust.deployers.compose_paste.analyze_document`): the services, the
variables the document reads, and what would refuse or only warn. It runs and
writes nothing. A directory with a ``docker-compose.yml`` is deployed with
``noust create -d DOMAIN -s DIRECTORY``; the console deploys a pasted one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES, analyze_document


@click.group("compose", cls=NoustGroup)
def cli() -> None:
    """Look at a Docker Compose document before it is deployed."""


@cli.command("check", read_only=True)
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@json_option("Print the analysis as JSON.")
@pass_context
def check_command(ctx: Context, file: Path) -> None:
    """
    Say what a Compose document is, without running it.

    FILE is a document, or - for standard input. Exits 1 when something refuses
    it (a privileged container, the Docker socket, a build context or an
    env_file a pasted document does not have).
    """
    if str(file) == "-":
        document = sys.stdin.read(MAX_DOCUMENT_BYTES + 1)
    else:
        document = file.read_text(encoding="utf-8")
    found = analyze_document(document)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "deployable": found.deployable,
                    "services": [
                        {
                            "name": item.name,
                            "image": item.image,
                            "publishes": list(item.publishes),
                            "depends_on": list(item.depends_on),
                        }
                        for item in found.services
                    ],
                    "variables": [
                        {
                            "name": item.name,
                            "default": item.default,
                            "required": item.required,
                            "message": item.message,
                            "services": list(item.services),
                        }
                        for item in found.variables
                    ],
                    "web": found.web,
                    "refused": found.refused,
                    "warned": found.warned,
                }
            )
        )
    else:
        logger = ctx.logger
        for service in found.services:
            ports = f", publishes {', '.join(service.publishes)}" if service.publishes else ""
            logger.key_value(service.name, f"{service.image or 'built'}{ports}")
        if found.variables:
            logger.blank()
            logger.info("Variables (give them with --env NAME=value, or in the console):")
            for variable in found.variables:
                state = (
                    f"default {variable.default!r}"
                    if variable.default is not None
                    else "needs a value"
                )
                logger.info(f"  {variable.name}: {state}")
        for warning in found.warned:
            logger.warning(warning)
        for problem in found.refused:
            logger.error(problem)
        if found.deployable:
            logger.success("This document can be deployed.")
    if not found.deployable:
        raise SystemExit(1)
