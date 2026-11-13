# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Leaving what a token did as it was, as a job (item 185).

The work :class:`~noust.deployers.credential_undo.Undoer` plans and applies, run by the job manager: it goes
through the applications the person chose, each by the way back Noust already has behind its health gate, and
says what went back and what did not. The route that queues it asks for sudo mode, holds the person to the
applications they see and to the permission of deploying in each, and refuses a going back past a change of
the database's schema that was not confirmed; the job asks none of that again, but it does plan again at the
moment it runs, so what changed since the person looked is judged as it is.

It lives in a module of its own because the module of the other jobs is past its size.
"""

from __future__ import annotations

from typing import Any

from noust.core.credential_activity import CredentialRef
from noust.core.server_text import say
from noust.deployers.credential_undo import Undoer, UndoItem
from noust.web.jobs import JobContext
from noust.web.mcp.tools import BY_NAME


def is_read_tool(name: str) -> bool:
    """
    Args:
        name: A tool of MCP.

    Returns:
        Whether it only reads (the tools' own table, which the reading of the audit trail cannot see).
    """
    tool = BY_NAME.get(name)
    return tool is not None and tool.read_only


def undo_credential_job(
    credential: str,
    intervention: int,
    domains: list[str],
    confirmed: list[str],
    visible_domains: list[str] | None = None,
    names: list[str] | None = None,
    by_conversation: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Put an intervention of a token back, for the applications asked, as the plan is now.

    Args:
        credential: The token's name, or what the credential is called (``assistant:<person>``).
        intervention: The intervention's number.
        domains: The applications to put back.
        confirmed: The applications for which going back past a deployment that changed the database's
            schema was confirmed.
        visible_domains: The applications the person who asked sees, so that what is told of the
            intervention is theirs only; None for a person who sees them all.
        names: The tokens the credential is made of, when it is more than one (the assistant of a person).
        by_conversation: The tokens are conversations: an intervention ends where the token changes.
        job_context: Injected by the job manager.

    Returns:
        ``status`` (``undone``, ``partly`` or ``nothing``), ``reverted``, ``not_reverted`` and what the
        credential did that has no way back here (``irreversible``).

    Raises:
        ValueError: When called outside the job manager.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    seen = None if visible_domains is None else frozenset(visible_domains)
    job_context.set_metadata("credential", credential)
    job_context.set_metadata("intervention", intervention)
    total = max(len(domains), 1)
    done = 0

    def step(item: UndoItem) -> None:
        nonlocal done
        job_context.update(
            say("jobs.step.rollback.to_deployment", deployment=item.back_to or 0),
            10 + int(80 * done / total),
        )
        done += 1

    ref = CredentialRef(credential, tuple(names or [credential]), by_conversation)
    result = Undoer().apply(
        ref,
        intervention,
        domains=domains,
        confirmed=confirmed,
        visible=None if seen is None else (lambda domain: domain is not None and domain in seen),
        on_step=step,
        is_read_tool=is_read_tool,
    )
    job_context.update(say("jobs.step.rollback.complete"), 100)
    return result.to_dict()
