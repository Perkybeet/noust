# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server needs, given what the central wants and what the server says it applied.

Pure: it asks nobody. The reconciler (:mod:`noust.web.fleet_settings_actions`) reads the server's
managed settings, calls :func:`plan_node` and then does the steps one by one through the
server's own typed routes. The rules:

- A setting the server lacks, or holds at another version than the one it should (also when the
  difference is an exception the central added or took away: a version names one row of one
  scope), is **pushed**.
- A setting at the same version is **left**.
- A setting the server's own operator changed (``overridden``) is **left**: the server rules on
  its own machine. It is pushed again only when the person applying says so, or when the server's
  operator asked the central to put it back.
- A setting the server has as the fleet's that the central no longer wants - it was removed, or the
  server was made independent - is **let go**: the server stops treating it as the fleet's and
  keeps it as its own. Nothing a setting put on a server is ever deleted from it.
- A setting whose route cannot match anything on the server (:mod:`noust.fleet.settings.reach`) is
  **held back**: nothing is pushed, so a credential does not reach a machine that could never use
  it, and what the server already holds of it is left exactly as it is. When the route can match
  again, the version decides as always. If the applications that decide it could not be read, it
  is held back too, and says so.
- What another central manages is not this one's to touch.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.fleet.settings.desired import DesiredSetting

__all__ = ["Step", "plan_node"]


@dataclass(frozen=True)
class Step:
    """
    One thing to do about one setting on one server.

    Attributes:
        resource: The kind.
        name: The setting.
        action: ``apply`` (push it), ``keep`` (leave it) or ``release`` (let go of it).
        reason: Why: for ``apply`` ``new``, ``outdated`` or ``restore``; for ``keep``
            ``in_sync``, ``overridden``, ``out_of_reach`` or ``reach_unknown``; for ``release``
            ``removed`` or ``independent``.
        setting: What the server should have, for ``apply`` and ``keep``.
        row: What the server says it applied, when it did.
        why: For ``out_of_reach``, what cannot match on the server: ``server``, ``domain`` or
            ``tag`` (:data:`noust.fleet.settings.reach.REASONS`).
    """

    resource: str
    name: str
    action: str
    reason: str
    setting: DesiredSetting | None = None
    row: Mapping[str, Any] | None = None
    why: str | None = None


def plan_node(
    desired: Sequence[DesiredSetting],
    managed: Sequence[Mapping[str, Any]],
    *,
    restore: Collection[str] = (),
    restore_all: bool = False,
    independent: bool = False,
    central: str | None = None,
    out_of_reach: Mapping[tuple[str, str], str] | None = None,
    reach_unknown: Collection[tuple[str, str]] = (),
) -> list[Step]:
    """
    Work out the steps for one server.

    Args:
        desired: What the server should have: :meth:`FleetSettings.effective_for`.
        managed: What it says it applied: the items of ``GET /api/managed-settings``.
        restore: ``kind/name`` of overridden settings to put back.
        restore_all: Put back every overridden setting.
        independent: The server follows none of it: everything it manages is let go.
        central: This central, as the server knows it; the rows of another central are ignored.
            All the rows when None.
        out_of_reach: ``(kind, name)`` of the settings whose route cannot match anything on this
            server, with what cannot (:func:`noust.fleet.settings.reach.explain`).
        reach_unknown: ``(kind, name)`` of the settings whose reach depends on applications that
            could not be read.

    Returns:
        The steps, by kind and name.
    """
    rows = {
        (str(row["resource"]), str(row["name"])): row
        for row in managed
        if central is None or row.get("central") == central
    }
    steps: dict[tuple[str, str], Step] = {}
    for setting in [] if independent else desired:
        key = (setting.resource, setting.name)
        row = rows.pop(key, None)
        if out_of_reach is not None and key in out_of_reach:
            steps[key] = Step(*key, "keep", "out_of_reach", setting, row, why=out_of_reach[key])
        elif key in reach_unknown:
            steps[key] = Step(*key, "keep", "reach_unknown", setting, row)
        else:
            steps[key] = _step_for(setting, row, restore, restore_all)
    for key, row in rows.items():
        steps[key] = Step(
            key[0], key[1], "release", "independent" if independent else "removed", None, row
        )
    return [steps[key] for key in sorted(steps)]


def _step_for(
    setting: DesiredSetting,
    row: Mapping[str, Any] | None,
    restore: Collection[str],
    restore_all: bool,
) -> Step:
    """
    Args:
        setting: What the server should have.
        row: What it says it applied of it, if it did.
        restore: ``kind/name`` of settings to put back.
        restore_all: Put back every overridden one.

    Returns:
        The step.
    """
    resource, name = setting.resource, setting.name
    if row is None:
        return Step(resource, name, "apply", "new", setting, None)
    if row.get("state") == "overridden":
        asked = bool(row.get("restore_requested_at"))
        if restore_all or asked or f"{resource}/{name}" in restore:
            return Step(resource, name, "apply", "restore", setting, row)
        return Step(resource, name, "keep", "overridden", setting, row)
    if int(row.get("version", 0)) == setting.version:
        return Step(resource, name, "keep", "in_sync", setting, row)
    return Step(resource, name, "apply", "outdated", setting, row)
