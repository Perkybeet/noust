# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust probe`` command group: addresses asked from outside the application.

    noust probe set shop --url https://shop.example.com/health --public --title "Shop"
    noust probe list
    noust probe run shop
    noust probe status-page set --enable --title "Acme status"
    noust probe status-page publish status.example.com

A thin front end over :class:`~noust.managers.probes.ProbeManager` and
:class:`~noust.managers.status_page.StatusPageSite`, which ``/api/probes`` uses
too, so a probe the console refuses is refused here in the same words. The
monitor asks the probes on a clock (``noust monitor enable``) and tells the
notification channels when one goes down and when it answers again.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.notifications.formatting import format_duration
from noust.core.probe_store import (
    DEFAULT_FAIL_AFTER,
    DEFAULT_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    METHODS,
    Probe,
)
from noust.managers.probes import ProbeManager, ProbeView
from noust.managers.status_page import StatusPage, StatusPageSite


@click.group("probe", cls=NoustGroup)
def cli() -> None:
    """Probes of an address from outside the application, and the public status page."""


def _when(moment: float | None) -> str:
    """
    Args:
        moment: Epoch seconds, or None.

    Returns:
        ``2026-10-16 08:14 UTC``, or ``-``.
    """
    if not moment:
        return "-"
    return datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _state(view: ProbeView) -> str:
    """
    Args:
        view: A probe with where it stands.

    Returns:
        The state in a word, with what the last answer said when it is bad.
    """
    if not view.probe.enabled:
        return "off"
    state = view.state
    if state.state == "down":
        return f"DOWN since {_when(state.since)}: {state.last_error or 'no answer'}"
    return {"up": "up", "unknown": "waiting"}[state.state]


@cli.command("list", read_only=True)
@global_flags
@json_option("Print the probes as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the probes, where each stands and how often it answered."""
    views = ProbeManager().views()
    if ctx.json_output:
        click.echo(json.dumps([view.to_dict() for view in views]))
        return
    if not views:
        ctx.logger.info("No probes. Add one with: noust probe set NAME --url https://example.com/")
        return
    ctx.logger.table(
        ["Probe", "Address", "State", "24 h", "30 d", "Public"],
        [
            [
                view.probe.name,
                view.to_dict()["address"],
                _state(view),
                _percent(view.uptime_24h.percent),
                _percent(view.uptime_30d.percent),
                "yes" if view.probe.public else "-",
            ]
            for view in views
        ],
    )


def _percent(value: float | None) -> str:
    """
    Args:
        value: A percentage, or None.

    Returns:
        It written, or ``-``.
    """
    return "-" if value is None else f"{value:g}%"


@cli.command("set")
@click.argument("name")
@click.option("--url", metavar="URL", help="What to ask: https://example.com/health.")
@click.option("--method", type=click.Choice(list(METHODS)), help="GET (default) or HEAD.")
@click.option(
    "--expect-status",
    metavar="STATUSES",
    help="The statuses that mean well: 200-399 (default) or 200,204.",
)
@click.option(
    "--expect-text", metavar="TEXT", help="A word the page must contain (GET only, first 64 kB)."
)
@click.option(
    "--interval",
    type=click.IntRange(30, 3600),
    help=f"Seconds between two asks (default {DEFAULT_INTERVAL_S}).",
)
@click.option(
    "--timeout",
    type=click.IntRange(1, 60),
    help=f"Seconds an answer may take (default {DEFAULT_TIMEOUT_S}).",
)
@click.option(
    "--fail-after",
    type=click.IntRange(1, 10),
    help=f"Bad answers in a row that make it down (default {DEFAULT_FAIL_AFTER}).",
)
@click.option("--public/--private", default=None, help="Show it on the status page.")
@click.option("--title", metavar="TEXT", help="What the status page calls it.")
@click.option("--enable/--disable", "enabled", default=None, help="Ask it, or stop asking.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    url: str | None,
    method: str | None,
    expect_status: str | None,
    expect_text: str | None,
    interval: int | None,
    timeout: int | None,
    fail_after: int | None,
    public: bool | None,
    title: str | None,
    enabled: bool | None,
) -> None:
    """
    Create a probe, or change the one of that name; what is not named is kept.

    Examples:

    \b
      noust probe set shop --url https://shop.example.com/health --public --title Shop
      noust probe set shop --expect-text "Add to basket" --interval 120
      noust probe set shop --disable
    """
    manager = ProbeManager()
    existing = manager.store.get(name)
    if existing is None and url is None:
        raise click.UsageError("A new probe needs --url")
    base = existing or Probe(name=name, url=url or "")
    changes: dict[str, Any] = {
        key: value
        for key, value in (
            ("url", url),
            ("method", method),
            ("expect_status", expect_status),
            ("expect_text", expect_text),
            ("interval_s", interval),
            ("timeout_s", timeout),
            ("fail_after", fail_after),
            ("public", public),
            ("title", title),
            ("enabled", enabled),
        )
        if value is not None
    }
    saved = manager.save(replace(base, **changes), actor="cli")
    ctx.logger.success(f"Probe {saved.name} saved: it asks {saved.url} every {saved.interval_s} s")
    if not manager.store.states():
        ctx.logger.info("The monitor asks it: noust monitor enable")


@cli.command("remove")
@click.argument("name")
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """Remove a probe with its answers, its days and its incidents."""
    if not ProbeManager().remove(name):
        raise NoustError(f"No probe named {name!r}", details="Run 'noust probe list'.")
    ctx.logger.success(f"Probe {name} removed")


@cli.command("run")
@click.argument("name", required=False)
@global_flags
@json_option("Print the answers as JSON.")
@pass_context
def run_command(ctx: Context, name: str | None) -> None:
    """Ask a probe now (every one with no name), whatever its interval."""
    manager = ProbeManager()
    names = [name] if name else [probe.name for probe in manager.store.probes()]
    if not names:
        raise NoustError("There are no probes", details="Add one: noust probe set NAME --url URL")
    results: list[dict[str, Any]] = []
    for item in names:
        check = manager.check(item)
        if check is None:
            raise NoustError(f"No probe named {item!r}", details="Run 'noust probe list'.")
        results.append(
            {
                "name": item,
                "ok": check.answer.ok,
                "status": check.answer.status,
                "latency_ms": check.answer.latency_ms,
                "error": check.answer.error,
                "state": check.state.state,
            }
        )
    if ctx.json_output:
        click.echo(json.dumps(results))
    else:
        ctx.logger.table(
            ["Probe", "Answer", "Status", "Time", "State"],
            [
                [
                    r["name"],
                    "good" if r["ok"] else f"BAD: {r['error']}",
                    r["status"] or "-",
                    f"{r['latency_ms']} ms" if r["latency_ms"] is not None else "-",
                    r["state"],
                ]
                for r in results
            ],
        )
    if not all(r["ok"] for r in results):
        raise SystemExit(1)


@cli.command("history", read_only=True)
@click.argument("name")
@global_flags
@json_option("Print the history as JSON.")
@pass_context
def history_command(ctx: Context, name: str) -> None:
    """Show the incidents of a probe, newest first."""
    manager = ProbeManager()
    if manager.store.get(name) is None:
        raise NoustError(f"No probe named {name!r}", details="Run 'noust probe list'.")
    incidents = manager.incidents(name)
    if ctx.json_output:
        click.echo(json.dumps([item.to_dict() for item in incidents]))
        return
    if not incidents:
        ctx.logger.success(f"{name} has had no incident.")
        return
    ctx.logger.table(
        ["Started", "Lasted", "First bad answer"],
        [
            [
                _when(item.started_at),
                format_duration(item.ended_at - item.started_at) if item.ended_at else "ongoing",
                item.reason or "-",
            ]
            for item in incidents
        ],
    )


@cli.group("status-page", cls=NoustGroup)
def status_page_group() -> None:
    """The public status page of the probes marked public."""


@status_page_group.command("show", read_only=True)
@global_flags
@json_option("Print the settings as JSON.")
@pass_context
def status_page_show(ctx: Context) -> None:
    """Show how the status page is set up and whether it is on a name."""
    manager = ProbeManager()
    page = StatusPage(manager.store)
    record = StatusPageSite(page).record()
    body: dict[str, Any] = {
        "enabled": page.enabled,
        "directory": str(page.directory),
        "title": page.title,
        "components": sum(1 for p in manager.store.probes() if p.public and p.enabled),
        "domain": record.domain if record else None,
    }
    if ctx.json_output:
        click.echo(json.dumps(body))
        return
    ctx.logger.key_value("Written by the monitor", "yes" if body["enabled"] else "no")
    ctx.logger.key_value("Directory", body["directory"])
    ctx.logger.key_value("Title", body["title"])
    ctx.logger.key_value("Components", str(body["components"]))
    ctx.logger.key_value("Published at", f"https://{record.domain}" if record else "nowhere")


@status_page_group.command("set")
@click.option("--enable/--disable", "enabled", default=None, help="Let the monitor write the page.")
@click.option("--title", metavar="TEXT", help="What the page is called.")
@click.option(
    "--directory", metavar="PATH", help="Where the files go (empty: /var/lib/noust/status-page)."
)
@pass_context
def status_page_set(
    ctx: Context, enabled: bool | None, title: str | None, directory: str | None
) -> None:
    """Change the status page's settings."""
    config = Config()
    changed = False
    for key, value in (("enabled", enabled), ("title", title), ("directory", directory)):
        if value is not None:
            config.set(f"status_page.{key}", value)
            changed = True
    if not changed:
        raise click.UsageError("Name what to change: --enable, --title or --directory")
    config.write()
    ctx.logger.success("Status page settings saved")


@status_page_group.command("write")
@pass_context
def status_page_write(ctx: Context) -> None:
    """Write the page's files now, instead of at the monitor's next scan."""
    for path in StatusPage(ProbeManager().store).write():
        ctx.logger.success(f"Wrote {path}")


@status_page_group.command("publish")
@click.argument("domain")
@click.option(
    "--skip-dns-check",
    is_flag=True,
    help="Do not require the name to resolve to this server (behind NAT).",
)
@pass_context
def status_page_publish(ctx: Context, domain: str, skip_dns_check: bool) -> None:
    """Serve the page on a name of its own, over TLS, with a certificate."""
    manager = ProbeManager()
    if not any(p.public and p.enabled for p in manager.store.probes()):
        ctx.logger.warning("No probe is marked public yet: the page will say so.")
    record = StatusPageSite(StatusPage(manager.store)).publish(
        domain, skip_dns_check=skip_dns_check
    )
    ctx.logger.success(f"The status page is at https://{record.domain}")
    config = Config()
    if not config.get("status_page.enabled", False):
        ctx.logger.info(
            "The monitor keeps it up to date once you run: noust probe status-page set --enable"
        )


@status_page_group.command("unpublish")
@pass_context
def status_page_unpublish(ctx: Context) -> None:
    """Take the page off its name, removing exactly what publishing made."""
    removed = StatusPageSite(StatusPage(ProbeManager().store)).unpublish()
    if not removed:
        ctx.logger.info("The status page was not published.")
        return
    for item in removed:
        (ctx.logger.warning if item.startswith("NOT removed") else ctx.logger.success)(
            f"Removed {item}"
        )
