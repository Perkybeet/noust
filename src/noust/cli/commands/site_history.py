# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust site history``, ``diff`` and ``restore``: the earlier texts of a site's file.

A presentation layer over :mod:`noust.managers.site_history`, which
``/api/sites/{domain}/config/versions`` reads too. Every write of a site's file keeps its
text, and a hand edit that a deploy is about to overwrite is kept first, so a change is never
lost for good. Putting a version back is the editor's own save: the web server tests the text
before it is installed.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.site_history_store import sha256_of
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.site_history import unified_diff
from noust.managers.webserver import WebServerManager
from noust.validators.domain import validate_domain

_WEBSERVER = click.option(
    "--webserver", "-w", type=click.Choice(["nginx", "apache"]), help="The web server."
)


def _manager(domain: str, webserver: str | None) -> WebServerManager:
    """
    Find the web server that has this site.

    Args:
        domain: The site's name.
        webserver: ``nginx`` or ``apache``; whichever has the site by default.

    Returns:
        Its manager.

    Raises:
        NoustError: Neither has the site.
    """
    candidates: list[WebServerManager] = (
        [{"nginx": NginxManager, "apache": ApacheManager}[webserver]()]
        if webserver
        else [NginxManager(), ApacheManager()]
    )
    for manager in candidates:
        if manager.site_exists(domain):
            return manager
    raise NoustError(
        f"Site not found: {domain}",
        details="Run 'noust site list' to see the virtual hosts this server knows about.",
    )


@click.command("history", cls=NoustCommand, read_only=True)
@click.argument("domain")
@_WEBSERVER
@json_option("Print the versions as JSON.")
@pass_context
def history_command(ctx: Context, domain: str, webserver: str | None) -> None:
    """List the versions kept of a site's file, newest first, and say if it has drifted."""
    name = validate_domain(domain)
    manager = _manager(name, webserver)
    current = sha256_of(manager.get_site_config(name) or "")
    drift = manager.history.drift({name: manager.config_path(name)}, manager.runner).get(name)
    versions = manager.history.versions(name)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "site": name,
                    "webserver": manager.backend.name,
                    "edited_outside": bool(drift and drift.edited_outside),
                    "pending_reload": bool(drift and drift.pending_reload),
                    "versions": [
                        {
                            "version": item.version,
                            "sha256": item.sha256,
                            "size": item.size,
                            "source": item.source,
                            "actor": item.actor,
                            "created_at": item.created_at,
                            "current": item.sha256 == current,
                        }
                        for item in versions
                    ],
                }
            )
        )
        return
    if drift and drift.edited_outside:
        ctx.logger.warning(
            f"{name} was changed outside Noust: its file is not the latest version below"
        )
    if drift and drift.pending_reload:
        ctx.logger.warning(
            f"{name} was written after {manager.backend.name} last loaded its configuration: "
            "test and reload it to apply it"
        )
    if not versions:
        ctx.logger.info(f"No version of {name} has been kept yet")
        return
    ctx.logger.table(
        ["Version", "When", "How", "By", "Size", ""],
        [
            [
                str(item.version),
                item.created_at[:19].replace("T", " "),
                item.source,
                item.actor or "",
                f"{item.size} B",
                "now" if item.sha256 == current else "",
            ]
            for item in versions
        ],
    )


@click.command("diff", cls=NoustCommand, read_only=True)
@click.argument("domain")
@click.argument("version", type=int)
@click.option("--against", type=int, help="Compare with this version instead of the file now.")
@_WEBSERVER
@pass_context
def diff_command(
    ctx: Context, domain: str, version: int, against: int | None, webserver: str | None
) -> None:
    """Show what changed between a version of a site's file and the file now."""
    name = validate_domain(domain)
    manager = _manager(name, webserver)
    older = manager.history.version(name, version)
    if older is None:
        raise NoustError(
            f"Version {version} of {name} is not kept",
            details=f"See what is: noust site history {name}",
        )
    if against is None:
        newer_text, newer_label = manager.get_site_config(name) or "", "now"
    else:
        newer = manager.history.version(name, against)
        if newer is None:
            raise NoustError(
                f"Version {against} of {name} is not kept",
                details=f"See what is: noust site history {name}",
            )
        newer_text, newer_label = newer.text, f"version {against}"
    text = unified_diff(
        older.text, newer_text, older_label=f"version {version}", newer_label=newer_label
    )
    click.echo(text if text else f"No difference between version {version} and {newer_label}")


@click.command("restore", cls=NoustCommand)
@click.argument("domain")
@click.argument("version", type=int)
@click.option("--reload", "reload_now", is_flag=True, help="Reload the web server after.")
@_WEBSERVER
@pass_context
def restore_command(
    ctx: Context, domain: str, version: int, reload_now: bool, webserver: str | None
) -> None:
    """
    Put an earlier version of a site's file back.

    The web server tests the text before it is installed and refuses it with its own
    words; the result is a version of its own. Without --reload the web server keeps
    running what it loaded.
    """
    name = validate_domain(domain)
    manager = _manager(name, webserver)
    older = manager.history.version(name, version)
    if older is None:
        raise NoustError(
            f"Version {version} of {name} is not kept",
            details=f"See what is: noust site history {name}",
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would put version {version} of {name} back")
        return
    manager.replace_site_config(name, older.text, source="restore")
    ctx.logger.success(f"Version {version} of {name} is back")
    if reload_now:
        if manager.reload():
            ctx.logger.success(f"{manager.backend.name} reloaded")
        else:
            raise NoustError(f"{manager.backend.name} did not reload", details="See its log.")
    else:
        ctx.logger.info(
            f"Reload {manager.backend.name} to apply it: systemctl reload {manager.backend.service}"
        )
