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
import sqlite3
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.notification_channels import ChannelAdmin, ChannelView, event_catalog
from noust.core.notification_ledger import STATES, DeliveryLedger
from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.grouping import as_topic_number
from noust.core.notifications.instances import TYPES
from noust.core.notifications.routing import QUIET_ALLOW, SEVERITIES
from noust.core.notifications.team_channels import TeamChannels
from noust.core.notifications.topic_notes import TopicNote, TopicNotes
from noust.core.notifier import Notifier, fresh_config
from noust.core.telegram_topics import TelegramTopics

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


def _topics(pairs: tuple[str, ...], clear: bool, existing: ChannelView | None) -> dict[str, Any]:
    """
    Work out the topic of each subject a command leaves a Telegram channel with.

    Args:
        pairs: ``SUBJECT=NUMBER`` strings; an empty number stops a subject having a topic.
        clear: Forget every topic first.
        existing: The channel as it is now, or None for a new one.

    Returns:
        The channel's ``topics`` after the change: what it had, less what was cleared, with what
        the command says. A subject that does not exist is the configuration's to refuse, in the
        same words as the console's.

    Raises:
        click.UsageError: When a number is not a whole positive one.
    """
    stored = existing.settings.get("topics") if existing is not None else None
    found: dict[str, Any] = {} if clear or not isinstance(stored, dict) else dict(stored)
    for subject, number in _kv(pairs, "--topic").items():
        text = number.strip()
        if not text:
            found.pop(subject, None)
        elif text.isdigit() and int(text) > 0:
            found[subject] = int(text)
        else:
            raise click.UsageError(
                f"--topic {subject}= must be a topic number (a whole number from 1), not {number!r}"
            )
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
    "--topic",
    "topics",
    multiple=True,
    metavar="SUBJECT=NUMBER",
    help="Telegram: post this subject (deploys, alerts, backups, security or system) in this topic "
    "of the forum group; an empty NUMBER stops it having one.",
)
@click.option("--clear-topics", is_flag=True, help="Telegram: forget every topic of a subject.")
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
    topics: tuple[str, ...],
    clear_topics: bool,
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
    if topics or clear_topics:
        values["topics"] = _topics(topics, clear_topics, existing)

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
        ["Event", "Subject", "On by default", "On now"],
        [
            [
                item["kind"],
                item["subject"] or "-",
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


#: What to do about each reason topics could not be created, in the words of the command line.
_TOPIC_FIXES = {
    "not_forum": (
        "The group does not have topics turned on. Only the group's owner can: open the group's "
        "settings in Telegram, choose Topics and switch them on. Then run this again."
    ),
    "not_member": ("The bot is not in the group. Add it as an administrator, then run this again."),
    "no_right": (
        "The bot may not manage topics. In the group, make the bot an administrator with the "
        "right to 'Manage topics', then run this again."
    ),
    "chat_unreachable": (
        "Telegram could not find the chat. Check the chat ID and that the bot is in the group."
    ),
    "unreachable": "Telegram could not be reached. Try again in a moment.",
    "refused": "Telegram refused to create a topic. The topics made so far are saved.",
}


def _telegram_channel(name: str, admin: ChannelAdmin) -> ChannelView:
    """
    Args:
        name: A channel's name.
        admin: The channel admin.

    Returns:
        The channel, when it is a Telegram one.

    Raises:
        NoustError: When there is no such channel, or it is not a Telegram one.
    """
    view = admin.get(name)
    if view is None:
        raise ValidationError(
            f"There is no notification channel named {name!r}",
            details="'noust notify list' shows the channels.",
        )
    if view.type != "telegram":
        raise ValidationError(
            f"{name!r} is not a Telegram channel", details="Only a Telegram group has topics."
        )
    return view


def _lost_topics(name: str, view: ChannelView) -> list[TopicNote]:
    """
    Args:
        name: The channel.
        view: The channel as it is shown.

    Returns:
        The topics it still posts in that stopped taking messages; empty when they cannot be read.
    """
    try:
        return TopicNotes(NotificationState()).standing(name, view.settings)
    except (sqlite3.Error, OSError, NoustError):
        return []


@cli.command("telegram-topics", read_only=lambda params: not params.get("create"))
@click.option(
    "--channel",
    default="telegram",
    show_default=True,
    help="The Telegram channel whose group is asked.",
)
@click.option(
    "--create",
    is_flag=True,
    help="Create in the group the topics the subjects are missing, with the channel's bot, and "
    "save their numbers.",
)
@click.option(
    "--recreate",
    is_flag=True,
    help="With --create, also make anew a topic that stopped taking messages.",
)
@global_flags
@json_option("Print the topics as JSON.")
@pass_context
def telegram_topics_command(ctx: Context, channel: str, create: bool, recreate: bool) -> None:
    """
    Show the topic of each subject, or create the ones that are missing.

    A Telegram group with topics turned on keeps conversations apart. Noust posts each subject
    (deploys, alerts, backups, security, system) in a topic of its own once the channel has one:
    set it with 'noust notify channel set telegram --topic deploys=12', or let Noust create the
    topics with --create. Creating asks Telegram, with the channel's bot, whether the group has
    topics and whether the bot may manage them, and says what to change when it does not; asking
    again makes only what is missing.

    Listing asks nothing of Telegram.
    """
    admin = ChannelAdmin()
    try:
        view = _telegram_channel(channel, admin)
        if create:
            _create_topics(ctx, channel, admin, recreate)
            return
    except NoustError as exc:
        ctx.logger.error(exc.message, details=exc.details)
        raise SystemExit(1) from exc

    stored = view.settings.get("topics")
    numbers = {
        subject: as_topic_number(stored.get(subject)) if isinstance(stored, dict) else None
        for subject in grouping.SUBJECTS
    }
    lost = {note.topic: note for note in _lost_topics(channel, view)}
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "channel": channel,
                    "subjects": [
                        {
                            "subject": subject,
                            "topic": numbers[subject],
                            "events": list(grouping.kinds_of(subject)),
                            "lost": numbers[subject] in lost,
                        }
                        for subject in grouping.SUBJECTS
                    ],
                    "notices": [
                        {"topic": n.topic, "subject": n.subject, "reason": n.reason}
                        for n in lost.values()
                    ],
                }
            )
        )
        return
    ctx.logger.table(
        ["Subject", "Topic", "Covers"],
        [
            [
                subject,
                (str(numbers[subject]) + (" (lost)" if numbers[subject] in lost else ""))
                if numbers[subject] is not None
                else "-",
                ", ".join(grouping.kinds_of(subject)),
            ]
            for subject in grouping.SUBJECTS
        ],
    )
    for note in lost.values():
        ctx.logger.warning(
            f"Topic {note.topic} stopped taking messages; what was meant for it goes to the "
            f"general thread. Telegram said: {note.reason}"
        )


def _create_topics(ctx: Context, channel: str, admin: ChannelAdmin, recreate: bool) -> None:
    """
    Create the topics a channel is missing and report what happened.

    Args:
        ctx: The command's context.
        channel: The Telegram channel.
        admin: The channel admin, whose configuration receives the numbers.
        recreate: Also make anew a topic that stopped taking messages.

    Raises:
        SystemExit: With 1 when it could not create every topic, or could not save them.
        NoustError: When the channel has no bot or chat yet.
    """
    if ctx.dry_run:
        ctx.logger.info(
            "Would ask Telegram whether the group has topics and whether the bot may manage "
            "them, then create the topics that are missing. Nothing was asked."
        )
        return
    result = TelegramTopics(Notifier(Config())).create(channel, admin, recreate=recreate)
    if result.created and not _save(ctx, admin):
        raise SystemExit(1)
    if ctx.json_output:
        click.echo(json.dumps({"channel": channel, **result.to_dict()}))
    else:
        for subject, number in result.created.items():
            ctx.logger.success(f"Created the topic for {subject}: {number}.")
        for subject in result.kept:
            ctx.logger.info(f"{subject} already has a topic.")
    if result.problem is not None:
        ctx.logger.error(_TOPIC_FIXES[result.problem], details=result.detail or "")
        raise SystemExit(1)
    if not ctx.json_output:
        ctx.logger.success(
            "Every subject has a topic."
            if result.created
            else "Nothing to create: every subject has a topic."
        )
