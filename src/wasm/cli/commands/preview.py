# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``wasm preview``: pull request previews of an application (2.2).

A presentation layer over :mod:`wasm.managers.previews`, the module the
console's ``/api/apps/{domain}/previews`` endpoints and the webhooks call
too. The CLI runs without the console, so removals here happen in this
process instead of as console jobs.
"""

from __future__ import annotations

import dataclasses
import json

import click

from wasm.cli.app import Context, WasmGroup, global_flags, json_option, pass_context
from wasm.core.store import get_store
from wasm.managers import previews
from wasm.validators.domain import validate_domain


@click.group("preview", cls=WasmGroup)
def cli() -> None:
    """Pull request previews: a short-lived copy of an application per pull request."""


@cli.command("enable")
@click.argument("domain")
@click.option(
    "--domain",
    "base_domain",
    required=True,
    help="Base domain previews answer under; *.BASE must point at this server.",
)
@click.option(
    "--max",
    "max_previews",
    type=int,
    default=previews.DEFAULT_MAX_PREVIEWS,
    show_default=True,
    help="How many previews may exist at once (1 to 20).",
)
@click.option(
    "--ttl",
    default="7d",
    show_default=True,
    help="How long a preview lives without a push: 12h, 7d, 2w (1 hour to 90 days).",
)
@global_flags
@json_option("Print the settings as JSON.")
@pass_context
def enable(ctx: Context, domain: str, base_domain: str, max_previews: int, ttl: str) -> None:
    """
    Turn previews on for an application, or change their settings.

    Each pull request opened against the application's repository gets its
    own copy at pr-<n>-<app>.BASE, built from the pull request's branch and
    removed when it is closed or its time-to-live runs out. Previews copy the
    application's environment variables, production secrets included, and
    use its databases. Pull requests from forks never get one.
    """
    stored = previews.enable_previews(
        domain, base_domain, max_previews=max_previews, ttl_hours=previews.parse_ttl(ttl)
    )
    if ctx.json_output:
        click.echo(json.dumps(dataclasses.asdict(stored)))
        return
    logger = ctx.logger
    logger.success(f"Previews on for {stored.app_domain}")
    logger.key_value("Answer at", f"pr-<n>-...{stored.base_domain}")
    logger.key_value("At most", str(stored.max_previews))
    logger.key_value("Time-to-live", f"{stored.ttl_hours} hours without a push")
    logger.blank()
    logger.warning(
        f"Previews run with a copy of {stored.app_domain}'s environment variables, "
        "production secrets included, and use the same databases."
    )
    logger.info(f"Point a wildcard record *.{stored.base_domain} at this server.")


@cli.command("disable")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before removing existing previews.")
@global_flags
@json_option("Print the previews removed as JSON.")
@pass_context
def disable(ctx: Context, domain: str, yes: bool) -> None:
    """
    Turn previews off for an application and remove the previews it has.
    """
    domain = validate_domain(domain)
    existing = [record.domain for record in get_store().list_previews(domain)]
    if existing and not yes:
        question = (
            f"Remove {len(existing)} preview(s) of {domain} ({', '.join(existing)})? "
            "This deletes their files and certificates"
        )
        try:
            confirmed = click.confirm(question, default=False)
        except click.Abort:
            confirmed = False
        if not confirmed:
            ctx.logger.info("Cancelled; previews are still on")
            return
    removed = previews.disable_previews(domain, logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"domain": domain, "enabled": False, "removed": removed}))
        return
    ctx.logger.success(f"Previews off for {domain}")
    if removed:
        ctx.logger.info(f"Removed {len(removed)}: {', '.join(removed)}")


@cli.command("list")
@click.argument("domain", required=False)
@json_option("Print the settings and previews as JSON.")
@pass_context
def list_command(ctx: Context, domain: str | None) -> None:
    """
    List previews, of one application or of every one.
    """
    store = get_store()
    parent = validate_domain(domain) if domain else None
    records = store.list_previews(parent)
    settings = store.get_preview_settings(parent) if parent else None
    if ctx.json_output:
        payload: dict[str, object] = {
            "items": [
                dataclasses.asdict(record) | {"url": previews.preview_url(record.domain)}
                for record in records
            ]
        }
        if parent:
            payload["domain"] = parent
            payload["settings"] = dataclasses.asdict(settings) if settings else None
        click.echo(json.dumps(payload))
        return
    logger = ctx.logger
    if parent:
        if settings is None:
            logger.info(f"Previews are off for {parent}")
        else:
            logger.key_value(
                "Previews",
                f"under {settings.base_domain}, at most {settings.max_previews}, "
                f"{settings.ttl_hours} h without a push",
            )
    if not records:
        logger.info("No previews")
        return
    logger.table(
        ["Preview", "Of", "PR", "Branch", "Status", "Expires"],
        [
            [
                record.domain,
                record.parent_domain,
                f"#{record.number}",
                record.branch,
                record.status,
                record.expires_at,
            ]
            for record in records
        ],
    )


@cli.command("remove")
@click.argument("preview_domain")
@global_flags
@pass_context
def remove(ctx: Context, preview_domain: str) -> None:
    """
    Remove one preview now: its application, certificate and record.
    """
    warnings = previews.remove_preview(preview_domain, logger=ctx.logger)
    for warning in warnings:
        ctx.logger.warning(warning)
    previews.refresh_sweep_timer(ctx.logger)
    ctx.logger.success(f"Removed {preview_domain}")


@cli.command("sweep")
@global_flags
@json_option("Print the previews removed as JSON.")
@pass_context
def sweep_command(ctx: Context) -> None:
    """
    Remove expired previews, and those whose application is gone.

    What wasm-previews.timer runs every hour.
    """
    removed = previews.sweep(logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"removed": removed}))
        return
    if removed:
        ctx.logger.success(f"Removed {len(removed)}: {', '.join(removed)}")
    else:
        ctx.logger.info("Nothing expired")
