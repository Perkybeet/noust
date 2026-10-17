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
from noust.core.fs import SECRET_MODE, get_fs
from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES, analyze_document
from noust.recipes.coolify import MAX_TEMPLATE_BYTES, convert, default_host_port, values_file
from noust.validators.domain import validate_domain


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


@cli.command("import")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@click.option(
    "--from", "origin", type=click.Choice(["coolify"]), default="coolify", show_default=True
)
@click.option(
    "--domain", help="The name the stack will be served on (fills its address variables)."
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    help="The host port of the web service on 127.0.0.1 (a free one by default).",
)
@click.option(
    "--out",
    "out",
    type=click.Path(path_type=Path, file_okay=False),
    help="Write docker-compose.yml and .env (0600, with the generated secrets) into this folder.",
)
@json_option("Print what was made as JSON (never the secrets).")
@pass_context
def import_command(
    ctx: Context, file: Path, origin: str, domain: str | None, port: int | None, out: Path | None
) -> None:
    """
    Turn a Coolify service template into a Compose document Noust deploys.

    FILE is a template from Coolify's catalogue (a Compose file with SERVICE_URL_*,
    SERVICE_PASSWORD_* and the like), or - for standard input. The variables keep their names; their
    values are made here (new secrets) and written to .env, never printed. The routing entry becomes
    a port on 127.0.0.1, the Traefik labels and the coolify network go, and what cannot be
    translated is said. Nothing is run: the result is judged like any Compose file (a template that
    mounts the Docker socket is refused). Then: noust create -d DOMAIN -s FOLDER.
    """
    if str(file) == "-":
        text = sys.stdin.read(MAX_TEMPLATE_BYTES + 1)
    else:
        text = file.read_text(encoding="utf-8")
    name = validate_domain(domain) if domain else None
    result = convert(text, domain=name, port=port or default_host_port())
    found = analyze_document(result.document)
    # A variable with no default that nobody made a value for: Compose stops on a ${NAME:?}, and
    # starts the others with a blank. Saying so now is kinder than at the deploy. The addresses
    # asked for without a domain are reported on their own.
    reported = {v.name for v in result.variables}
    required = [
        v for v in found.variables if v.required and v.name not in {*result.values, *reported}
    ]
    if out is not None:
        fs = get_fs()
        fs.make_dir(out, mode=0o755, parents=True)
        fs.write_text(out / "docker-compose.yml", result.document, mode=0o644)
        fs.write_text(out / ".env", values_file(result.values), mode=SECRET_MODE)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "deployable": found.deployable,
                    "written": str(out) if out is not None else None,
                    "routed": list(result.routed) if result.routed else None,
                    "variables": [variable.to_dict() for variable in result.variables],
                    "metadata": result.metadata,
                    "required": [{"name": v.name, "message": v.message} for v in required],
                    "adjustments": result.adjustments,
                    "warnings": result.warnings,
                    "refused": found.refused,
                    "document": result.document if out is None else None,
                }
            )
        )
        if not found.deployable:
            raise SystemExit(1)
        return
    logger = ctx.logger
    if out is None:
        click.echo(result.document, nl=False)
    logger.blank()
    if result.routed:
        logger.key_value("Web", f"{result.routed[0]}, port {result.routed[1]}")
    for line in result.adjustments:
        logger.info(line)
    for line in result.warnings:
        logger.warning(line)
    made = [v for v in result.variables if v.origin != "ask"]
    asked = [v for v in result.variables if v.origin == "ask"]
    if made:
        logger.key_value(
            "Values made",
            ", ".join(f"{v.name}{' (secret)' if v.secret else ''}" for v in made),
        )
    for variable in asked:
        logger.warning(f"{variable.name}: {variable.note}")
    for needed in required:
        logger.warning(
            f"{needed.name} has no default"
            + (f" ({needed.message})" if needed.message else "")
            + (f": give it a value in {out}/.env" if out is not None else "")
        )
    if found.refused:
        logger.error("It cannot be deployed as it is:")
        for problem in found.refused:
            logger.error(f"  {problem}")
        raise SystemExit(1)
    for problem in found.warned:
        logger.warning(problem)
    if out is not None:
        logger.success(f"Wrote {out}/docker-compose.yml and {out}/.env (0600)")
        logger.info(f"Deploy it: noust create -d {name or 'DOMAIN'} -s {out}")
    else:
        logger.info("Nothing was written. Add --out FOLDER to write the document and its .env.")
