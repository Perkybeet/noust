# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app hooks``: the commands an application's deployments run before and after serving.

``show`` says which hooks the next deployment runs and where they come from:
the operator's own, which win whole when they exist, or the repository's
``noust.yaml``. ``set`` stores the operator's from a file (the same shape as a
``noust.yaml``, holding only ``hooks``); ``clear`` removes them, so the
repository's apply again. Each is :mod:`noust.deployers.helpers.hooks`, which
``/api/apps/{d}/hooks`` calls too and which records the change in the audit
trail. This module only parses, presents and asks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.deployers.helpers.hooks import (
    PHASES,
    AppHooks,
    HookSet,
    describe_hooks,
    set_operator_hooks,
)
from noust.validators.domain import validate_domain

#: The largest document read from a file or standard input.
_MAX_DOCUMENT = 64 * 1024

_SOURCES = {
    "operator": "the operator's hooks (they replace the repository's noust.yaml)",
    "repo": "the repository's noust.yaml, in the code that runs now",
    "none": "none declared",
}


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _print_hooks(logger: Logger, hooks: HookSet) -> None:
    """
    Show each phase's hooks, in order.

    Args:
        logger: Logger the command writes through.
        hooks: The hooks.
    """
    for phase in PHASES:
        selected = hooks.phase(phase)
        if not selected:
            continue
        logger.blank()
        logger.info(f"{phase}:")
        for hook in selected:
            extras = [f"timeout {hook.timeout}s"]
            if hook.service:
                extras.insert(0, f"in {hook.service}")
            if hook.workdir:
                extras.append(f"in {hook.workdir}")
            if hook.migrates:
                extras.append("changes the schema")
            logger.info(f"  {hook.command}  ({', '.join(extras)})")


def _print(ctx: Context, described: AppHooks) -> None:
    """
    Show what applies, for a human or as JSON.

    Args:
        ctx: The CLI context.
        described: What applies.
    """
    if ctx.json_output:
        click.echo(json.dumps(described.to_dict()))
        return
    ctx.logger.key_value("Application", described.domain)
    ctx.logger.key_value("Hooks", _SOURCES[described.hooks.source])
    if described.repository_error:
        ctx.logger.warning(
            "The noust.yaml of the code that runs now is not valid; the next deployment "
            "fails with this unless it is fixed:"
        )
        ctx.logger.info(described.repository_error)
    _print_hooks(ctx.logger, described.hooks)


def _read_document(path: str) -> str:
    """
    Read a hooks document from a file, or from standard input for ``-``.

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
            details="A hooks document is a few lines.",
            field="file",
        )
    return text


@click.group("hooks", cls=NoustGroup)
def hooks() -> None:
    """Commands a deployment runs before it serves and after: see, set or clear them."""


@hooks.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the hooks as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """
    Show the hooks the next deployment of an application runs, and where they come from.
    """
    _print(ctx, describe_hooks(validate_domain(domain)))


@hooks.command("set")
@click.argument("domain")
@click.option(
    "--file",
    "path",
    required=True,
    help="YAML with 'hooks:' and its phases, the shape of a noust.yaml; '-' reads stdin.",
)
@json_option("Print the hooks now in force as JSON.")
@pass_context
def set_command(ctx: Context, domain: str, path: str) -> None:
    """
    Set an application's hooks, replacing the repository's noust.yaml whole.

    A hook runs with the application's identity and its secrets, like its
    migrations: it is code you are adding to every deployment. The document
    is validated before anything is stored, and the change is audited.
    """
    validated = validate_domain(domain)
    document = _read_document(path)
    if ctx.dry_run:
        ctx.logger.info(f"Would set the hooks of {validated} from {path}")
        return
    set_operator_hooks(validated, document, actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps(describe_hooks(validated).to_dict()))
        return
    ctx.logger.success(f"{validated} runs these hooks from its next deployment on")
    _print_hooks(ctx.logger, describe_hooks(validated).hooks)


@hooks.command("clear")
@click.argument("domain")
@pass_context
def clear_command(ctx: Context, domain: str) -> None:
    """
    Remove an application's hooks, so the repository's noust.yaml applies again.
    """
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would clear the hooks of {validated}")
        return
    set_operator_hooks(validated, None, actor=_actor())
    ctx.logger.success(
        f"{validated} has no hooks of its own: the repository's noust.yaml applies again"
    )
