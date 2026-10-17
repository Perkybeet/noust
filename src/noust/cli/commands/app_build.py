# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app build``: how an application is installed, built and started.

``show`` says which build block the next deployment uses and where it comes
from: the operator's own, which wins whole when it exists, or the repository's
``noust.yaml``. ``set`` stores the operator's (from flags, or from a file with
the shape of a ``noust.yaml`` holding only ``build``); ``unset`` removes one
key of it, or all of it, so the repository's applies again. Each is
:mod:`noust.deployers.helpers.build_config`, which ``/api/apps/{d}/build``
calls too and which records the change in the audit trail. This module only
parses, presents and asks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import app_build_store
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.deployers.helpers.build_config import (
    BUILD_KEYS,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    AppBuild,
    describe_build,
    set_operator_build,
)
from noust.deployers.helpers.project_file import parse_build_document
from noust.validators.domain import validate_domain

#: The largest document read from a file or standard input.
_MAX_DOCUMENT = 64 * 1024

_SOURCES = {
    "operator": "the operator's settings (they replace the repository's noust.yaml)",
    "repo": "the repository's noust.yaml, in the code that runs now",
    "none": "nothing declared: the commands of the application's type",
}

#: What each key is called when it is shown.
_LABELS = {
    "base_directory": "Base directory",
    "install": "Install",
    "build": "Build",
    "start": "Start",
    "publish_directory": "Publish directory",
    "runtime": "Runtime",
    "cache": "Build cache",
    "timeout": "Timeout (seconds)",
}


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _print(ctx: Context, described: AppBuild) -> None:
    """
    Show what applies, for a human or as JSON.

    Args:
        ctx: The CLI context.
        described: What applies.
    """
    if ctx.json_output:
        click.echo(json.dumps(described.to_dict()))
        return
    logger: Logger = ctx.logger
    logger.key_value("Application", described.domain)
    logger.key_value("Build block", _SOURCES[described.build.source])
    if described.repository_error:
        logger.warning(
            "The noust.yaml of the code that runs now is not valid; the next deployment "
            "fails with this unless it is fixed:"
        )
        logger.info(described.repository_error)
    values = described.build.to_dict()
    for key in BUILD_KEYS:
        if values[key] is not None:
            logger.key_value(_LABELS[key], values[key])
    applied = described.applied
    if applied:
        logger.blank()
        logger.key_value("Unit starts", applied.get("start") or "-")
        logger.key_value("Unit directory", applied.get("working_directory") or "-")
        runtime = applied.get("runtime")
        if runtime:
            logger.key_value("Unit runtime", f"{runtime['language']} {runtime['version']}")


def _read_document(path: str) -> str:
    """
    Read a build document from a file, or from standard input for ``-``.

    Args:
        path: The file, or ``-``.

    Returns:
        Its text.

    Raises:
        ValidationError: It cannot be read, or is too large.
    """
    try:
        text = sys.stdin.read(_MAX_DOCUMENT + 1) if path == "-" else Path(path).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read {path}: {exc}", details="Give a readable UTF-8 YAML file.", field="file"
        ) from exc
    if len(text) > _MAX_DOCUMENT:
        raise ValidationError(
            f"{path} is larger than {_MAX_DOCUMENT // 1024} KiB",
            details="A build block is a few lines.",
            field="file",
        )
    return text


def _stored(domain: str) -> dict[str, str | bool | int]:
    """
    Read the operator's block as the mapping its document holds.

    Args:
        domain: A validated domain.

    Returns:
        The keys it sets; empty when the operator has none.
    """
    document = app_build_store.get_document(domain)
    if document is None:
        return {}
    return parse_build_document(document, source="operator").to_mapping()


def _document(mapping: dict[str, str | bool | int]) -> str:
    """
    Write a mapping of keys as the document ``set_operator_build`` validates.

    Args:
        mapping: Key to text.

    Returns:
        ``build:`` and its keys, as YAML.
    """
    return yaml.safe_dump({"build": mapping}, sort_keys=False)


@click.group("build", cls=NoustGroup)
def build() -> None:
    """The commands an application is installed, built and started with: see, set or unset them."""


@build.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the build block as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """
    Show the build block the next deployment of an application uses, and where it comes from.
    """
    _print(ctx, describe_build(validate_domain(domain)))


@build.command("set")
@click.argument("domain")
@click.option(
    "--base-directory",
    metavar="DIR",
    help="The subdirectory of the repository the project lives in (apps/web).",
)
@click.option(
    "--install", "install_command", metavar="COMMAND", help="Replaces the install command."
)
@click.option("--build", "build_command", metavar="COMMAND", help="Replaces the build command.")
@click.option(
    "--start",
    "start_command",
    metavar="COMMAND",
    help="Replaces the start command. systemd expands ${PORT}; it is never run by a shell.",
)
@click.option(
    "--publish-directory",
    metavar="DIR",
    help="Where a static build leaves the files the web server serves, from the project.",
)
@click.option(
    "--runtime",
    metavar="LANG@VERSION",
    help="Pin the runtime: node@22, python@3.12 or php@8.3.",
)
@click.option(
    "--cache/--no-cache",
    "cache",
    default=None,
    help="--no-cache builds every deployment from scratch, without the package managers' "
    "cache or the dependencies of the release that serves.",
)
@click.option(
    "--timeout",
    "timeout",
    type=click.IntRange(MIN_TIMEOUT, MAX_TIMEOUT),
    metavar="SECONDS",
    help="How long the install and the build may each take before they are stopped "
    "(the server's deploy.install_timeout and deploy.build_timeout otherwise).",
)
@click.option(
    "--file",
    "path",
    help="Replace the whole block with a YAML file holding 'build:'; '-' reads stdin.",
)
@json_option("Print the build block now in force as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    base_directory: str | None,
    install_command: str | None,
    build_command: str | None,
    start_command: str | None,
    publish_directory: str | None,
    runtime: str | None,
    cache: bool | None,
    timeout: int | None,
    path: str | None,
) -> None:
    """
    Set an application's build block, replacing the repository's noust.yaml whole.

    The keys you give are added to the operator's block (or become it, if there
    is none): what the repository's noust.yaml said no longer applies. The
    commands run in the build sandbox and, for start, as the application, with
    its secrets: they are code you are adding to every deployment. The block is
    validated before anything is stored, and the change is audited.
    """
    validated = validate_domain(domain)
    given: dict[str, str | bool | int | None] = {
        "base_directory": base_directory,
        "install": install_command,
        "build": build_command,
        "start": start_command,
        "publish_directory": publish_directory,
        "runtime": runtime,
        "cache": cache,
        "timeout": timeout,
    }
    flags = {key: value for key, value in given.items() if value is not None}
    if path is not None and flags:
        raise click.UsageError("Give --file, or the keys to set, not both")
    if path is None and not flags:
        raise click.UsageError("Give at least one key to set, or --file")
    document = (
        _read_document(path) if path is not None else _document({**_stored(validated), **flags})
    )
    if ctx.dry_run:
        ctx.logger.info(f"Would set the build block of {validated}")
        return
    set_operator_build(validated, document, actor=_actor())
    described = describe_build(validated)
    if ctx.json_output:
        click.echo(json.dumps(described.to_dict()))
        return
    ctx.logger.success(
        f"{validated} uses this build block from its next deployment on "
        "(it replaces the repository's noust.yaml whole)"
    )
    _print(ctx, described)


@build.command("unset")
@click.argument("domain")
@click.argument("keys", nargs=-1, metavar="[KEY]...")
@pass_context
def unset_command(ctx: Context, domain: str, keys: tuple[str, ...]) -> None:
    """
    Remove keys of the operator's block, or all of it, so the repository's applies again.

    With no KEY the whole block goes. With keys (base_directory, install,
    build, start, publish_directory, runtime, cache, timeout) only those do, and the block
    that is left still replaces the repository's.
    """
    validated = validate_domain(domain)
    wanted = [key.replace("-", "_") for key in keys]
    unknown = [key for key in wanted if key not in BUILD_KEYS]
    if unknown:
        raise click.UsageError(
            f"Not a key of the build block: {', '.join(unknown)}. Keys: {', '.join(BUILD_KEYS)}"
        )
    if ctx.dry_run:
        ctx.logger.info(
            f"Would unset {', '.join(wanted) or 'the whole build block'} of {validated}"
        )
        return
    if not wanted:
        set_operator_build(validated, None, actor=_actor())
        ctx.logger.success(
            f"{validated} has no build block of its own: the repository's noust.yaml applies again"
        )
        return
    remaining = {key: value for key, value in _stored(validated).items() if key not in wanted}
    set_operator_build(validated, _document(remaining) if remaining else None, actor=_actor())
    ctx.logger.success(f"Unset {', '.join(wanted)} for {validated}")
