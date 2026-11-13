# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The approval policy for a caller that is not a person, at the guard of the API (item 184).

:func:`noust.web.api.approvals.require_approval` is where every call to the API is judged for a second
person, and the MCP and the API pass through it alike (the MCP is a client of the API and calls it with the
caller's own token). The policy is decided there, by :class:`~noust.core.caller_policy.CallerPolicy`, and no
route decides it by itself (rule 4). This module is the translation between HTTP and that policy: which
class of caller a request is, what the call is, which environment its resource lives in, and the answer a
refusal gives.

Where the resource lives is read from the store with the scope of whoever asks, through the same references
the team scope reads (:func:`noust.web.api.scope.placements_of`), never from a parameter the caller sends:
a call that names no application, that names one that is not found or not seen, or that is forwarded to
another server, is judged as production.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from noust.core.audit import record
from noust.core.caller_policy import (
    ASSISTANT_NEVER_REASONS,
    NEVER,
    OTHER,
    PRODUCTION,
    CallerPolicy,
    Verdict,
    environment_of_target,
)
from noust.core.callers import ASSISTANT, caller_of
from noust.core.credential_clients import CredentialClients
from noust.core.operation_classes import (
    ACTION,
    READ,
    OperationClass,
    Unclassified,
    classify_parts,
    escalated_permission,
)
from noust.core.server_text import ServerText, say
from noust.core.store import StoreError
from noust.web.api.scope import placements_of
from noust.web.permissions import PUBLIC
from noust.web.permissions.enforce import required_permissions
from noust.web.permissions.principal import actor_of
from noust.web.permissions.registry import permission_for_route
from noust.web.permissions.scopes import APP, DOMAIN, rule_for

logger = logging.getLogger(__name__)

#: The error a refusal by the table carries, and the one a refusal by the floor does.
ERROR_NEVER = "approval_never"
ERROR_FLOOR = "approval_floor"

#: The audit event a refused call is recorded as, the one every refusal of the approval guard uses.
DENIED_EVENT = "http.denied.approval"


@dataclass(frozen=True)
class Judgement:
    """
    How the approval policy judged one call.

    Attributes:
        verdict: What it said and why.
        needs: Every permission the call needs, what a person holds to decide it.
        domain: The application the call names, when it names one.
        operation: What the operation is.
    """

    verdict: Verdict
    needs: tuple[str, ...]
    domain: str | None
    operation: OperationClass


async def where(
    request: Request, method: str, template: str, *, proxied: bool
) -> tuple[str, str | None]:
    """
    Say which environment the resource a call names is in, and which application it is.

    Args:
        request: The call.
        method: Its method.
        template: Its route's template (the node's own, for a call through the proxy).
        proxied: Whether the call is forwarded to another server, whose applications this one does not
            hold: those are judged as production.

    Returns:
        ``(environment, domain)``: production when nothing says otherwise, and the domain the call names
        when it names one.
    """
    if proxied:
        return PRODUCTION, None
    rule = rule_for(method, template)
    if rule is None:
        return PRODUCTION, None
    found = await placements_of(request, rule)
    if not found:
        return PRODUCTION, None
    domain = next((name for ref, name, _ in found if ref.kind in (APP, DOMAIN) and name), None)
    environments = {
        environment_of_target(target.environment_id) if target is not None else PRODUCTION
        for _ref, _name, target in found
    }
    return (OTHER if environments == {OTHER} else PRODUCTION), domain


def _operation(
    method: str, template: str, permission: str | None, *, elevation: bool, approval: str | None
) -> OperationClass:
    """
    Args:
        method: The call's method.
        template: Its route's template.
        permission: What the route needs.
        elevation: Whether it asks for sudo mode.
        approval: When a four-eyes rule covers it.

    Returns:
        The operation's class. A route nobody classified (a test fails for that before it is served) is
        taken for an action of its permission, which is what judging it strictly is.
    """
    try:
        return classify_parts(method, template, permission, elevation=elevation, approval=approval)
    except Unclassified as exc:
        logger.error("The approval policy met an operation nobody classified: %s", exc)
        return OperationClass(method.upper(), template, permission or PUBLIC, ACTION)


async def judge(
    request: Request,
    session: dict[str, Any],
    *,
    method: str,
    template: str,
    needed: list[str],
    elevation: bool,
    approval_when: str | None,
    four_eyes_action: str | None,
    proxied: bool,
) -> Judgement:
    """
    Judge a call from a caller that is not a person.

    Args:
        request: The call.
        session: The authenticated payload.
        method: The call's method.
        template: Its route's template (the node's own, for a call through the proxy).
        needed: Every permission the call needs.
        elevation: Whether the route asks for sudo mode.
        approval_when: When a four-eyes rule covers the route (``always`` or a condition on the body).
        four_eyes_action: The action of the four-eyes rule that matched the call's body, if one did.
        proxied: Whether the call is forwarded to another server.

    Returns:
        The judgement.
    """
    caller = caller_of(session)
    operation = _operation(
        method,
        template,
        permission_for_route(method, template),
        elevation=elevation,
        approval=approval_when,
    )
    escalated = escalated_permission(four_eyes_action)
    environment, domain = await where(request, method, template, proxied=proxied)
    verdict = CallerPolicy().decide(caller, environment, operation, needed, escalated=escalated)
    needs = tuple(dict.fromkeys([*needed, *([escalated] if escalated else [])]))
    return Judgement(verdict, needs, domain, operation)


async def judge_read(request: Request, session: dict[str, Any]) -> None:
    """
    Hold the built-in assistant to the floor on a call that only reads.

    What its token must never hold is not read either: a token that somehow held it is refused here, before
    the route answers.

    Args:
        request: The call.
        session: The authenticated payload.

    Raises:
        HTTPException: 403 ``approval_floor`` for a read that needs what the assistant never holds.
    """
    needed = await required_permissions(request) or []
    operation = OperationClass(request.method.upper(), request.url.path, PUBLIC, READ)
    verdict = CallerPolicy().decide(ASSISTANT, PRODUCTION, operation, needed)
    if verdict.value == NEVER:
        refuse(request, session, Judgement(verdict, tuple(needed), None, operation))


def refuse(request: Request, session: dict[str, Any], judgement: Judgement) -> None:
    """
    Record a call the policy never allows and answer it.

    Args:
        request: The call.
        session: The authenticated payload.
        judgement: What the policy said, which was ``never``.

    Raises:
        HTTPException: 403, with the sentence that says which policy forbids it and who changes it.
    """
    verdict = judgement.verdict
    record(
        DENIED_EVENT,
        actor=actor_of(session),
        target=request.url.path,
        outcome="denied",
        details={
            "action": "caller.never",
            "reason": verdict.reason,
            "caller": verdict.caller,
            "environment": verdict.environment,
            "permission": verdict.permission or "",
        },
    )
    floor = verdict.reason in ASSISTANT_NEVER_REASONS
    said = never_sentence(verdict)
    raise HTTPException(
        status_code=403,
        detail={
            "error": ERROR_FLOOR if floor else ERROR_NEVER,
            "detail": said.text,
            "hint": said.hint,
            "fields": {
                "caller": verdict.caller,
                "environment": verdict.environment,
                "permission": verdict.permission,
            },
            "coded": said.to_dict(),
            "notes_coded": [],
        },
    )


def never_sentence(verdict: Verdict) -> ServerText:
    """
    Args:
        verdict: A ``never``.

    Returns:
        The sentence that says which policy forbids the call and who changes it.
    """
    if verdict.reason in ASSISTANT_NEVER_REASONS:
        return say("approvals.never_assistant_floor")
    code = (
        "approvals.never_by_policy_assistant"
        if verdict.caller == ASSISTANT
        else "approvals.never_by_policy_token"
    )
    return say(code, permission=verdict.permission or "", environment=verdict.environment)


def held_sentence(
    caller: str, environment: str, permission: str, *, request: int, header: str, minutes: int
) -> ServerText:
    """
    Args:
        caller: The class of caller.
        environment: The environment judged.
        permission: The permission whose cell held the call.
        request: The request that was made.
        header: The header the call is repeated under.
        minutes: How long an approval allows the call.

    Returns:
        The sentence that says the call waits for a person and what to do next.
    """
    code = (
        "approvals.held_by_policy_assistant"
        if caller == ASSISTANT
        else "approvals.held_by_policy_token"
    )
    return say(
        code,
        permission=permission,
        environment=environment,
        request=request,
        header=header,
        minutes=minutes,
    )


def note_client(session: dict[str, Any], request: Request) -> None:
    """
    Remember the client a token says it is, from the ``User-Agent`` of its request.

    Never raises: a client that cannot be written down is not a reason to refuse a call.

    Args:
        session: The authenticated payload.
        request: The call.
    """
    name = session.get("token_name")
    if not name or session.get("type") != "api_token" or session.get("fleet"):
        return
    try:
        CredentialClients().note_user_agent(str(name), request.headers.get("user-agent"))
    except (sqlite3.Error, StoreError) as exc:
        logger.warning("Could not note the client of the token %s: %s", name, exc)


def current_client(session: dict[str, Any]) -> dict[str, Any] | None:
    """
    Args:
        session: The authenticated payload.

    Returns:
        What the token's client said of itself (the MCP handshake's name when there was one, else the
        newest ``User-Agent`` product), as the API shows it; None when nobody was seen.
    """
    name = session.get("token_name")
    if not name:
        return None
    try:
        found = CredentialClients().current(str(name))
    except (sqlite3.Error, StoreError) as exc:
        logger.warning("Could not read the client of the token %s: %s", name, exc)
        return None
    return found.to_dict() if found is not None else None


__all__ = [
    "DENIED_EVENT",
    "ERROR_FLOOR",
    "ERROR_NEVER",
    "Judgement",
    "current_client",
    "held_sentence",
    "judge",
    "judge_read",
    "never_sentence",
    "note_client",
    "refuse",
    "where",
]
