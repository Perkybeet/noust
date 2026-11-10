# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where each server stands on each setting of the fleet, for the screen that shows it.

Every server is asked what it manages (``GET /api/managed-settings``, through the aggregator, so
the answers are cached for a few seconds, parallel and partial like every other fleet view) and
the answer is compared with what the central wants. Two facts are told apart, because the screen
draws them apart:

- **Where the value comes from** (``origin``): ``fleet`` (the server follows the fleet's value),
  ``central_exception`` (a person set a value for this server on the central), ``local_exception``
  (the server's own operator changed it, and the server rules on its own machine) or
  ``independent`` (the server is left out of the fleet's settings).
- **Whether the server holds it** (``sync``): ``in_sync``, ``pending`` (not yet, or an older
  version), ``diverged`` (its operator changed it), ``release_pending`` (it is no longer the
  fleet's and the server still treats it as such), ``not_applicable``, or why the server could not
  be read or cannot take it: ``unreachable``, ``unsupported`` (an older Noust, or one that lacks
  this kind of setting), ``refused`` (the server will not answer this central, or its published
  ceiling does not let it set this kind) or ``unknown``.

A third fact is what is still to be done (``waiting``): the central holds a version for the server
that it has not seen the server hold. It is told apart from ``sync`` because ``unreachable`` says
why a server cannot be read and must not hide that a change is waiting for it: a server that
cannot be read is waiting when the setting changed after the last time it answered, since nothing
could have been pushed in between.

A fourth belongs to the setting and not to a server: how many of the servers it is meant for follow
it (``following`` of ``of``). Following is holding the fleet's value or being behind it; an
exception, a change of the server's own, independence, a refusal and an older Noust are not. The
central is never counted, and neither is a server a channel's route cannot match on. The count is
made here and nowhere else, so that the console does not make it again.

A fifth is what a server left out of the fleet's settings would be given if it followed them again
(``if_followed``): the ``sync`` the cell would have, worked out the way it is for a server that
does follow, so that the operator is told what will change before it does.

A channel with a route has one more fact: whether the route can match anything on the
server (``reach``, :mod:`noust.fleet.settings.reach`). A server it cannot match on is not behind;
it is ``not_applicable``, and ``reach.why`` says what cannot match. What it still holds of the
setting is shown as it is.

Nothing secret is read from a server: the state is the version it applied and what it says of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.fleet.aggregate import (
    RESOURCES,
    Asker,
    FleetResult,
    NodeOutcome,
    NodeView,
    Part,
    Resource,
    gather,
)
from noust.fleet.policy import FleetAccess, permits, validate_access_level
from noust.fleet.settings.desired import DesiredSetting, FleetSettings
from noust.fleet.settings.kinds import KINDS
from noust.fleet.settings.reach import Reach, explain, needs_apps
from noust.fleet.settings.routes import MANAGED_PREFIX

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager

__all__ = ["ORIGINS", "SYNC_STATES", "compose", "reach_of", "read"]

#: Where a server's value comes from.
ORIGINS = ("fleet", "central_exception", "local_exception", "independent", "none")

#: Whether a server holds it.
SYNC_STATES = (
    "in_sync",
    "pending",
    "diverged",
    "release_pending",
    "not_applicable",
    "unreachable",
    "unsupported",
    "refused",
    "unknown",
)

#: How a server that could not be read stands, by how it answered.
_UNREADABLE = {
    "unreachable": "unreachable",
    "stale": "unreachable",
    "unsupported": "unsupported",
    "forbidden": "refused",
    "error": "unknown",
}


def _build(view: NodeView) -> list[dict[str, Any]]:
    """
    One row per setting a server says it manages, and one that says which kinds it can manage.

    The second is not a setting: it has no ``resource`` and carries the server's own ``kinds``
    (``GET /api/managed-settings``), which is how a central tells a server that is behind from one
    that runs a Noust without a kind of setting.

    Args:
        view: The server.

    Returns:
        The rows, each labelled with the server.
    """
    answer = view.parts.get("managed")
    items = answer.get("items") if isinstance(answer, dict) else None
    rows = [
        {**item, "node": view.name}
        for item in items or []
        if isinstance(item, dict) and "resource" in item and "name" in item
    ]
    kinds = answer.get("kinds") if isinstance(answer, dict) else None
    if isinstance(kinds, list):
        rows.append({"node": view.name, "kinds": [str(kind) for kind in kinds]})
    return rows


#: What each server manages, as a fleet view. Not cached for long and never served stale: the
#: screen that shows it must not say a server is in sync because it was a minute ago.
MANAGED_VIEW = Resource(
    "managed_settings",
    (Part("managed", MANAGED_PREFIX, title="Managed settings"),),
    ttl=5.0,
    max_stale=0.0,
    build=_build,
)


def _register() -> None:
    """Add the view to the aggregator's, once."""
    RESOURCES.setdefault(MANAGED_VIEW.name, MANAGED_VIEW)


def reach_of(
    settings: FleetSettings,
    nodes: Sequence[str],
    apps: Mapping[str, Sequence[Mapping[str, Any]] | None] | None,
) -> dict[tuple[str, str, str], Reach]:
    """
    Say where each setting can match something, for each server.

    Args:
        settings: What the central wants.
        nodes: The servers.
        apps: The applications each server lists; None for a server that did not say, and for
            all when they were not asked (nothing depends on them).

    Returns:
        ``(server, kind, name)`` to :class:`Reach`, for what each server should have, also one
        that is independent: the screen says what it would be given if it followed again.
    """
    found: dict[tuple[str, str, str], Reach] = {}
    for node in nodes:
        listed = apps.get(node) if apps is not None else None
        for setting in settings.effective_for(node, ignore_mode=True):
            found[(node, setting.resource, setting.name)] = explain(
                setting.resource, setting.value, node, listed
            )
    return found


@dataclass(frozen=True)
class _Server:
    """
    What the central knows of one server that decides how a setting stands on it.

    Attributes:
        name: The server.
        mode: ``managed`` or ``independent``.
        status: How it answered, when it was asked.
        seen: When it last answered, ISO 8601, as the registry has it.
        kinds: The kinds of setting it says it can be managed for; None when it did not say.
        access: The ceiling it published; None when it never did.
    """

    name: str
    mode: str
    status: str | None
    seen: str | None
    kinds: frozenset[str] | None
    access: FleetAccess | None


def _ceiling(access_level: str | None, host_access: bool | None) -> FleetAccess | None:
    """
    Args:
        access_level: What a server published as the most a central may do on it.
        host_access: Whether that includes the host itself.

    Returns:
        The ceiling; None when the server never published one or what it published is not one.
    """
    if access_level is None:
        return None
    try:
        return FleetAccess(validate_access_level(access_level), bool(host_access))
    except ValidationError:
        return None


def _utc(moment: str) -> datetime:
    """
    Args:
        moment: An ISO 8601 time; one without an offset was written in UTC.

    Returns:
        The time, aware.

    Raises:
        ValueError: It is not a time.
    """
    parsed = datetime.fromisoformat(moment)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _changed_after_seen(wanted: DesiredSetting, seen: str | None) -> bool:
    """
    Say whether the central changed a setting after the last time a server answered.

    Args:
        wanted: What the central wants of the server.
        seen: When the server last answered.

    Returns:
        True when the change is later than that, and also when either time is missing or cannot
        be read: a change that nobody can say was seen is a change that is waiting.
    """
    if seen is None:
        return True
    try:
        return _utc(wanted.changed_at) > _utc(seen)
    except ValueError:
        return True


def _standing(
    server: _Server, wanted: DesiredSetting, row: Mapping[str, Any] | None, reach: Reach
) -> tuple[str, str, bool]:
    """
    Say where a server that follows the fleet's settings stands on one of them.

    Args:
        server: The server.
        wanted: What the central wants of it: its exception, else the fleet's.
        row: What the server says it manages of it.
        reach: Whether the route can match anything on the server.

    Returns:
        ``origin``, ``sync`` and whether the setting is waiting for the server.
    """
    origin = "central_exception" if wanted.node else "fleet"
    if reach.reaches is False:
        # Not sent here and not behind: the route has nothing to match on this server.
        return origin, "not_applicable", False
    if server.status != "ok":
        sync = _UNREADABLE.get(server.status or "", "unknown")
        return origin, sync, _changed_after_seen(wanted, server.seen)
    if server.kinds is not None and wanted.resource not in server.kinds:
        return origin, "unsupported", False
    if row is not None and row.get("state") == "overridden":
        return "local_exception", "diverged", False
    if row is not None and row.get("version") == wanted.version:
        return origin, "in_sync", False
    kind = KINDS.get(wanted.resource)
    if (
        server.access is not None
        and kind is not None
        and not permits(server.access, kind.permission)
    ):
        return origin, "refused", True
    return origin, "pending", True


def _follows(wanted: DesiredSetting | None, origin: str, sync: str) -> bool | None:
    """
    Say whether a server follows a setting, for the count of how many do.

    Args:
        wanted: What the central wants of the server; None for none.
        origin: Where the server's value comes from.
        sync: Whether it holds it.

    Returns:
        None when the setting is not meant for the server (the central wants nothing of it, or the
        route cannot match there); False for independence, an exception, a change of the server's
        own, a refusal or a Noust that lacks the kind; True for the rest, which is holding the
        fleet's value, being behind it and being out of reach without having moved away from it.
    """
    if wanted is None:
        return None
    if origin == "independent":
        return False
    if sync == "not_applicable":
        return None
    if origin in ("central_exception", "local_exception"):
        return False
    return sync not in ("refused", "unsupported")


def _cell(
    server: _Server,
    wanted: DesiredSetting | None,
    row: Mapping[str, Any] | None,
    *,
    reach: Reach | None = None,
) -> tuple[dict[str, Any], bool | None]:
    """
    Say where one server stands on one setting.

    Args:
        server: The server.
        wanted: What the central wants of it: its exception, else the fleet's; None for none.
        row: What the server says it manages of it.
        reach: Whether the route can match anything on the server; assumed to when None.

    Returns:
        The cell, and whether the server follows the setting (:func:`_follows`).
    """
    reach = reach or Reach(True)
    independent = server.mode == "independent"
    origin, sync, waiting = "none", "not_applicable", False
    if independent:
        origin, sync = "independent", "release_pending" if row is not None else "not_applicable"
    elif wanted is None:
        sync = "release_pending" if row is not None else "not_applicable"
    else:
        origin, sync, waiting = _standing(server, wanted, row, reach)
    # What it would be given if it followed again, told the way a server that does is told.
    if_followed = (
        _standing(server, wanted, row, reach)[1] if independent and wanted is not None else None
    )
    held = row if server.status == "ok" and row is not None else None
    cell = {
        "node": server.name,
        "origin": origin,
        "sync": sync,
        "waiting": waiting,
        "if_followed": if_followed,
        "wanted_version": wanted.version if wanted is not None and not independent else None,
        "applied_version": held.get("version") if held is not None else None,
        "state": held.get("state") if held is not None else None,
        "applied_by": held.get("applied_by") if held is not None else None,
        "applied_at": held.get("applied_at") if held is not None else None,
        "change": held.get("change") if held is not None else None,
        "restore_requested": bool(held.get("restore_requested_at")) if held is not None else False,
        "reach": {"reaches": reach.reaches, "why": reach.why},
    }
    return cell, _follows(wanted, origin, sync)


def _servers(
    settings: FleetSettings,
    names: Sequence[str],
    outcomes: Mapping[str, NodeOutcome],
    kinds: Mapping[str, frozenset[str]],
) -> dict[str, _Server]:
    """
    Args:
        settings: What the central wants.
        names: The servers.
        outcomes: How each answered.
        kinds: The kinds each says it can be managed for.

    Returns:
        What decides how a setting stands, for each server.
    """
    modes = settings.modes()
    records = {record.name: record for record in settings.store.list_nodes()}
    servers = {}
    for name in names:
        record = records.get(name)
        servers[name] = _Server(
            name=name,
            mode=modes.get(name, "managed"),
            status=outcomes[name].status if name in outcomes else None,
            seen=record.last_seen if record is not None else None,
            kinds=kinds.get(name),
            access=_ceiling(
                record.access_level if record is not None else None,
                record.host_access if record is not None else None,
            ),
        )
    return servers


def compose(
    settings: FleetSettings,
    result: FleetResult,
    *,
    nodes: Sequence[str] | None = None,
    reach: Mapping[tuple[str, str, str], Reach] | None = None,
) -> dict[str, Any]:
    """
    Put what the central wants beside what each server says.

    Args:
        settings: What the central wants.
        result: What each server said it manages (the ``managed_settings`` view).
        nodes: The servers, in order; every registered one by default.
        reach: Whether each setting can match anything on each server (:func:`reach_of`); every
            setting is taken to when None.

    Returns:
        ``servers`` (how each answered, its mode and whether a look is waiting), ``settings``
        (each with its fleet row, its exceptions, how many servers follow it and a cell per
        server), ``released`` (what some server still manages that the central no longer wants),
        ``generated_at`` and ``partial``.
    """
    names = list(nodes) if nodes is not None else [n.name for n in settings.store.list_nodes()]
    outcomes = {outcome.name: outcome for outcome in result.nodes}
    looking = set(settings.pending_resync())
    rows: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {}
    kinds: dict[str, frozenset[str]] = {}
    for item in result.items:
        if "resource" not in item:
            kinds[str(item["node"])] = frozenset(str(kind) for kind in item.get("kinds") or ())
            continue
        rows.setdefault(str(item["node"]), {})[(str(item["resource"]), str(item["name"]))] = item
    servers = _servers(settings, names, outcomes, kinds)

    fleet: dict[tuple[str, str], DesiredSetting] = {}
    exceptions: dict[tuple[str, str], dict[str, DesiredSetting]] = {}
    for setting in settings.list():
        key = (setting.resource, setting.name)
        if setting.node is None:
            fleet[key] = setting
        else:
            exceptions.setdefault(key, {})[setting.node] = setting

    entries = []
    for key in sorted(set(fleet) | set(exceptions)):
        cells = []
        counted: list[bool | None] = []
        for node in names:
            wanted = exceptions.get(key, {}).get(node) or fleet.get(key)
            cell, follows = _cell(
                servers[node],
                wanted,
                rows.get(node, {}).get(key),
                reach=(reach or {}).get((node, key[0], key[1])),
            )
            cells.append(cell)
            counted.append(follows)
        row = fleet.get(key)
        entries.append(
            {
                "resource": key[0],
                "name": key[1],
                "version": row.version if row else None,
                "change": row.change_id if row else None,
                "changed_by": row.changed_by if row else None,
                "changed_at": row.changed_at if row else None,
                "exceptions": sorted(exceptions.get(key, {})),
                "following": sum(1 for follows in counted if follows is True),
                "of": sum(1 for follows in counted if follows is not None),
                "servers": cells,
            }
        )

    wanted_keys = set(fleet) | set(exceptions)
    left: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in names:
        for key, held in rows.get(node, {}).items():
            if key not in wanted_keys:
                left.setdefault(key, []).append(
                    {
                        "node": node,
                        "state": held.get("state"),
                        "applied_version": held.get("version"),
                        "central": held.get("central"),
                    }
                )
    return {
        "generated_at": result.generated_at,
        "partial": result.partial,
        "servers": [
            {
                "node": node,
                "mode": servers[node].mode,
                "outcome": outcomes[node].status if node in outcomes else "unknown",
                "message": outcomes[node].message if node in outcomes else None,
                "code": outcomes[node].code if node in outcomes else None,
                "hint": outcomes[node].hint if node in outcomes else None,
                "pending_look": node in looking,
            }
            for node in names
        ],
        "settings": entries,
        "released": [
            {"resource": key[0], "name": key[1], "servers": on} for key, on in sorted(left.items())
        ],
    }


def read(
    settings: FleetSettings,
    *,
    asker: Asker,
    manager: NodeManager | None = None,
    nodes: Sequence[str] | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """
    Ask every server what it manages and say where each stands.

    Args:
        settings: What the central wants.
        asker: Who asks: each server answers for them.
        manager: The node registry; a default one by default.
        nodes: Only these servers.
        refresh: Ask even a server whose answer is a few seconds old.

    Returns:
        :func:`compose`'s answer.

    Raises:
        SealError: This central is locked; no server can be reached.
    """
    from noust.fleet.nodes import NodeManager

    _register()
    manager = manager or NodeManager()
    result = gather(MANAGED_VIEW.name, asker=asker, manager=manager, nodes=nodes, refresh=refresh)
    names = [n.name for n in result.nodes if not n.local]
    reach = reach_of(settings, names, _applications(settings, asker, manager, names, refresh))
    return compose(settings, result, nodes=names, reach=reach)


def _applications(
    settings: FleetSettings,
    asker: Asker,
    manager: NodeManager,
    names: Sequence[str],
    refresh: bool,
) -> dict[str, list[dict[str, Any]] | None] | None:
    """
    Ask the servers their applications, when some route depends on them.

    Args:
        settings: What the central wants.
        asker: Who asks.
        manager: The node registry.
        names: The servers.
        refresh: Ask even a server whose answer is a few seconds old.

    Returns:
        Each server's applications, None for one that did not answer; None altogether when no
        route depends on them, so nobody is asked for nothing.
    """
    if not any(needs_apps(s.resource, s.value) for s in settings.list()):
        return None
    result = gather("apps", asker=asker, manager=manager, nodes=list(names), refresh=refresh)
    listed: dict[str, list[dict[str, Any]] | None] = {
        o.name: [] for o in result.nodes if o.status == "ok"
    }
    for item in result.items:
        rows = listed.get(str(item.get("node")))
        if rows is not None:
            rows.append(item)
    return {name: listed.get(name) for name in names}
