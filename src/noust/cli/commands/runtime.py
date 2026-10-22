# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust runtime``: the language runtimes applications can be pinned to.

``list`` shows what Noust installed (Node and Python, under
``/opt/noust/runtimes``), the PHP versions the server has and the runtimes the
server itself runs, with the applications that run on each. ``install`` makes a
version available (``node@22``, ``python@3.12``, ``php@8.3``) ahead of the
deployment that needs it; a deployment installs a pinned runtime itself when it
is missing. ``remove`` deletes one, unless an application runs on it. Each is
:class:`noust.managers.runtimes.RuntimeManager`, which owns the guards; this
module only parses, presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import app_build_store
from noust.core.exceptions import ValidationError
from noust.core.versions import RUNTIME_LANGUAGES, parse_runtime
from noust.managers.runtimes import InstalledRuntime, RuntimeManager


def _row(runtime: InstalledRuntime, users: list[str]) -> dict[str, object]:
    """
    Describe a runtime for the table and ``--json``.

    Args:
        runtime: The runtime.
        users: The applications whose unit runs on it.

    Returns:
        Its fields and who uses it.
    """
    return {**runtime.to_dict(), "used_by": users}


@click.group("runtime", cls=NoustGroup)
def cli() -> None:
    """The language runtimes (Node, Python, PHP) applications can be pinned to."""


@cli.command("list", read_only=True)
@json_option("Print the runtimes as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """
    List the runtimes Noust installed, the PHP versions and what the server runs.
    """
    manager = RuntimeManager()
    installed = manager.list_installed()
    rows = [
        _row(item, app_build_store.runtime_users(item.language, item.version)) for item in installed
    ]
    system = [
        item.to_dict()
        for item in (manager.host(language) for language in ("node", "python"))
        if item is not None
    ]
    if ctx.json_output:
        click.echo(json.dumps({"installed": rows, "system": system}))
        return
    if not rows and not system:
        ctx.logger.info("No runtime is installed. Pin one: noust runtime install node@22")
        return
    for row in rows:
        users = ", ".join(row["used_by"]) if row["used_by"] else "no application"  # type: ignore[arg-type]
        origin = "managed" if row["managed"] else "system"
        ctx.logger.info(f"{row['language']} {row['version']}  ({origin}, {row['home']})  {users}")
    for row_ in system:
        ctx.logger.info(f"{row_['language']} {row_['version']}  (the server's own)")


@cli.command("install")
@click.argument("spec", metavar="LANGUAGE@VERSION")
@json_option("Print the runtime as JSON.")
@pass_context
def install_command(ctx: Context, spec: str) -> None:
    """
    Install a runtime: node@22, node@22.11.0, python@3.12 or php@8.3.

    Node comes from nodejs.org, verified against its published SHA-256; Python
    through uv; PHP through the distribution's packages. Already installed is
    not an error.
    """
    request = parse_runtime(spec, field="runtime")
    if ctx.dry_run:
        ctx.logger.info(f"Would install {request.text}")
        return
    installed = RuntimeManager().ensure(request)
    if ctx.json_output:
        click.echo(json.dumps(installed.to_dict()))
        return
    ctx.logger.success(f"{installed.language} {installed.version} is at {installed.home}")


@cli.command("remove")
@click.argument("spec", metavar="LANGUAGE@VERSION")
@pass_context
def remove_command(ctx: Context, spec: str) -> None:
    """
    Remove an installed runtime: node@22.11.0 or python@3.12.4 (the full version).

    Refused while an application's unit runs on it.
    """
    request = parse_runtime(spec, field="runtime")
    if request.language not in ("node", "python"):
        raise ValidationError(
            f"{request.language} is not something Noust installed",
            details=f"Only {', '.join(lang for lang in RUNTIME_LANGUAGES if lang != 'php')} "
            "are Noust's to remove; PHP is the distribution's package.",
            field="runtime",
        )
    if request.exact is None:
        raise ValidationError(
            f"{request.text} does not name one version",
            details="Remove one release by its full version: see 'noust runtime list'.",
            field="runtime",
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would remove {request.language} {request.exact}")
        return
    RuntimeManager().remove(request.language, str(request.exact))
    ctx.logger.success(f"Removed {request.language} {request.exact}")
