# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What came of a call the assistant prepared and a person carried out (item 171).

A proposal is carried out by the person's browser, with the person's own session, by the way every
call is made: sudo mode, a second person, the audit trail. Then the browser tells the assistant's
conversation how it went: "I made it, and it answered this". The server does not take that on faith, and
nothing here concedes anything the call did not already have:

- the call is the proposal's call: the browser sends the fingerprint, and it is the one that was kept;
- the call was **made**: the browser sent the identifier of the proposal as ``X-Noust-Request-Id``, so the
  audit trail has a line for it, by that person, on that path. Without the line it is not given as done;
- what happened is what the audit trail says (a call that was refused is a failed proposal whatever the
  browser reports), and a call that became a request for a second person is one that waits, with the
  request that it made and whose fingerprint must be the proposal's.

A call that became a job is followed by the server, which reads the job when the proposal is asked for:
the browser says it started, and the job says how it ended.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any

from noust.core.assistant.records import Proposal, ProposalError, Proposals
from noust.core.audit import get_log
from noust.core.exceptions import NoustError

__all__ = ["Seen", "followed", "seen_in_audit"]

logger = logging.getLogger(__name__)

_ERROR_RESULT = re.compile(r"error:(\d{3})")

#: How a job's record says it ended well, and badly.
_JOB_DONE = frozenset({"completed"})
_JOB_FAILED = frozenset({"failed", "cancelled"})


@dataclass(frozen=True)
class Seen:
    """
    What the audit trail shows of a call.

    Attributes:
        audited: The audit trail is on and could be read.
        found: It has a line for the call.
        status: The status the line says (``ok`` is a success), when it says one.
    """

    audited: bool
    found: bool = False
    status: int | None = None


def seen_in_audit(proposal: Proposal, actor: str) -> Seen:
    """
    Look for the line the audit trail wrote for a proposal's call.

    Args:
        proposal: The proposal.
        actor: Who carried it out, as the audit trail names them.

    Returns:
        What it shows. ``audited`` is False when the trail is off: nothing can then be proven, and the
        caller says so instead of taking the browser's word as a proof.
    """
    log = get_log()
    if not log.enabled:
        return Seen(audited=False)
    try:
        entries = log.read(
            limit=20,
            correlation_id=proposal.request_id,
            since=proposal.created_at,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        logger.warning("The audit trail could not be read for a proposal: %s", exc)
        return Seen(audited=False)
    action = f"api.{proposal.method.lower()}"
    for entry in entries:
        if entry.get("action") != action or entry.get("resource") != proposal.path:
            continue
        if entry.get("actor") != actor:
            continue
        result = str(entry.get("result") or "")
        error = _ERROR_RESULT.fullmatch(result)
        return Seen(audited=True, found=True, status=int(error.group(1)) if error else None)
    return Seen(audited=True)


def followed(
    proposal: Proposal, account_id: int, *, proposals: Proposals, persist: bool = False
) -> Proposal:
    """
    Bring a proposal that became a job up to date with how the job ended.

    A route that only reads shows the proposal as the job says (it writes nothing: a ``GET`` never does);
    the loop settles it in the record before it tells the model what became of the proposals.

    Args:
        proposal: The proposal.
        account_id: Whose it is.
        proposals: Where proposals are kept.
        persist: Write what the job says into the record.

    Returns:
        The proposal as it is now: done or failed when its job has ended, as it was otherwise.
    """
    if proposal.state != "running" or not proposal.job_id:
        return proposal
    from noust.web.api.deps import scoped_store

    try:
        job = scoped_store().get_job(proposal.job_id)
    except (NoustError, sqlite3.Error):
        return proposal
    if job is None:
        return proposal
    if job.status in _JOB_DONE:
        state, output = "done", None
    elif job.status in _JOB_FAILED:
        state, output = "failed", job.error or job.status
    else:
        return proposal
    if not persist:
        return replace(proposal, state=state, output=output or proposal.output)
    try:
        return proposals.transition(
            proposal.id,
            account_id,
            state,
            by=proposal.decided_by or "",
            output=output,
            verified=proposal.verified,
        )
    except ProposalError:
        return proposal


def state_for(status: int, *, job_id: str | None, approval: int | None) -> str:
    """
    Args:
        status: The status the person's call got.
        job_id: The job it queued, when it queued one.
        approval: The request of a second person it made, when it made one.

    Returns:
        The state the proposal goes to.
    """
    if status == 202 and approval is not None:
        return "second"
    if status == 202 and job_id:
        return "running"
    return "done" if 200 <= status < 400 else "failed"


def result_of(decoded: Any) -> Any:
    """
    Args:
        decoded: What the person's call answered, as the browser reports it.

    Returns:
        It bounded to what a record keeps.
    """
    return decoded if len(repr(decoded)) <= 4_000 else {"truncated": repr(decoded)[:4_000]}
