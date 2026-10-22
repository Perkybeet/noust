# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust alert`` command group: rules on the metrics Noust records.

A thin front end over :class:`~noust.core.alerts.rules.AlertRules`, the class
``/api/alerts`` uses too, so a rule the console refuses is refused here in the
same words. A rule says "tell me when this metric has been above (or below)
that for this long"; the monitor judges every rule once a minute against the
recorded history and sends ``alert_firing`` and ``alert_resolved`` through the
notification channels (``noust notify``). Whoever is on call takes a firing alert
with ``noust alert ack``.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.alerts import (
    METRICS,
    SEVERITIES,
    AlertRule,
    AlertRules,
    format_duration_short,
    format_value,
    parse_duration,
    parse_threshold,
)
from noust.core.alerts.model import DEFAULT_COOLDOWN_S, DEFAULT_DURATION_S
from noust.core.exceptions import NoustError
from noust.monitor.alert_engine import acknowledge_alert


@click.group("alert", cls=NoustGroup)
def cli() -> None:
    """Alert rules on CPU, memory, disk, load and traffic."""


def _fail(ctx: Context, exc: NoustError) -> SystemExit:
    """
    Args:
        ctx: The command's context.
        exc: What went wrong.

    Returns:
        The exit to raise, after the error was reported.
    """
    ctx.logger.error(exc.message, details=exc.details)
    return SystemExit(1)


def _limit(rule: AlertRule) -> str:
    """
    Args:
        rule: A rule.

    Returns:
        ``> 90%`` in the metric's unit.
    """
    return f"{rule.comparator} {format_value(rule.spec.unit, rule.threshold, limit=True)}"


@cli.command("metrics", read_only=True)
@global_flags
@json_option("Print the metrics as JSON.")
@pass_context
def metrics_command(ctx: Context) -> None:
    """List what a rule can watch, and the unit its limit is written in."""
    if ctx.json_output:
        click.echo(
            json.dumps([{"id": m.id, "scope": m.scope, "unit": m.unit} for m in METRICS.values()])
        )
        return
    ctx.logger.table(
        ["Metric", "About", "Limit is written as"],
        [
            [
                m.id,
                "the server" if m.scope == "server" else "an application",
                {
                    "percent": "a percentage: 90 or 90%",
                    "bytes": "a size: 512MB, 2GB",
                    "bytes_s": "a rate: 2MB/s",
                    "number": "a number",
                    "per_min": "a count per minute",
                    "ms": "a time: 500ms, 2s",
                }[m.unit],
            ]
            for m in METRICS.values()
        ],
    )


@cli.command("list", read_only=True)
@global_flags
@json_option("Print the rules as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the alert rules and how many subjects of each are firing."""
    rules = AlertRules()
    states = rules.states()
    items = rules.rules()
    if ctx.json_output:
        click.echo(
            json.dumps(
                [
                    {
                        **rule.to_dict(),
                        "states": [s.to_dict() for s in states if s.rule_id == rule.id],
                    }
                    for rule in items
                ]
            )
        )
        return
    if not items:
        ctx.logger.info(
            "No alert rules. Add one with: noust alert set NAME --metric cpu --above 90%"
        )
        return

    def state_of(rule: AlertRule) -> str:
        if not rule.enabled:
            return "off"
        firing = [
            s.subject or "server" for s in states if s.rule_id == rule.id and s.state == "firing"
        ]
        return "FIRING: " + ", ".join(firing) if firing else "ok"

    ctx.logger.table(
        ["Rule", "Watches", "Limit", "For", "Severity", "State"],
        [
            [
                rule.name,
                rule.metric + (f" of {rule.app_domain}" if rule.app_domain else ""),
                _limit(rule),
                format_duration_short(rule.duration_s),
                rule.severity,
                state_of(rule),
            ]
            for rule in items
        ],
    )


@cli.command("status", read_only=True)
@global_flags
@json_option("Print the firing alerts as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show the alerts that are firing now."""
    rules = AlertRules()
    by_id = {rule.id: rule for rule in rules.rules()}
    firing = [s for s in rules.states() if s.state == "firing" and s.rule_id in by_id]
    if ctx.json_output:
        click.echo(json.dumps([{**s.to_dict(), "rule": by_id[s.rule_id].name} for s in firing]))
        return
    if not firing:
        ctx.logger.success("Nothing is firing.")
        return
    ctx.logger.table(
        ["Rule", "About", "Now", "Limit", "Taken by"],
        [
            [
                by_id[s.rule_id].name,
                s.subject or "the server",
                format_value(by_id[s.rule_id].spec.unit, s.last_value or 0.0),
                _limit(by_id[s.rule_id]),
                s.acknowledged_by or "-",
            ]
            for s in firing
        ],
    )


@cli.command("set")
@click.argument("name")
@click.option("--metric", required=True, type=click.Choice(list(METRICS)), help="What to watch.")
@click.option(
    "--above", metavar="LIMIT", help="Fire when it stays above LIMIT (90%, 512MB, 2MB/s)."
)
@click.option("--below", metavar="LIMIT", help="Fire when it stays below LIMIT.")
@click.option("--or-equal", is_flag=True, help="Count the limit itself as a breach (>= and <=).")
@click.option(
    "--for",
    "duration",
    default=format_duration_short(DEFAULT_DURATION_S),
    show_default=True,
    help="How long it must hold before it fires (at least 1m): 5m, 1h.",
)
@click.option(
    "--hysteresis",
    metavar="N",
    default="0",
    show_default=True,
    help="How far back inside the limit it must come to clear, in the metric's unit.",
)
@click.option(
    "--cooldown",
    default=format_duration_short(DEFAULT_COOLDOWN_S),
    show_default=True,
    help="The least time between two messages about the same thing.",
)
@click.option(
    "--app",
    "app_domain",
    metavar="DOMAIN",
    help="The application, for an application metric (every one by default).",
)
@click.option("--severity", type=click.Choice(SEVERITIES), default="warning", show_default=True)
@click.option("--remind", is_flag=True, help="Say it again every cooldown while it keeps firing.")
@click.option("--disabled", is_flag=True, help="Create it switched off.")
@click.option("--note", default="", help="Free text for whoever reads the rule later.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    metric: str,
    above: str | None,
    below: str | None,
    or_equal: bool,
    duration: str,
    hysteresis: str,
    cooldown: str,
    app_domain: str | None,
    severity: str,
    remind: bool,
    disabled: bool,
    note: str,
) -> None:
    """
    Create an alert rule, or replace the one of that name.

    Examples:

    \b
      noust alert set cpu-high --metric cpu --above 90% --for 5m
      noust alert set low-memory --metric app_memory --app shop.example.com --below 256MB
      noust alert set errors --metric app_5xx --above 20 --for 10m --severity failed
    """
    if (above is None) == (below is None):
        raise click.UsageError("Give the limit as exactly one of --above or --below")
    try:
        unit = METRICS[metric].unit
        comparator = (
            (">=" if or_equal else ">") if above is not None else ("<=" if or_equal else "<")
        )
        rule = AlertRule(
            name=name,
            metric=metric,
            comparator=comparator,
            threshold=parse_threshold(above if above is not None else below or "", unit),
            app_domain=app_domain,
            duration_s=parse_duration(duration),
            hysteresis=parse_threshold(hysteresis, unit),
            cooldown_s=parse_duration(cooldown),
            severity=severity,
            enabled=not disabled,
            remind=remind,
            note=note,
        )
        saved = AlertRules().save(rule, actor="cli")
    except NoustError as exc:
        raise _fail(ctx, exc) from exc
    ctx.logger.success(
        f"Rule {saved.name}: {saved.metric} {_limit(saved)} for {format_duration_short(saved.duration_s)}"
        + ("" if saved.enabled else " (off)")
    )


@cli.command("ack")
@click.argument("name")
@click.option(
    "--app",
    "subject",
    default="",
    metavar="DOMAIN",
    help="The application the alert is about; leave out for the server's.",
)
@click.option(
    "--by",
    envvar="SUDO_USER",
    default="cli",
    show_default=True,
    help="Who is taking it, as the channels will say.",
)
@pass_context
def ack_command(ctx: Context, name: str, subject: str, by: str) -> None:
    """
    Take an alert that is firing: its reminders stop and the channels are told.

    A pager channel acknowledges the incident too, which stops it escalating. The
    alert still clears, and says so, when the metric is back inside its limit.

    Examples:

    \b
      noust alert ack cpu-high
      noust alert ack app-5xx --app shop.example.com --by alice
    """
    try:
        state = acknowledge_alert(AlertRules(), name, subject, by=by)
    except NoustError as exc:
        raise _fail(ctx, exc) from exc
    ctx.logger.success(
        f"{name}{f' for {subject}' if subject else ''} is taken by {state.acknowledged_by}."
    )


@cli.command("unack")
@click.argument("name")
@click.option(
    "--app",
    "subject",
    default="",
    metavar="DOMAIN",
    help="The application the alert is about; leave out for the server's.",
)
@pass_context
def unack_command(ctx: Context, name: str, subject: str) -> None:
    """Hand a firing alert back: its reminder speaks again."""
    try:
        AlertRules().unacknowledge(name, subject)
    except NoustError as exc:
        raise _fail(ctx, exc) from exc
    ctx.logger.success(f"{name}{f' for {subject}' if subject else ''} is handed back.")


@cli.command("remove")
@click.argument("name")
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """Remove a rule and what it remembered."""
    if not AlertRules().remove(name):
        ctx.logger.error(f"No alert rule named {name}")
        raise SystemExit(1)
    ctx.logger.success(f"Rule {name} removed.")


def _switch(ctx: Context, name: str, enabled: bool) -> None:
    """
    Args:
        ctx: The command's context.
        name: The rule.
        enabled: On or off.
    """
    rule: Any = AlertRules().set_enabled(name, enabled, actor="cli")
    if rule is None:
        ctx.logger.error(f"No alert rule named {name}")
        raise SystemExit(1)
    ctx.logger.success(f"Rule {name} is {'on' if rule.enabled else 'off'}.")


@cli.command("enable")
@click.argument("name")
@pass_context
def enable_command(ctx: Context, name: str) -> None:
    """Turn a rule on."""
    _switch(ctx, name, True)


@cli.command("disable")
@click.argument("name")
@pass_context
def disable_command(ctx: Context, name: str) -> None:
    """Turn a rule off; what it had decided is forgotten."""
    _switch(ctx, name, False)
