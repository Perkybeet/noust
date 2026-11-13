# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a request made by the approval policy adds to a four-eyes request (item 184).

A call a token or the built-in assistant makes, which :mod:`noust.core.caller_policy` says ``propose``,
becomes the request the second-person rule has always made, with the same fingerprint, the same thirty
minutes and the same single use. This module is what is different, kept beside
:mod:`noust.core.accounts.approvals` instead of inside it:

- **Who decides.** A person with an account who holds every permission the call needs. That can be the
  owner of the token: she is supervising her own tool, which is not the second person the rule asks for.
  A token, the master token and a central never decide; the command line as root decides, as it does for
  every request. A request the rule also holds (``both``) is decided as the rule says, by another person.
- **What it remembers** (the columns the schema 18 fragment of this item adds to ``approval_requests``):
  the class of caller, the environment, the application, what the call needs and the client the credential
  said it was.
- **When changing the policy asks for a person.** A change that gives a token or the assistant more
  freedom than it has waits for a second person; one that only takes freedom away does not, because it can
  only make the server safer.

Nothing here knows HTTP.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noust.core.accounts.approvals import ApprovalActor, ApprovalRequest

#: The longest a stored list or document may be; a larger one is a defect, not a request.
_MAX_JSON = 8 * 1024


def read_columns(row: sqlite3.Row) -> dict[str, Any]:
    """
    Read what a request remembers of the policy from its row.

    Args:
        row: A row of ``approval_requests``.

    Returns:
        The keyword arguments of :class:`~noust.core.accounts.approvals.ApprovalRequest` that come
        from the columns of item 184: a row from before them is a request of the rule.
    """
    keys = set(row.keys())

    def column(name: str) -> Any:
        return row[name] if name in keys else None

    needs = _loads(column("needs"), list)
    client = _loads(column("client"), dict)
    return {
        "origin": str(column("origin") or "rule"),
        "caller": column("caller"),
        "environment": column("environment"),
        "domain": column("domain"),
        "needs": tuple(str(item) for item in needs) if needs else (),
        "client": client or None,
    }


def write_columns(
    origin: str,
    caller: str | None,
    environment: str | None,
    domain: str | None,
    needs: Collection[str],
    client: Mapping[str, Any] | None,
) -> tuple[Any, ...]:
    """
    Turn what a request remembers into the values of its columns.

    Args:
        origin: ``rule``, ``policy`` or ``both``.
        caller: The class of caller, when it is not a person.
        environment: ``production`` or ``other``.
        domain: The application the call names.
        needs: The permissions the call needs.
        client: What the credential's client said of itself.

    Returns:
        ``(origin, caller, environment, domain, needs, client)``, the lists and documents as JSON.
    """
    return (
        origin,
        caller,
        environment,
        domain[:253] if domain else None,
        json.dumps(sorted(set(needs)))[:_MAX_JSON] if needs else None,
        json.dumps(dict(client), sort_keys=True)[:_MAX_JSON] if client else None,
    )


def audit_details(request: ApprovalRequest) -> dict[str, Any]:
    """
    Args:
        request: A request, as it was just made.

    Returns:
        What the audit event of its making adds: who and where, never the call's parameters.
    """
    found: dict[str, Any] = {"origin": request.origin}
    if request.caller:
        found["caller"] = request.caller
    if request.environment:
        found["environment"] = request.environment
    if request.domain:
        found["domain"] = request.domain
    if request.client:
        found["client"] = {
            key: request.client[key]
            for key in ("name", "version", "source")
            if key in request.client
        }
    return found


def eligibility(request: ApprovalRequest, decider: ApprovalActor) -> str | None:
    """
    Say why a person may not decide a request the approval policy made, if they may not.

    The caller has already refused whoever is not an account (and let the command line through).

    Args:
        request: A request whose origin is the policy.
        decider: The account that wants to decide it, with what it holds where the call lands.

    Returns:
        None when they may; otherwise the sentence to refuse with.
    """
    held = decider.permissions or frozenset()
    missing = [permission for permission in request.needs if permission not in held]
    if missing:
        return (
            "Deciding this call takes permissions you do not hold: "
            + ", ".join(missing)
            + ". A person who holds them, such as the owner of the token, decides it."
        )
    return None


def loosens_policy(body: Any, _params: Mapping[str, str]) -> bool:
    """
    Say whether a change of the approval policy gives a token or the assistant more freedom than it has.

    Judged against what the policy is now, cell by cell: a value less strict than the one in force, or
    a cell put back to what the release ships when that is less strict than the one in force. A body that
    names no cell that exists is not one: the handler refuses it, and a refusal changes nothing.

    Args:
        body: The parsed JSON body: ``cells`` (``caller``, ``environment``, ``permission``, ``value``)
            and ``reset`` (``caller``, ``environment``, ``permission``).
        _params: The route's parameters.

    Returns:
        True when some cell would be less strict than it is.
    """
    if not isinstance(body, Mapping):
        return False
    from noust.core.caller_policy import VALUES, CallerPolicy
    from noust.core.exceptions import ValidationError

    policy = CallerPolicy()
    rank = {value: number for number, value in enumerate(VALUES)}
    for item in _items(body.get("cells")):
        try:
            current = policy.cell(
                str(item.get("caller")), str(item.get("environment")), str(item.get("permission"))
            )
        except ValidationError:
            continue
        wanted = item.get("value")
        if isinstance(wanted, str) and wanted in rank and rank[wanted] < rank[current.value]:
            return True
    for item in _items(body.get("reset")):
        try:
            current = policy.cell(
                str(item.get("caller")), str(item.get("environment")), str(item.get("permission"))
            )
        except ValidationError:
            continue
        if rank[current.default] < rank[current.value]:
            return True
    return False


def _items(value: Any) -> list[Mapping[str, Any]]:
    """
    Args:
        value: What a body holds under ``cells`` or ``reset``.

    Returns:
        The objects in it, at most a hundred, and nothing for what is not a list.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value[:100] if isinstance(item, Mapping)]


def _loads(text: Any, kind: type) -> Any:
    """
    Args:
        text: A column holding JSON, or None.
        kind: ``list`` or ``dict``.

    Returns:
        The value when it is of that kind, otherwise an empty one.
    """
    if not isinstance(text, str) or not text:
        return kind()
    try:
        value = json.loads(text)
    except ValueError:
        return kind()
    return value if isinstance(value, kind) else kind()
