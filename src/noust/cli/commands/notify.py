# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust notify`` command group: notification channels, events and deliveries.

A thin front end over the same classes the console's settings page uses:
:class:`~noust.core.notification_channels.ChannelAdmin` to list and change
channels, :class:`~noust.core.notifier.Notifier` to try one and to send again
what failed, and :class:`~noust.core.notification_ledger.DeliveryLedger` to read
the record of deliveries. What a channel accepts is decided once, by the
configuration chokepoint, so a setting the console refuses is refused here in the
same words.

A credential is never typed as an argument (it would sit in the shell history and
in ``ps``): ``--secret KEY`` asks for it hidden and ``--secret-stdin KEY`` reads it
from a pipe.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.notification_channels import ChannelAdmin, ChannelView, event_catalog
from noust.core.notification_ledger import STATES, DeliveryLedger
from noust.core.notifications.instances import TYPES
from noust.core.notifications.routing import QUIET_ALLOW, SEVERITIES
from noust.core.notifications.team_channels import TeamChannels
from noust.core.notifier import Notifier, fresh_config

_ROUTE_LISTS = {
    "route_apps": "apps",
    "route_projects": "projects",
    "route_environments": "environments",
    "route_tags": "tags",
}


@click.group("notify", cls=NoustGroup)
def cli() -> None:
    """Notification channels: what Noust tells you, and where."""


def _save(ctx: Context, admin: ChannelAdmin) -> bool:
    """
    Write the configuration a command changed.

    Args:
        ctx: The command's context, for the logger.
        admin: The admin whose configuration was changed.

    Returns:
        Whether it was written; the failure is reported either way.
    """
    try:
        admin.config.write()
    except (OSError, NoustError) as exc:
        ctx.logger.error("Could not save the configuration", details=str(exc))
        return False
    return True


def _describe(view: ChannelView) -> str:
    """
    Args:
        view: A channel.

    Returns:
        One line of what narrows it: its route, quiet hours and event switches.
    """
    parts = []
    if view.route:
        parts.append("route " + ", ".join(f"{k}={v}" for k, v in view.route.items()))
    if view.quiet:
        parts.append(f"quiet {view.quiet['start']}-{view.quiet['end']} {view.quiet['timezone']}")
    if view.flood:
        parts.append(f"at most {view.flood['max']} per {view.flood['minutes']} min")
    if view.summary:
        parts.append(
            f"below {view.summary['below']} in a summary at {view.summary['at']} "
            f"{view.summary['timezone']}"
        )
    if view.events:
        parts.append(f"{len(view.events)} event switch(es)")
    return "; ".join(parts) or "everything"


@cli.command("list", read_only=True)
@global_flags
@json_option("Print the channels as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List every notification channel, the default of each type and your own."""
    views = ChannelAdmin().channels()
    if ctx.json_output:
        click.echo(json.dumps([view.to_dict() for view in views]))
        return
    ctx.logger.table(
        ["Name", "Type", "State", "Team", "Receives"],
        [
            [
                view.name,
                view.type,
                ("on" if view.enabled else "off") if view.configured else "not configured",
                view.team or "-",
                _describe(view) if view.configured else "-",
            ]
            for view in views
        ],
    )


@cli.command("test")
@click.argument("channel")
@pass_context
def test_command(ctx: Context, channel: str) -> None:
    """
    Send a test message through CHANNEL.

    CHANNEL is a channel's name as 'noust notify list' shows it. Ignores the
    master switch and the per-event filters, the same as the panel's own "Test"
    button: this exists to check a channel is configured correctly before
    notifications are turned on.
    """
    known = [view.name for view in ChannelAdmin().channels()]
    if channel not in known:
        raise click.BadParameter(
            f"{channel!r} is not a channel; the channels are: {', '.join(known)}",
            param_hint="CHANNEL",
        )
    error = Notifier(Config(), record=False).test_channel(channel)
    if error is not None:
        ctx.logger.error(f"Test message through {channel} failed", details=error)
        raise SystemExit(1)

    ctx.logger.success(f"Test message sent through {channel}.")


def _kv(pairs: tuple[str, ...], what: str) -> dict[str, str]:
    """
    Args:
        pairs: ``KEY=VALUE`` strings.
        what: What they are, for the error.

    Returns:
        The mapping.

    Raises:
        click.UsageError: When one has no ``=``.
    """
    found: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise click.UsageError(f"{what} must be KEY=VALUE, not {pair!r}")
        found[key.strip()] = value
    return found


def _secrets(secret_keys: tuple[str, ...], stdin_key: str | None) -> dict[str, str]:
    """
    Read the credentials a command was asked to set.

    Args:
        secret_keys: Keys to ask for, hidden, twice.
        stdin_key: The one key whose value is piped in.

    Returns:
        The values by key.
    """
    found: dict[str, str] = {}
    if stdin_key:
        raw = sys.stdin.read()
        found[stdin_key] = raw[:-1] if raw.endswith("\n") else raw
    for key in secret_keys:
        found[key] = click.prompt(key, hide_input=True, confirmation_prompt=True)
    return found


@cli.group("channel", cls=NoustGroup)
def channel_group() -> None:
    """Add, change and remove notification channels."""


@channel_group.command("set")
@click.argument("name")
@click.option("--type", "type_", type=click.Choice(list(TYPES)), help="The channel's type.")
@click.option("--enable/--disable", "enabled", default=None, help="Turn the channel on or off.")
@click.option(
    "--set",
    "settings",
    multiple=True,
    metavar="KEY=VALUE",
    help="A setting of the type (chat_id, device, mention_role_id...). Not a credential.",
)
@click.option(
    "--secret",
    "secret_keys",
    multiple=True,
    metavar="KEY",
    help="Ask for a credential (webhook_url, bot_token, user_key...), hidden.",
)
@click.option(
    "--secret-stdin",
    "stdin_key",
    metavar="KEY",
    help="Read one credential from standard input.",
)
@click.option(
    "--event",
    "events",
    multiple=True,
    metavar="KIND=on|off",
    help="This channel's own switch for an event (see 'noust notify events').",
)
@click.option("--clear-events", is_flag=True, help="Forget this channel's own event switches.")
@click.option(
    "--route-app",
    "route_apps",
    multiple=True,
    metavar="DOMAIN",
    help="Only this application (a * pattern works).",
)
@click.option(
    "--route-project", "route_projects", multiple=True, metavar="SLUG", help="Only this project."
)
@click.option(
    "--route-environment",
    "route_environments",
    multiple=True,
    metavar="SLUG",
    help="Only this environment (or project/environment).",
)
@click.option(
    "--route-tag",
    "route_tags",
    multiple=True,
    metavar="TAG",
    help="Only applications with this tag.",
)
@click.option(
    "--min-severity",
    type=click.Choice(SEVERITIES[1:]),
    help="Skip everything below this severity.",
)
@click.option(
    "--server-events/--no-server-events",
    default=None,
    help="With a route: also hear what is about the server, not an application.",
)
@click.option("--clear-route", is_flag=True, help="Remove the channel's route.")
@click.option(
    "--quiet", "quiet_window", metavar="START-END", help="Quiet hours, such as 22:00-07:00."
)
@click.option(
    "--quiet-timezone", metavar="ZONE", help="The time zone of the quiet hours (UTC by default)."
)
@click.option(
    "--quiet-allow",
    type=click.Choice(QUIET_ALLOW),
    help="The lowest severity that still gets through the quiet hours (failed by default).",
)
@click.option("--no-digest", is_flag=True, help="Deliver what the quiet hours held one by one.")
@click.option("--clear-quiet", is_flag=True, help="Remove the channel's quiet hours.")
@click.option(
    "--flood",
    "flood_cap",
    metavar="MAX/MINUTES",
    help="At most MAX messages in any MINUTES minutes, such as 20/10; the rest arrive as one.",
)
@click.option("--clear-flood", is_flag=True, help="Remove the channel's anti-flood cap.")
@click.option(
    "--summary-at",
    metavar="HH:MM",
    help="Send everything below --summary-below once a day at this time, as one message.",
)
@click.option("--summary-timezone", metavar="ZONE", help="The time zone of the summary.")
@click.option(
    "--summary-below",
    type=click.Choice(SEVERITIES[1:]),
    help="What waits for the summary: what is below this severity (warning by default).",
)
@click.option(
    "--summary-days",
    metavar="DAYS",
    help="The days it goes out on, such as mon,wed,fri (every day by default).",
)
@click.option("--clear-summary", is_flag=True, help="Remove the channel's summary.")
@click.option(
    "--team",
    metavar="TEAM",
    default=None,
    help="Make it the channel of this team: it hears only that team's applications. "
    "NAME is then what the team calls it.",
)
@pass_context
def channel_set(
    ctx: Context,
    name: str,
    type_: str | None,
    enabled: bool | None,
    settings: tuple[str, ...],
    secret_keys: tuple[str, ...],
    stdin_key: str | None,
    events: tuple[str, ...],
    clear_events: bool,
    min_severity: str | None,
    server_events: bool | None,
    clear_route: bool,
    quiet_window: str | None,
    quiet_timezone: str | None,
    quiet_allow: str | None,
    no_digest: bool,
    clear_quiet: bool,
    flood_cap: str | None,
    clear_flood: bool,
    summary_at: str | None,
    summary_timezone: str | None,
    summary_below: str | None,
    summary_days: str | None,
    clear_summary: bool,
    team: str | None,
    **route_lists: tuple[str, ...],
) -> None:
    """
    Create or change a channel, keeping what is not mentioned.

    Examples:

    \b
      noust notify channel set slack-staging --type slack --secret webhook_url
      noust notify channel set oncall --type pushover --secret user_key --secret app_token
      noust notify channel set oncall --min-severity failed --quiet 22:00-07:00
      noust notify channel set oncall --flood 20/10
      noust notify channel set slack-ops --summary-at 08:00 --summary-timezone Europe/Madrid
      noust notify channel set slack-shop --route-project shop --event deploy_started=on
    """
    admin = ChannelAdmin()
    display = name
    owner = TeamChannels(team, admin) if team else None
    if owner is not None:
        try:
            name = owner.key_of(display)
        except NoustError as exc:
            ctx.logger.error(exc.message, details=exc.details)
            raise SystemExit(1) from exc
    existing = admin.get(name)
    values: dict[str, Any] = dict(_kv(settings, "--set"))
    kind = type_ or (existing.type if existing else (name if name in TYPES else None))
    credentials = {spec.key for spec in TYPES[kind].fields if spec.secret} if kind else set()
    typed_secret = sorted(set(values) & credentials)
    if typed_secret:
        raise click.UsageError(
            f"{typed_secret[0]} is a credential: use --secret {typed_secret[0]} "
            "(asked hidden) or --secret-stdin, never an argument"
        )
    values.update(_secrets(secret_keys, stdin_key))

    switches: dict[str, bool] | None = None
    if events or clear_events:
        switches = {} if clear_events else dict(existing.events if existing else {})
        for key, setting in _kv(events, "--event").items():
            if setting.lower() not in ("on", "off", "true", "false"):
                raise click.UsageError(f"--event {key}= must be on or off, not {setting!r}")
            switches[key] = setting.lower() in ("on", "true")

    route: dict[str, Any] | None = None
    wants_route = any(route_lists.values()) or min_severity or server_events is not None
    if clear_route:
        route = {}
    elif wants_route:
        route = dict(existing.route if existing else {})
        for option, key in _ROUTE_LISTS.items():
            if route_lists.get(option):
                route[key] = list(route_lists[option])
        if min_severity:
            route["min_severity"] = min_severity
        if server_events is not None:
            route["server_events"] = server_events

    quiet: dict[str, Any] | None = None
    if clear_quiet:
        quiet = {}
    elif quiet_window or quiet_timezone or quiet_allow or no_digest:
        quiet = dict(existing.quiet or {}) if existing else {}
        if quiet_window:
            start, separator, end = quiet_window.partition("-")
            if not separator:
                raise click.UsageError("--quiet must be START-END, such as 22:00-07:00")
            quiet.update(start=start.strip(), end=end.strip())
        if quiet_timezone:
            quiet["timezone"] = quiet_timezone
        if quiet_allow:
            quiet["allow"] = quiet_allow
        if no_digest:
            quiet["digest"] = False

    flood: dict[str, Any] | None = None
    if clear_flood:
        flood = {}
    elif flood_cap:
        maximum, separator, minutes = flood_cap.partition("/")
        if not separator or not maximum.strip().isdigit() or not minutes.strip().isdigit():
            raise click.UsageError("--flood must be MAX/MINUTES, such as 20/10")
        flood = {"max": int(maximum), "minutes": int(minutes)}

    summary: dict[str, Any] | None = None
    if clear_summary:
        summary = {}
    elif summary_at or summary_timezone or summary_below or summary_days:
        summary = dict(existing.summary or {}) if existing else {}
        if summary_at:
            summary["at"] = summary_at
        if summary_timezone:
            summary["timezone"] = summary_timezone
        if summary_below:
            summary["below"] = summary_below
        if summary_days:
            summary["days"] = [day.strip() for day in summary_days.split(",") if day.strip()]
        if "at" not in summary:
            raise click.UsageError("a summary needs a time: add --summary-at HH:MM")

    try:
        if owner is not None:
            view = owner.save(
                display,
                type=type_,
                enabled=enabled,
                settings=values or None,
                events=switches,
                route=route,
                quiet=quiet,
                flood=flood,
                summary=summary,
            )
        else:
            view = admin.save(
                name,
                type=type_,
                enabled=enabled,
                settings=values or None,
                events=switches,
                route=route,
                quiet=quiet,
                flood=flood,
                summary=summary,
            )
    except NoustError as exc:
        ctx.logger.error(exc.message, details=exc.details)
        raise SystemExit(1) from exc
    if not _save(ctx, admin):
        raise SystemExit(1)
    state = "configured" if view.configured else "saved, but it still needs its credentials"
    ctx.logger.success(f"Channel {view.name} ({view.type}) {state}.")


@channel_group.command("remove")
@click.argument("name")
@click.option("--team", metavar="TEAM", default=None, help="Remove this team's channel NAME.")
@pass_context
def channel_remove(ctx: Context, name: str, team: str | None) -> None:
    """Remove a channel. A default channel is emptied instead."""
    admin = ChannelAdmin()
    try:
        removed = TeamChannels(team, admin).remove(name) if team else admin.remove(name)
    except NoustError as exc:
        ctx.logger.error(exc.message, details=exc.details)
        raise SystemExit(1) from exc
    if not removed:
        ctx.logger.warning(f"There is nothing to remove under the name {name}.")
        return
    if not _save(ctx, admin):
        raise SystemExit(1)
    ctx.logger.success(f"Channel {name} removed.")


@cli.command("events", read_only=True)
@global_flags
@json_option("Print the events as JSON.")
@pass_context
def events_command(ctx: Context) -> None:
    """List the events a channel may be switched for, and which are on."""
    items = event_catalog(Config())
    if ctx.json_output:
        click.echo(json.dumps(items))
        return
    ctx.logger.table(
        ["Event", "On by default", "On now"],
        [
            [
                item["kind"],
                "yes" if item["default_on"] else "no",
                "yes" if item["enabled"] else "no",
            ]
            for item in items
        ],
    )


@cli.command("deliveries", read_only=True)
@click.option("--state", type=click.Choice(STATES), help="Only deliveries in this state.")
@click.option("--channel", help="Only deliveries to this channel.")
@click.option("--limit", type=click.IntRange(1, 500), default=30, show_default=True)
@global_flags
@json_option("Print the deliveries as JSON.")
@pass_context
def deliveries_command(ctx: Context, state: str | None, channel: str | None, limit: int) -> None:
    """Show the record of deliveries: what went out, what waits and what failed."""
    ledger = DeliveryLedger()
    items = ledger.recent(limit=limit, state=state, channel=channel)
    if ctx.json_output:
        click.echo(
            json.dumps({"items": [item.to_dict() for item in items], "counts": ledger.counts()})
        )
        return
    if not items:
        ctx.logger.info("No deliveries on record.")
        return
    ctx.logger.table(
        ["Id", "Channel", "Event", "About", "State", "Tries", "Last error"],
        [
            [
                str(item.id),
                item.channel,
                item.kind,
                item.subject[:30],
                f"{item.state} ({item.held_for})"
                if item.state == "held" and item.held_for
                else item.state,
                str(item.attempts),
                (item.last_error or "")[:60],
            ]
            for item in items
        ],
    )


@cli.command("retry")
@click.argument("delivery_id", type=int, required=False)
@pass_context
def retry_command(ctx: Context, delivery_id: int | None) -> None:
    """
    Send again what failed, now.

    With DELIVERY_ID, that delivery (a failed one is given a fresh set of
    attempts). Without, whatever is due: retries whose wait is over and
    notifications the quiet hours held. The monitor does the second on every
    scan; this is for whoever does not want to wait.
    """
    ledger = DeliveryLedger()
    if delivery_id is not None and ledger.retry_now(delivery_id) is None:
        ctx.logger.error(
            f"Delivery {delivery_id} cannot be sent again",
            details="It does not exist, or it went out and kept nothing to send.",
        )
        raise SystemExit(1)
    report = Notifier(fresh_config()).deliver_due()
    ctx.logger.success(
        f"{report.delivered} delivered, {report.retried} waiting, {report.failed} given up"
        + (f", {report.digests} digest(s)" if report.digests else "")
        + "."
    )


@cli.command("telegram-chats", read_only=True)
@click.option(
    "--channel",
    default="telegram",
    show_default=True,
    help="The Telegram channel whose bot is asked.",
)
@global_flags
@json_option("Print the chats as a JSON array.")
@pass_context
def telegram_chats_command(ctx: Context, channel: str) -> None:
    """
    List the chats the Telegram bot can send to, with their ids.

    Send the bot a message, or add it to the group or channel, then run this.
    The first column is the chat id: set it with 'noust config set
    notifications.channels.telegram.chat_id ID' (or 'noust notify channel set
    telegram --set chat_id=ID'). Group and channel ids start with a minus sign,
    and it is part of the id. A chat shows up only after it has sent the bot
    something recently, so if the one you want is missing, send the bot another
    message and run this again. A forum group's topics are numbered in the
    group itself: set 'thread_id' to post in one.

    Needs the channel's bot_token to be set first.
    """
    try:
        notifier = Notifier(Config())
        chats = (
            notifier.list_telegram_chats()
            if channel == "telegram"
            else notifier.list_telegram_chats(channel)
        )
    except (OSError, ValueError) as exc:
        ctx.logger.error("Could not list Telegram chats", details=str(exc))
        raise SystemExit(1) from exc

    if ctx.json_output:
        click.echo(json.dumps([dataclasses.asdict(chat) for chat in chats]))
        return

    if not chats:
        ctx.logger.warning(
            "No chats yet. Send the bot a message, or add it to the group or channel, "
            "then run this again."
        )
        return

    for chat in chats:
        label = chat.title or (f"@{chat.username}" if chat.username else "(no name)")
        ctx.logger.info(f"{chat.id}\t{chat.type}\t{label}")
