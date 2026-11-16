# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A turn and a proposal as the console draws them (item 171).

What the loop keeps (:mod:`noust.core.assistant.records`) and what the console is given are not the
same: the console is given sentences with a code and their values (so it says them in its own
language) and the English text and fix beside them, the answer cut at the lines of the system it
quoted, the disguised emails and addresses put back, and a proposal with the exact call it will make
and the header that joins it to the audit trail. One function does it, for the routes and for the
live events alike, so what the person sees while it runs is what they see when they come back.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from noust.core.assistant.prompt import CITATION, parts_of
from noust.core.assistant.records import Proposal, Turn
from noust.core.server_text import ServerText, say

__all__ = ["coded", "present_proposal", "present_turn", "sentence_of"]

logger = logging.getLogger(__name__)

#: The header that joins what the browser does to the proposal it carries out.
REQUEST_ID_HEADER = "X-Noust-Request-Id"


def coded(sentence: ServerText) -> dict[str, Any]:
    """
    Args:
        sentence: A sentence of the catalog.

    Returns:
        It as the API carries one: the code, its values, and the English text and fix.
    """
    return {**sentence.to_dict(), "text": sentence.text, "hint": sentence.hint}


def sentence_of(held: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    Args:
        held: A sentence as it was kept (``code`` and ``params``, and maybe its words).

    Returns:
        It as the API carries one; what the catalog no longer knows keeps its code and its kept words.
    """
    if not held or not held.get("code"):
        return None
    params = held.get("params") or {}
    try:
        return coded(say(str(held["code"]), **params))
    except (KeyError, ValueError, TypeError):
        logger.info("A kept sentence is not in the catalog any more: %s", held.get("code"))
        return {
            "code": held["code"],
            "params": params,
            "text": str(held.get("text") or held["code"]),
            "hint": held.get("hint"),
        }


def plan_of(held: Any) -> dict[str, Any] | None:
    """
    Args:
        held: What the operation's plan route answered, as a proposal keeps it.

    Returns:
        It as the one answer every plan has (:class:`noust.web.api.plan_models.PlanOutModel`), whole; None when
        there is none, or when the route answered something that is not a plan (a server of another version, a
        route of another shape): the proposal is still the exact call, and a loose object is not drawn.
    """
    if not isinstance(held, Mapping):
        return None
    from noust.web.api.plan_models import PlanOutModel
    from noust.web.pydantic_compat import dump_model

    try:
        return dump_model(PlanOutModel(**held))
    except (ValueError, TypeError):
        logger.info("What a plan route answered is not a plan, and is not shown as one")
        return None


def result_of(held: Any) -> str | dict[str, Any] | None:
    """
    Args:
        held: What a call answered, as a proposal keeps it: words, a sentence with a code, the marker of what
            was too long to keep, or whatever the route answered.

    Returns:
        What a card can say of it: its words, or a sentence with a code (the code, its values and the
        English beside them); None when the answer says nothing a card can draw (the call's own body is on
        the audit trail, not here).
    """
    if isinstance(held, str):
        return held or None
    if isinstance(held, Mapping) and held.get("code"):
        return sentence_of(held)
    return None


def present_proposal(proposal: Proposal) -> dict[str, Any]:
    """
    Args:
        proposal: A proposal.

    Returns:
        It as the console draws it: what it is, its class and state, the exact call it will make and
        what came of it.
    """
    return {
        "id": proposal.id,
        "turn": proposal.turn_id,
        "tool": proposal.tool,
        "klass": proposal.klass,
        "state": proposal.state,
        "title": sentence_of(proposal.title),
        "effect": proposal.effect,
        "why": sentence_of(proposal.why),
        "permission": proposal.permission,
        "environment": proposal.environment,
        "server": proposal.server,
        "facts": proposal.facts,
        "plan": plan_of(proposal.plan),
        "needs": {
            "elevation": bool((proposal.needs or {}).get("elevation")),
            "second_person": bool((proposal.needs or {}).get("second_person")),
        },
        "call": {
            "method": proposal.method,
            "path": proposal.path,
            "query": proposal.query,
            "body": proposal.body,
            "headers": {REQUEST_ID_HEADER: proposal.request_id},
        },
        "fingerprint": proposal.fingerprint,
        "created_at": proposal.created_at,
        "decided_at": proposal.decided_at,
        "decided_by": proposal.decided_by,
        "status": proposal.status,
        "result": result_of(proposal.result),
        "output": proposal.output,
        "approval": proposal.approval,
        "job_id": proposal.job_id,
        "verified": proposal.verified,
    }


def _step(step: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        step: A step as it was kept.

    Returns:
        It with its sentence said.
    """
    return {**step, "what": sentence_of(step.get("what"))}


def _usage(held: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        held: What a turn used, as it keeps it: the numbers and the notes as ``{"code", "params"}``.

    Returns:
        The same, with each note said as a sentence (the code, its values and the English beside them).
    """
    notes = [sentence_of(note) for note in held.get("notes") or []]
    return {**held, "notes": [note for note in notes if note is not None]}


def present_turn(
    turn: Turn,
    proposals: Sequence[Proposal],
    restore: Callable[[str], str],
) -> dict[str, Any]:
    """
    Args:
        turn: A turn.
        proposals: Every proposal of its conversation, of which the turn's own are shown.
        restore: Puts back what was disguised.

    Returns:
        The turn as the console draws it.
    """
    citations = turn.citations if isinstance(turn.citations, Mapping) else {}
    cited = {
        key: {
            **value,
            "lines": [{**line, "text": restore(line["text"])} for line in value["lines"]],
        }
        for key, value in citations.items()
    }
    return {
        "id": turn.id,
        "seq": turn.seq,
        "state": turn.state,
        "question": turn.question,
        "page": turn.page,
        "server": turn.server,
        "model": turn.model,
        "location": turn.location,
        "started_at": turn.started_at,
        "ended_at": turn.ended_at,
        "steps": [_step(step) for step in turn.steps],
        "text": restore(CITATION.sub("", turn.answer)),
        "parts": parts_of(turn.answer, cited, restore),
        "proposals": [present_proposal(p) for p in proposals if p.turn_id == turn.id],
        "not_offered": [
            {**item, "title": sentence_of(item.get("title")), "why": sentence_of(item.get("why"))}
            for item in turn.not_offered
        ],
        "warnings": [sentence_of(w) for w in turn.warnings],
        "stop": sentence_of(turn.stop),
        "error": (
            {**turn.error, **(sentence_of(turn.error) or {})}
            if turn.error and turn.error.get("code")
            else None
        ),
        "usage": _usage(turn.usage),
    }
