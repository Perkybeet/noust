# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app traffic``: what the proxy's access log says about an application.

    noust app traffic shop.example.com
    noust app traffic shop.example.com --window 7d --top 20
    noust app traffic shop.example.com --json

Requests and how they ended, bytes served, the pages asked for most, the
networks they came from, the status codes and the referrers. It reads the
history the monitor keeps (``noust monitor enable`` records it); query strings
are never kept and a client is its network, not its address.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.monitor.timeseries import MetricsStore, default_metrics_db_path
from noust.monitor.traffic_store import TrafficPoint
from noust.validators.domain import validate_domain

#: How far back, by name.
WINDOWS = {"24h": 86_400, "7d": 7 * 86_400, "30d": 30 * 86_400, "90d": 90 * 86_400}

#: What each ranking is called to a person.
HEADINGS = {
    "path": "Pages asked for most",
    "network": "Networks",
    "status": "Status codes",
    "referrer": "Referrers",
}


def _size(value: int) -> str:
    """
    Args:
        value: Bytes.

    Returns:
        The size in the unit that reads best.
    """
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _row(point: TrafficPoint) -> dict[str, int]:
    """
    Args:
        point: One step of the traffic.

    Returns:
        It as JSON.
    """
    return {
        "ts": point.ts,
        "requests": point.requests,
        "s2xx": point.s2xx,
        "s3xx": point.s3xx,
        "s4xx": point.s4xx,
        "s5xx": point.s5xx,
        "bytes": point.bytes,
    }


@click.command("traffic", cls=NoustCommand, read_only=True)
@click.argument("domain")
@click.option(
    "--window",
    type=click.Choice(list(WINDOWS)),
    default="24h",
    show_default=True,
    help="How far back to look.",
)
@click.option(
    "--top",
    type=click.IntRange(1, 50),
    default=10,
    show_default=True,
    help="Entries in each ranking.",
)
@json_option("Print the traffic as JSON.")
@pass_context
def traffic_command(ctx: Context, domain: str, window: str, top: int) -> None:
    """Show the requests an application served, and what was asked of it."""
    name = validate_domain(domain)
    if get_store().get_app(name) is None:
        raise NoustError(
            f"Application not found: {name}", details="Run 'noust list' to see what is deployed."
        )
    path = default_metrics_db_path()
    if not path.is_file():
        raise NoustError(
            "There is no traffic history yet",
            details="The monitor records it: run 'noust monitor enable'.",
        )
    store = MetricsStore(path)
    end = store.now()
    found = store.traffic_summary(name, end - WINDOWS[window], end, top=top)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": name,
                    "window": window,
                    "start": found.start,
                    "end": found.end,
                    "step": found.step,
                    "totals": _row(found.totals),
                    "points": [_row(point) for point in found.points],
                    "top": {
                        dimension: [{"key": key, "count": count} for key, count in ranked]
                        for dimension, ranked in found.top.items()
                    },
                }
            )
        )
        return
    totals = found.totals
    ctx.logger.header(f"Traffic of {name}, last {window}")
    if not totals.requests:
        ctx.logger.info("No request was counted in this window.")
        return
    ctx.logger.key_value("Requests", f"{totals.requests:,}")
    ctx.logger.key_value(
        "Answered",
        f"{totals.s2xx:,} 2xx, {totals.s3xx:,} 3xx, {totals.s4xx:,} 4xx, {totals.s5xx:,} 5xx",
    )
    ctx.logger.key_value("Served", _size(totals.bytes))
    for dimension, heading in HEADINGS.items():
        ranked = found.top.get(dimension) or []
        if not ranked:
            continue
        ctx.logger.section(heading)
        ctx.logger.table(["Count", "Key"], [[f"{count:,}", key] for key, count in ranked])
