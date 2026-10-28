# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers that refuse the operator, told as a group.

A fleet view asks every server for the same thing, so when the operator's role does not hold a
permission, every server says no in the same words: three servers, three identical paragraphs.
This turns those answers into what a person needs once: how many servers, which, what is missing
and how to give it, as a sentence with a code the console translates (``noust.core.server_text``)
and never the JSON a node answered with.

The permission is not read out of a node's English. The endpoint the node refused is known
(``NodeOutcome.path``) and this central has its own map of what each route needs
(:mod:`noust.web.permissions.registry`), the same map that decides who may ask the central to ask.
A refusal that is not about a permission (a second factor to enrol, a notice to accept) keeps the
node's own words, grouped when they are the same.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from noust.core.server_text import say
from noust.fleet.aggregate import Asker, NodeOutcome
from noust.web.permissions import PUBLIC, Permission
from noust.web.permissions.registry import permission_for_route

#: The refusal is about a permission the operator's role does not hold.
REASON_PERMISSION = "permission"

#: Anything else a server refuses for: its own words are the explanation.
REASON_OTHER = "other"

#: The code a node answers a missing permission with.
PERMISSION_DENIED = "permission_denied"


def permission_of(path: str) -> str | None:
    """
    Say which permission a server's endpoint needs, by this central's own map.

    Args:
        path: The endpoint a view asked, such as ``/api/audit``.

    Returns:
        The permission, or None when the map does not name the route (a server newer than this
        central answers for routes it has never heard of).
    """
    return permission_for_route("GET", path)


def _named(asker: Asker) -> str:
    """Returns: How a refusal calls the operator's access: their role, else their scope."""
    return asker.role or asker.scope


def _kind(outcome: NodeOutcome) -> tuple[str, str | None]:
    """
    Decide what a refusal is about.

    Args:
        outcome: A server's outcome whose status is ``forbidden``.

    Returns:
        The reason and, for a permission, which one.
    """
    if outcome.code in (None, PERMISSION_DENIED) and outcome.path:
        permission = permission_of(outcome.path)
        if permission is not None and permission not in (PUBLIC, Permission.SELF):
            return REASON_PERMISSION, permission
    return REASON_OTHER, None


def annotate(
    body: dict[str, Any],
    outcomes: Sequence[NodeOutcome],
    resource: str,
    asker: Asker,
) -> list[dict[str, Any]]:
    """
    Group the servers that refused the operator, and say on each what it was refused.

    Args:
        body: The view as JSON; each server's entry in ``nodes`` gains ``permission`` and
            ``refusal`` (None for a server that did not refuse).
        outcomes: The same servers' outcomes, in the same order as ``body["nodes"]``.
        resource: The view they were asked for, such as ``activity``.
        asker: Who asked, for the sentence that names their access.

    Returns:
        One group per thing refused, the largest first: ``reason``, ``code``, ``permission``,
        ``resource``, ``servers``, ``count``, ``includes_central``, ``message`` and ``hint`` (in
        English), and ``coded``: the sentence as a code and its values, None when the words are
        the node's own.
    """
    groups: dict[tuple[str, str, str | None, str | None], dict[str, Any]] = {}
    for entry, outcome in zip(body["nodes"], outcomes, strict=True):
        entry["permission"] = None
        entry["refusal"] = None
        if outcome.status != "forbidden":
            continue
        reason, permission = _kind(outcome)
        code = outcome.code or PERMISSION_DENIED
        entry["permission"], entry["refusal"] = permission, reason
        words = None if reason == REASON_PERMISSION else outcome.message
        group = groups.setdefault(
            (reason, code, permission, words),
            _new_group(reason, code, permission, resource, outcome, asker),
        )
        group["servers"].append(outcome.name)
        group["includes_central"] = group["includes_central"] or outcome.local
    for group in groups.values():
        group["count"] = len(group["servers"])
    return sorted(groups.values(), key=lambda group: -group["count"])


def _new_group(
    reason: str,
    code: str,
    permission: str | None,
    resource: str,
    outcome: NodeOutcome,
    asker: Asker,
) -> dict[str, Any]:
    """
    Start the group of servers that were refused alike.

    Args:
        reason: What the refusal is about.
        code: The code the servers answered with.
        permission: The permission refused, for a refusal about one.
        resource: The view asked.
        outcome: The first server refused, whose words are the group's when they are the node's.
        asker: Who asked.

    Returns:
        The group, with no servers yet.
    """
    if reason == REASON_PERMISSION and permission is not None:
        sentence = say("fleet.refused.permission", role=_named(asker), permission=permission)
        message, hint, coded = sentence.text, sentence.hint, sentence.to_dict()
    else:
        message, hint, coded = outcome.message or "", outcome.hint, None
    return {
        "reason": reason,
        "code": code,
        "permission": permission,
        "resource": resource,
        "servers": [],
        "count": 0,
        "includes_central": False,
        "message": message,
        "hint": hint,
        "coded": coded,
    }
