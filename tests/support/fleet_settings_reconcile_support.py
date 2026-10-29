# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the tests of the reconciler share: a setting the central wants and one a node applied."""

from __future__ import annotations

from typing import Any

from noust.fleet.settings.desired import DesiredSetting


def wanted(
    name: str, version: int, resource: str = "channel", scope: str = "fleet"
) -> DesiredSetting:
    return DesiredSetting(
        resource=resource,
        name=name,
        scope=scope,
        version=version,
        value={},
        secret_paths=(),
        change_id="c0123456789ab",
        changed_by="ana",
        changed_at="t",
    )


def applied(
    name: str,
    version: int,
    state: str = "managed",
    resource: str = "channel",
    central: str = "nas",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "resource": resource,
        "name": name,
        "version": version,
        "state": state,
        "central": central,
        "restore_requested_at": None,
        **extra,
    }


def steps(plan: list[Any]) -> list[tuple[str, str, str, str]]:
    return [(s.resource, s.name, s.action, s.reason) for s in plan]
