# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app cache``: what the package managers keep between an application's builds.

``show`` says how much it holds and ``clear`` empties it, so the next build
downloads everything again. One build can also skip it without emptying
anything else (``noust update DOMAIN --no-cache``), and an application can
always build without it (``noust app build set DOMAIN --no-cache``). Both
commands are :mod:`noust.deployers.helpers.build_cache`, which
``/api/apps/{d}/build-cache`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.utils import format_bytes
from noust.deployers.helpers import build_cache
from noust.validators.domain import validate_domain


def _size(size_bytes: int) -> str:
    """
    Args:
        size_bytes: A size.

    Returns:
        It as a person reads it.
    """
    return format_bytes(size_bytes)


@click.group("cache", cls=NoustGroup)
def cache() -> None:
    """The cache the package managers keep between an application's builds."""


@cache.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the cache as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show where an application's build cache is and how much it holds."""
    described = build_cache.describe(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps(described.to_dict()))
        return
    ctx.logger.key_value("Application", described.domain)
    ctx.logger.key_value("Cache", str(described.path) if described.exists else "none yet")
    ctx.logger.key_value("Size", _size(described.size_bytes))


@cache.command("clear")
@click.argument("domain")
@json_option("Print what was freed as JSON.")
@pass_context
def clear_command(ctx: Context, domain: str) -> None:
    """Empty an application's build cache; its next build downloads what it needs again."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would empty the build cache of {validated}")
        return
    cleared = build_cache.clear(validated)
    if ctx.json_output:
        click.echo(json.dumps(cleared.to_dict()))
        return
    ctx.logger.success(
        f"Emptied the build cache of {validated} ({_size(cleared.size_bytes)} freed)"
    )
