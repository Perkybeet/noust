# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust watches by itself, and where what it finds goes (item 159).

"Tell me if it goes down" was already there and on (an application that stops answering, a
certificate that is not renewed, a disk that fills, a backup that did not run) and nothing said so:
the answer was split between Alerts, Probes and the channels, and an operator who wanted to know
what would reach their phone had to read all three. :data:`WATCHES` is the one definition of what is
watched without being asked, each with the messages it can say and the setting that turns its check
off, and :func:`report` reads the configuration against it: which watches are on, which are off and
worth offering, and which channels would take what each of them says. The console asks the API for
this and keeps no list of its own, so a watch added here appears there.

Nothing here looks at the machine. The one thing that does is the question of whether the monitor,
which does the watching, is running: it is asked through an injected probe, like the rest of the
monitor's reads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.config import Config
from noust.core.notifications.instances import ChannelInstance, resolve_instances
from noust.core.notifications.model import OFF_BY_DEFAULT
from noust.core.notifications.pause import parse_pause

#: What the monitor's own unit says about itself: ``installed``, ``enabled`` and ``active``, or None
#: when systemd cannot be asked (a container, a machine without it).
WatcherProbe = Callable[[], Mapping[str, Any] | None]

#: The command that starts the monitor, and where to look when it is enabled and not running.
ENABLE_MONITOR = "noust monitor enable"
LOOK_AT_MONITOR = "journalctl -u noust-monitor -n 50"


@dataclass(frozen=True)
class Watch:
    """
    One thing Noust looks at without being asked.

    Attributes:
        id: Stable; the console words it, so a new one needs its words there.
        group: ``applications``, ``domains``, ``server``, ``backups`` or ``noust``.
        events: The kinds of notification it can say, from
            :data:`noust.core.notifications.model.EVENT_KINDS`.
        off_key: The setting whose value 0 (or false) turns the check off; None for a watch with no
            switch of its own (only the messages can be switched off).
        offer: The setting and the value that turn on a watch that ships off and is worth offering.
        asks_outside: It sends a request to somebody else (docs/PRIVACY.md); said wherever it is
            offered.
        params: The settings whose values the console's sentence quotes, by the name it uses.
    """

    id: str
    group: str
    events: tuple[str, ...]
    off_key: str | None = None
    offer: tuple[str, Any] | None = None
    asks_outside: bool = False
    params: Mapping[str, str] = field(default_factory=dict)


#: What is watched, in the order the console draws it.
WATCHES: tuple[Watch, ...] = (
    Watch(
        "apps_down",
        "applications",
        (
            "app_unreachable",
            "app_recovered",
            "unit_failed",
            "container_restart_loop",
            "app_oom_killed",
        ),
    ),
    Watch("deploys", "applications", ("deploy_failed", "deploy_rolled_back", "deploy_hook_failed")),
    Watch(
        "certificates",
        "domains",
        ("cert_expiring", "cert_renewal_failed"),
        params={"days": "monitor.cert_warning_days"},
    ),
    Watch(
        "domain_expiry",
        "domains",
        ("domain_expiring", "domain_expired"),
        off_key="monitor.domain_expiry",
        offer=("monitor.domain_expiry", True),
        asks_outside=True,
    ),
    Watch("disk", "server", ("disk_threshold",), params={"percent": "monitor.disk_threshold"}),
    Watch(
        "updates",
        "server",
        ("os_patches",),
        off_key="monitor.os_patches_check_hours",
        params={"hours": "monitor.os_patches_check_hours"},
    ),
    Watch("access_changes", "server", ("access_change_reverted",)),
    Watch("backups", "backups", ("backup_failed", "destination_unreachable", "restore_failed")),
    Watch(
        "backups_missing",
        "backups",
        ("backup_missing",),
        off_key="monitor.backup_missing_days",
        params={"days": "monitor.backup_missing_days"},
    ),
    Watch(
        "tokens",
        "noust",
        ("token_expiring",),
        off_key="monitor.token_warning_days",
        params={"days": "monitor.token_warning_days"},
    ),
    Watch("releases", "noust", ("security_release",), off_key="monitor.security_check_hours"),
    Watch("recipes", "noust", ("recipe_outdated",), off_key="monitor.recipe_check_hours"),
)

#: Every other kind of notification, and why it is not a watch: a test holds this and
#: :data:`WATCHES` to the notifier's whole list, so a kind added there is ruled on here.
NOT_A_WATCH: Mapping[str, str] = {
    "deploy_started": "says an action as it begins; nothing is looked at",
    "deploy_success": "the outcome of something somebody did",
    "restore_success": "the outcome of something somebody did",
    "backup_success": "a heartbeat of a backup that ran, off unless asked for",
    "node_unreachable": "a central's watch over the servers it manages, shown in the fleet",
    "node_recovered": "a central's watch over the servers it manages, shown in the fleet",
    "node_host_key_changed": "a central's watch over the servers it manages, shown in the fleet",
    "central_silent": "a server's word about the central that manages it",
    "server_rebooted": "said when the server starts again, not a check",
    "server_back": "said when the server starts again, not a check",
    "approval_requested": "a person's request, not a check",
    "approval_decided": "a person's decision, not a check",
    "alert_firing": "the operator's own rules, listed on their own",
    "alert_resolved": "the operator's own rules, listed on their own",
    "alert_acknowledged": "the operator's own rules, listed on their own",
    "probe_failed": "the operator's own probes, listed on their own",
    "probe_recovered": "the operator's own probes, listed on their own",
    "cron_failed": "the operator's own scheduled tasks",
    "cron_success": "the operator's own scheduled tasks",
    "cron_missed": "the operator's own scheduled tasks",
    "cleanup_success": "the disk cleanup the operator scheduled",
    "cleanup_failed": "the disk cleanup the operator scheduled",
    "test": "the button that sends a test",
    "report": "the process report, which has its own switch",
    "digest": "what a channel's quiet hours held, sent when they end",
}


def report(
    config: Config,
    *,
    watcher: WatcherProbe | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Say what is watched and who would hear what it finds.

    Args:
        config: The configuration, as it stands.
        watcher: Asks systemd about the monitor's unit; the real probe when None.
        now: The current moment, for a pause that has ended.

    Returns:
        ``watcher`` (whether the monitor that watches runs, and the command that fixes it when
        it does not), ``notifications`` (whether they are on, a pause and the server's channels
        that can deliver) and ``watches``: each with its state (``on``, ``off`` for a check turned
        off, ``muted`` when none of its messages is switched on anywhere), its messages, the
        numbers its sentence quotes, what turning it on would write when it is offered and
        the channels that would take what it says.
    """
    moment = now or datetime.now(timezone.utc)
    switches = config.get("notifications.events") or {}
    channels = _delivering(config)
    heard = {channel.name: channel for channel in channels}
    enabled = bool(config.get("notifications.enabled", False))
    until = parse_pause(config.get("notifications.muted_until"))
    return {
        "watcher": _watcher(watcher),
        "notifications": {
            "enabled": enabled,
            "paused_until": until.isoformat() if until is not None and moment < until else None,
            "channels": [{"name": channel.name, "type": channel.type} for channel in channels],
        },
        "watches": [
            _describe(watch, config, switches, heard if enabled else {}) for watch in WATCHES
        ],
    }


def _delivering(config: Config) -> list[ChannelInstance]:
    """
    Args:
        config: The configuration.

    Returns:
        The server's own channels that are on and have what their type needs: a team's channel
        hears about the team's applications and is the team's to read.
    """
    found = resolve_instances(config.get("notifications.channels") or {})
    return [
        channel
        for channel in found
        if channel.enabled and channel.configured and channel.routing.team is None
    ]


def _hears(channel: ChannelInstance, kind: str, switches: Mapping[str, Any]) -> bool:
    """
    Args:
        channel: A channel.
        kind: A kind of notification.
        switches: ``notifications.events``.

    Returns:
        Whether the channel takes the kind: its own switch when it has one, the server's otherwise
        (the notifier's own rule, :func:`noust.core.notifications.routing.decide`).
    """
    own = channel.routing.events.get(kind)
    if own is not None:
        return bool(own)
    return bool(switches.get(kind, kind not in OFF_BY_DEFAULT))


def _limited(channel: ChannelInstance) -> bool:
    """
    Args:
        channel: A channel.

    Returns:
        Whether it does not say everything at once: a route that narrows it, a floor of severity,
        quiet hours or a summary.
    """
    routing = channel.routing
    route = routing.route
    return (
        route.narrows
        or bool(route.min_severity)
        or routing.quiet is not None
        or routing.summary is not None
    )


def _number(value: Any) -> Any:
    """
    Args:
        value: A setting.

    Returns:
        The value, an integer when it is a whole number (a threshold stored as 90.0 is 90).
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _describe(
    watch: Watch,
    config: Config,
    switches: Mapping[str, Any],
    heard: Mapping[str, ChannelInstance],
) -> dict[str, Any]:
    """
    Args:
        watch: One watch.
        config: The configuration.
        switches: ``notifications.events``.
        heard: The channels that are delivering, by name; empty when notifications are off.

    Returns:
        The watch as the API says it.
    """
    events = [
        {"kind": kind, "on": bool(switches.get(kind, kind not in OFF_BY_DEFAULT))}
        for kind in watch.events
    ]
    reach = [
        {"name": channel.name, "type": channel.type, "limited": _limited(channel)}
        for channel in heard.values()
        if any(_hears(channel, kind, switches) for kind in watch.events)
    ]
    off = watch.off_key is not None and not config.get(watch.off_key)
    if off:
        state = "off"
    elif not reach and not any(event["on"] for event in events):
        state = "muted"
    else:
        state = "on"
    offer = None
    if off and watch.offer is not None:
        offer = {"key": watch.offer[0], "value": watch.offer[1]}
    return {
        "id": watch.id,
        "group": watch.group,
        "state": state,
        "events": events,
        "params": {name: _number(config.get(key)) for name, key in watch.params.items()},
        "reach": reach,
        "offer": offer,
        "asks_outside": watch.asks_outside,
    }


def _watcher(probe: WatcherProbe | None) -> dict[str, Any]:
    """
    Args:
        probe: Asks systemd about the monitor's unit; the real one when None.

    Returns:
        ``state`` (``running``, ``not_running``, ``disabled``, ``not_installed`` or ``unknown``)
        and ``fix``, the command that deals with it, or None when there is nothing to do.
    """
    if probe is None:
        from noust.monitor.collector import monitor_service_probe

        probe = monitor_service_probe
    unit = probe()
    if unit is None:
        return {"state": "unknown", "fix": None}
    if not unit.get("installed"):
        return {"state": "not_installed", "fix": ENABLE_MONITOR}
    if not unit.get("active"):
        if not unit.get("enabled"):
            return {"state": "disabled", "fix": ENABLE_MONITOR}
        return {"state": "not_running", "fix": LOOK_AT_MONITOR}
    return {"state": "running", "fix": None}
