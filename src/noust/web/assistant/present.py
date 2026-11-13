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
        "plan": proposal.plan,
        "needs": proposal.needs,
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
        "result": proposal.result,
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
        "usage": turn.usage,
    }
