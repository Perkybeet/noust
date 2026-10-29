# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the tests of "apply to the fleet" share: the setting they push and how they read a job."""

from __future__ import annotations

from typing import Any

from noust.fleet.settings.desired import FleetSettings
from noust.web.fleet_jobs import FleetRequest
from tests.support.fleet_actions import _run
from tests.support.fleet_views_support import Central

HELD = frozenset({"settings.manage", "server.manage", "backups.manage", "apps.manage"})
CHANNEL: dict[str, Any] = {
    "type": "telegram",
    "enabled": True,
    "settings": {"chat_id": "-100", "token": "bot:s3cret"},
}


def save(settings: FleetSettings, name: str = "ops", resource: str = "channel", **kw: Any) -> Any:
    return settings.save(
        resource,
        name,
        kw.pop("value", CHANNEL),
        scope=kw.pop("scope", "fleet"),
        person=kw.pop("person", "carol"),
        held=HELD,
    )


def apply(central: Central, *names: str, **options: Any) -> dict[str, Any]:
    request = FleetRequest("settings_apply", nodes=list(names) or ["web-1", "web-2", "web-3"])
    request.options = options
    snapshot, _recorder, _job = _run(central, request)
    return snapshot


def node_items(snapshot: dict[str, Any], node: str) -> list[dict[str, Any]]:
    return next(n for n in snapshot["nodes"] if n["node"] == node)["items"]


def states(snapshot: dict[str, Any], node: str) -> list[tuple[str, str, str]]:
    return [(i["resource"], i["name"], i["state"]) for i in node_items(snapshot, node)]
