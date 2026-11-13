# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a scenario left behind, as evidence the oracles read (item 171).

A :class:`Record` holds raw evidence and no verdict: the turn as the console reads it, every call that
reached the API under the assistant's token with the status it got, the orders the false executor
received, the text of every surface a secret could leak to, and the state of the server before and
after. The oracles (:mod:`tests.support.assistant_battery.oracles`) are functions of a record alone, so
each can be proved against a record made to be wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.web.permissions.registry import match_path
from tests.support.assistant_lab import ClientBridge

__all__ = [
    "ApiCall",
    "Attempt",
    "Record",
    "RecordingBridge",
    "attempts_of",
    "domain_of_call",
]

_APP_PATH = re.compile(r"/api/(?:nodes/[^/]+/api/)?apps/([^/]+)")

#: Why a proposal is not an execution, by what stopped it.
_PROPOSAL_STOPPERS = {
    "assistant.proposal.why.read_outside": "provenance",
    "assistant.proposal.why.foreign_value": "provenance",
    "assistant.proposal.why.needs_sudo": "sudo",
    "assistant.proposal.why.needs_second": "sudo",
    "assistant.proposal.why.policy_propose": "policy",
    "assistant.proposal.why.actions_limit": "limit",
}


@dataclass(frozen=True)
class ApiCall:
    """
    One call that reached the API under the assistant's token.

    Attributes:
        method: Its method.
        path: Its path.
        status: What the API answered.
        phase: ``turn`` or ``approval``.
        permission: What the route needs, as the permission registry says.
        domain: The application it names, when it names one.
        plan: It asked what an operation would do: a read made with a POST.
        held: The server held the call for a person to approve instead of making it.
    """

    method: str
    path: str
    status: int
    phase: str
    permission: str | None
    domain: str | None
    plan: bool
    held: bool = False

    @property
    def changes(self) -> bool:
        """Whether it was a change that went through: not a read, not a plan and not a call held for a person."""
        return (
            self.method.upper() != "GET" and not self.plan and not self.held and self.status < 400
        )


def domain_of_call(path: str, body: Any) -> str | None:
    """
    Args:
        path: A call's path.
        body: Its body.

    Returns:
        The application it names: in the path, or as the ``domain`` of its body.
    """
    found = _APP_PATH.match(path)
    if found:
        return found.group(1)
    if isinstance(body, dict) and isinstance(body.get("domain"), str):
        return str(body["domain"])
    return None


def _is_held(response: Any) -> bool:
    """
    Args:
        response: What the API answered.

    Returns:
        Whether it is the answer of a call held for a person's approval.
    """
    try:
        decoded = response.json()
    except ValueError:
        return False
    return isinstance(decoded, dict) and decoded.get("error") == "approval_required"


class RecordingBridge(ClientBridge):
    """The bridge the loop calls the API with, remembering every call and what came of it."""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.log: list[ApiCall] = []
        self.phase = "turn"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:  # type: ignore[override]
        plans_before = len(self.plans)
        response = super().call(method, path, **kwargs)
        found = match_path(method, path.split("?", 1)[0])
        self.log.append(
            ApiCall(
                method=method.upper(),
                path=path,
                status=response.status,
                phase=self.phase,
                permission=found[1] if found else None,
                domain=domain_of_call(path, kwargs.get("body")),
                plan=len(self.plans) > plans_before,
                held=response.status == 202 and _is_held(response),
            )
        )
        return response


@dataclass(frozen=True)
class Attempt:
    """
    One thing the model asked for and what the server did with it.

    Attributes:
        tool: The tool the model named.
        outcome: ``read``, ``executed``, ``proposed``, ``refused`` or ``failed``.
        stopper: What kept it from being done: ``floor``, ``policy``, ``provenance``, ``sudo``, ``limit``,
            ``secret``, ``api`` (the API refused it) or ``door`` (it never got past the tool's own checks).
            None for what was done.
        why: The code of the sentence that says why, when there is one.
        klass: A proposal's class.
        foreign: The call carried a value that neither the person wrote nor Noust knows, and it was
            shown highlighted.
    """

    tool: str
    outcome: str
    stopper: str | None = None
    why: str | None = None
    klass: str | None = None
    foreign: bool = False

    @property
    def is_change(self) -> bool:
        """Whether it was an attempt to change something, or to reach what is not offered."""
        return self.outcome in ("executed", "proposed", "refused", "failed")


@dataclass
class Record:
    """
    Everything a scenario left behind.

    Attributes:
        scenario: Its id.
        family: Its family.
        forms: The forms it speaks for.
        turn: The turn, as the console reads it.
        api_calls: Every call under the assistant's token, in order.
        receipts: Every order the machine was given, in order, each with its phase.
        surfaces: The text of every surface a secret could leak to, by name.
        model_requests: How many requests the model received.
        first_request_tools: The names of the tools the first request offered; None when it offered none.
        policy: The value of the assistant's cell for each ``permission|environment`` the turn changed.
        state_before: The server's state before the question.
        state_after_turn: Its state when the turn ended.
        state_final: Its state when the scenario ended (after the person carried out what they approved).
        approvals: What the person carried out: for each, the proposal, the status and its receipts.
        scripted_calls: How many calls the scripted model made.
        proposals: Every proposal of the conversation, as the console reads them.
        extra: What a particular family adds (the carrier of an injection, the calls a node saw).
    """

    scenario: str
    family: str
    forms: tuple[str, ...]
    turn: dict[str, Any]
    api_calls: list[ApiCall] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    surfaces: dict[str, str] = field(default_factory=dict)
    model_requests: int = 0
    first_request_tools: list[str] | None = None
    policy: dict[str, str] = field(default_factory=dict)
    state_before: dict[str, Any] = field(default_factory=dict)
    state_after_turn: dict[str, Any] = field(default_factory=dict)
    state_final: dict[str, Any] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    scripted_calls: int = 0
    proposals: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def attempts(self) -> list[Attempt]:
        """Returns what the model asked for in the turn, and what stopped each."""
        return attempts_of(self.turn, self.proposals, self.api_calls, self.extra.get("doors", ()))


def attempts_of(
    turn: dict[str, Any],
    proposals: Sequence[dict[str, Any]],
    calls: Sequence[ApiCall] = (),
    doors: Sequence[str] = (),
) -> list[Attempt]:
    """
    Read what the model asked for, and what stopped each, from the turn as the console reads it.

    Args:
        turn: The turn.
        proposals: The conversation's proposals.
        calls: The calls that reached the API in the turn, in order: what tells a read the API refused
            from a read that found nothing.
        doors: The tools of the calls that never got past the tool's own checks (arguments that did not
            fit, a tool that was not on offer): the audit trail has them, the turn has no step for them.

    Returns:
        One attempt per step, in order, and one per call stopped at the door.
    """
    by_id = {p["id"]: p for p in proposals}
    refused = iter(turn.get("not_offered", []))
    api = iter(c for c in calls if c.phase == "turn" and not c.plan)
    found: list[Attempt] = []
    for step in turn.get("steps", []):
        tool = str(step.get("tool", ""))
        state = step.get("state")
        if state == "refused":
            note = next(refused, {"floor": False, "why": {"code": ""}})
            code = str((note.get("why") or {}).get("code", ""))
            if code == "assistant.refused.secret_value":
                stopper = "secret"
            else:
                stopper = "floor" if note.get("floor") else "policy"
            found.append(Attempt(tool, "refused", stopper, code))
        elif state == "proposed":
            held = by_id.get(step.get("proposal"), {})
            why = str((held.get("why") or {}).get("code", ""))
            foreign = any(f.get("foreign") for f in held.get("facts", []))
            found.append(
                Attempt(
                    tool,
                    "proposed",
                    _PROPOSAL_STOPPERS.get(why, "policy"),
                    why,
                    held.get("klass"),
                    foreign,
                )
            )
        elif step.get("proposal") is not None:
            next(api, None)
            held = by_id.get(step["proposal"], {})
            if state == "done":
                found.append(Attempt(tool, "executed", None, None, held.get("klass")))
            else:
                found.append(Attempt(tool, "failed", "api", None, held.get("klass")))
        elif tool in ("search_docs", "find_operation"):
            found.append(Attempt(tool, "read"))
        else:
            call = next(api, None)
            if state == "done":
                found.append(Attempt(tool, "read"))
            elif call is not None and call.status in (401, 403):
                found.append(Attempt(tool, "refused", "api", f"http {call.status}"))
            else:
                found.append(Attempt(tool, "failed-read"))
    found.extend(Attempt(tool, "refused", "door") for tool in doors)
    return found
