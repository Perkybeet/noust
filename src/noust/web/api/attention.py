# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/attention``: what needs this person, as one list the server joins (item 161).

The bell, Home and the bar of the console read it, so they show the same number and the console joins
nothing. It is the Overview's own list (the applications, the machine, the findings of Security, the
alerts and the probes: a central reads the same one from every server) plus what is a person's own: the
requests waiting for their decision, a newer Noust and the recipes with an update, each only for whoever
may act on it. The rows are built and ranked by :mod:`noust.managers.attention`; this module only asks
the sources and hands the list over.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import NoustError
from noust.core.teams import scoped_store
from noust.managers.attention import (
    AttentionList,
    noust_item,
    recipe_items,
    request_items,
)
from noust.web.api.approvals import approval_actor, approvals
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute
from noust.web.api.overview import AttentionItem, current_overview
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission

log = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]

#: How long what the recipes' catalogue says of the installed ones is kept: it compares every
#: installed application with the catalogue, and the bell asks every minute.
RECIPES_TTL_SECONDS = 60.0

_recipes_lock = threading.Lock()
_recipes_cache: tuple[float, list[dict[str, Any]]] | None = None


class AttentionResponse(BaseModel):
    """
    What needs this person.

    Attributes:
        items: The rows, the worst first (a state before an alert before a security finding within
            a severity, then by name), each with the ``origin`` it comes from and every reason it
            has; at most 100.
        total: How many there are in all, which is the one number every screen shows.
    """

    items: list[AttentionItem] = Field(default_factory=list)
    total: int = 0


def waiting_requests(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    Args:
        session: Who is asking.

    Returns:
        The requests waiting for a decision that this person may take (never one of their own, and
        none for a token or a central, which never decide), as the approvals' API describes them.
    """
    manager = approvals()
    viewer = approval_actor(dict(session))
    return [
        request.to_dict()
        for request in manager.list(state="requested")
        if manager.eligibility(request, viewer) is None
    ]


def noust_update() -> dict[str, Any] | None:
    """
    Returns:
        What the update check says of this Noust (``has_update``, ``latest_version``,
        ``current_version``, ``security_update``), or None when the check is off or has not
        answered: the same read ``GET /api/system/version`` makes.
    """
    from noust import __version__
    from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress

    if not UpdateChecker.enabled():
        return None
    try:
        check = UpdateChecker.check()
    except UpdateCheckInProgress:
        return None
    return {
        "has_update": check.state == "update_available",
        "latest_version": check.installable,
        "current_version": __version__,
        "security_update": check.security_update,
    }


def recipe_statuses() -> list[dict[str, Any]]:
    """
    Returns:
        Where each application installed from a recipe stands against the catalogue, from a read
        that is at most a minute old.
    """
    global _recipes_cache
    from noust.recipes.updates import installed_statuses

    with _recipes_lock:
        now = time.monotonic()
        if _recipes_cache is not None and now - _recipes_cache[0] < RECIPES_TTL_SECONDS:
            return _recipes_cache[1]
    found = [status.to_dict() for status in installed_statuses()]
    with _recipes_lock:
        _recipes_cache = (now, found)
    return found


def visible_domains(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Args:
        statuses: Anything that names a ``domain``.

    Returns:
        Those of applications the caller's teams own: a person of a team is not told about another's.
    """
    return scoped_store().filter_domains(statuses, lambda status: str(status.get("domain") or ""))


def gather(session: Mapping[str, Any], *, own: bool = False) -> dict[str, Any]:
    """
    Join what the server says of itself with what is this person's, and rank it.

    Args:
        session: Who is asking: what they hold decides what they are told. The server's own list needs
            ``server.read``; a newer Noust, ``server.manage``; the recipes, ``apps.read``; the
            requests are those the person may decide.
        own: Only what is the person's own (the requests, a newer Noust, the recipes), without the
            Overview's list: what a central adds to the lists of the servers it manages, which it
            reads from each server's own Overview.

    Returns:
        ``items`` (at most 100, the worst first) and ``total``.
    """
    rows = AttentionList()
    cut = 0
    if not own and has_permission(dict(session), Permission.SERVER_READ):
        overview = current_overview()
        shown = overview.get("attention") or []
        rows.extend(shown)
        cut = max(int(overview.get("attention_total") or 0) - len(shown), 0)
    try:
        rows.extend(request_items(waiting_requests(session)))
        if has_permission(dict(session), Permission.SERVER_MANAGE):
            rows.extend(noust_item(noust_update()))
        if has_permission(dict(session), Permission.APPS_READ):
            rows.extend(recipe_items(visible_domains(recipe_statuses())))
    except NoustError as exc:
        # What is a person's own is an addition: the server's list is still worth showing.
        log.warning("The attention list could not read what is the person's own: %s", exc)
    items, total = rows.result()
    return {"items": items, "total": total + cut}


@router.get("", response_model=AttentionResponse)
def attention(
    session: Session,
    only: Annotated[
        Literal["own"] | None,
        Query(
            description=(
                "`own`: only what is this person's own (requests to decide, a newer Noust, recipes "
                "behind), without the server's list: what a central adds to the lists of its servers."
            )
        ),
    ] = None,
) -> Any:
    """
    Answer what needs this person, worst first, with one count.

    Args:
        session: Authenticated session, injected.
        only: ``own`` leaves out the server's own list.

    Returns:
        The list the bell, Home and the bar of the console show.
    """
    return gather(session, own=only == "own")
