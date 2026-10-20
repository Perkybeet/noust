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
from noust.recipes import coolify, dokploy
from noust.recipes.compose_template import (
    ConvertedTemplate,
    default_host_port,
    env_file,
    values_file,
)
from noust.recipes.coolify import MAX_TEMPLATE_BYTES
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
@click.argument("file", type=click.Path(path_type=Path, dir_okay=True, allow_dash=True))
@click.option(
    "--from",
    "origin",
    type=click.Choice(["coolify", "dokploy"]),
    default="coolify",
    show_default=True,
    help="Whose template FILE is.",
)
@click.option(
    "--template",
    "template_file",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="dokploy: the template's template.toml (read from FILE's folder when FILE is a folder).",
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
    help="Write docker-compose.yml and .env (0600, with the generated secrets) into this folder, "
    "and the files a Dokploy template mounts.",
)
@json_option("Print what was made as JSON (never the secrets).")
@pass_context
def import_command(
    ctx: Context,
    file: Path,
    origin: str,
    template_file: Path | None,
    domain: str | None,
    port: int | None,
    out: Path | None,
) -> None:
    """
    Turn a Coolify or Dokploy service template into a Compose document Noust deploys.

    FILE is a template from Coolify's catalogue (a Compose file with SERVICE_URL_*,
    SERVICE_PASSWORD_* and the like), or from Dokploy's (a docker-compose.yml with its
    template.toml, or the folder that holds both), or - for standard input. The variables keep
    their names; their values are made here (new secrets) and written to .env, never printed. The
    routing entry becomes a port on 127.0.0.1, the Traefik labels and the proxy's network go, and
    what cannot be translated is said. Nothing is run: the result is judged like any Compose file
    (a template that mounts the Docker socket is refused). Then: noust create -d DOMAIN -s FOLDER.
    """
    name = validate_domain(domain) if domain else None
    result: ConvertedTemplate
    files: dict[str, str] = {}
    secret_files: set[str] = set()
    if origin == "dokploy":
        compose_text, template_text = _dokploy_inputs(file, template_file)
        converted = dokploy.convert(
            compose_text, template_text, domain=name, port=port or default_host_port()
        )
        result, files, secret_files = converted, converted.files, converted.secret_files
    else:
        if template_file is not None:
            raise click.UsageError("--template goes with --from dokploy.")
        if file.is_dir():
            raise click.UsageError(f"{file} is a folder: a Coolify template is one Compose file.")
        if str(file) == "-":
            text = sys.stdin.read(MAX_TEMPLATE_BYTES + 1)
        else:
            text = file.read_text(encoding="utf-8")
        result = coolify.convert(text, domain=name, port=port or default_host_port())
    found = analyze_document(result.document, provided=files)
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
        fs.write_text(
            out / ".env",
            env_file(result.values) if origin == "dokploy" else values_file(result.values),
            mode=SECRET_MODE,
        )
        for relative, content in files.items():
            target = out / relative
            fs.make_dir(target.parent, mode=0o755, parents=True)
            fs.write_text(target, content, mode=SECRET_MODE if relative in secret_files else 0o644)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "deployable": found.deployable,
                    "written": str(out) if out is not None else None,
                    "files": sorted(files),
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
        if files:
            logger.info(f"and {len(files)} file(s) the template mounts, under {out}/files")
        logger.info(f"Deploy it: noust create -d {name or 'DOMAIN'} -s {out}")
    else:
        logger.info("Nothing was written. Add --out FOLDER to write the document and its .env.")


def _dokploy_inputs(file: Path, template_file: Path | None) -> tuple[str, str | None]:
    """
    Read a Dokploy template's two files.

    Args:
        file: The ``docker-compose.yml``, a folder that holds it and ``template.toml``, or ``-``.
        template_file: The ``template.toml`` when it is not beside the Compose file.

    Returns:
        The Compose text and the template's text (None when there is none).

    Raises:
        click.UsageError: A folder with no ``docker-compose.yml``.
    """
    if str(file) == "-":
        compose_text = sys.stdin.read(dokploy.MAX_TEMPLATE_BYTES + 1)
        folder = None
    elif file.is_dir():
        compose = file / "docker-compose.yml"
        if not compose.is_file():
            raise click.UsageError(
                f"{file} has no docker-compose.yml: it is not a Dokploy template."
            )
        compose_text = compose.read_text(encoding="utf-8")
        folder = file
    else:
        compose_text = file.read_text(encoding="utf-8")
        folder = file.parent
    toml = template_file or (folder / "template.toml" if folder is not None else None)
    if toml is not None and toml.is_file():
        return compose_text, toml.read_text(encoding="utf-8")
    return compose_text, None
