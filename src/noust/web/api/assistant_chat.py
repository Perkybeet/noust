# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Talking to the built-in assistant: a question, its answer as it happens, and what it proposes (item 171).

A client of :mod:`noust.web.assistant` like the console's panel and ``noust assistant ask`` (rule 3): an
endpoint translates HTTP into a turn of the loop and back, and holds none of its logic. The routes
join the settings, probe and conversation routes of :mod:`noust.web.api.assistant` under the same prefix;
like them they import none of the assistant until a request asks, so that a server with the assistant off
pays nothing for them.

- ``POST /api/assistant/conversations`` begins a conversation; ``POST .../{id}/turns`` asks a question in it
  and answers ``202`` at once: the turn runs in the server and **everything it does arrives on the
  console's event stream** as the ``assistant`` event, to the streams of the account that asked and to no
  other. ``GET .../{id}`` has it all again, for a page that opens in the middle of a turn or later.
- ``POST .../{id}/turns/{turn}/cancel`` stops a turn between two steps.
- A call the assistant **proposes** is carried out by the person's browser with the person's session, and
  the browser reports what came of it to ``POST .../proposals/{id}/outcome``, which proves from the audit
  trail that the call was made. ``POST .../proposals/{id}/reject`` is the other answer. A person decides each
  proposal once; there is no way to approve them all.

A conversation is its author's alone: another account gets what a conversation that does not exist gets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.audit import record as audit_record
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.exceptions import ValidationError
from noust.core.server_text import say
from noust.web.api.assistant import ConversationOut, SentenceOut, _account_id
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, caller_account
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of, permissions_of, scope_of

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

#: The longest question.
MAX_QUESTION = 4_000


class ConversationIn(BaseModel):
    """
    What begins a conversation.

    Attributes:
        title: What to call it; the first question names it when empty.
    """

    title: str = Field(default="", max_length=120)


class TurnIn(BaseModel):
    """
    A question.

    Attributes:
        message: What the person asks.
        page: The page it is asked from, when it is asked about one: ``{"kind": "deployment", "id": 14}``,
            ``{"kind": "diagnosis", "domain": ...}``, ``{"kind": "alert", "rule": ...}`` or
            ``{"kind": "page", "route": ...}``, each with an optional ``server``. Left out, nothing of
            any page is read. Noust reads what the page shows; the model is not told to go and find it.
        server: The server the person is looking at: a server's name, ``*`` for all of them, or left out for
            the one the person is on (on a central that deploys nothing, all of them).
        locale: The language the console speaks, for the language of the answer.
    """

    message: str = Field(min_length=1, max_length=MAX_QUESTION)
    page: dict[str, Any] | None = None
    server: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=20)


class TurnStartedOut(BaseModel):
    """
    A turn that began.

    Attributes:
        turn: Its identifier, which the events carry.
        conversation: The conversation.
        seq: Its place in the conversation, from 1.
        state: ``running``.
        model: The model that answers.
        location: Where that model is.
        server: The server it is about: a name, ``*`` or empty for this one.
    """

    turn: str
    conversation: str
    seq: int
    state: str
    model: str
    location: str
    server: str


class StepOut(BaseModel):
    """
    One thing the assistant looked at or did.

    Attributes:
        id: Its identifier within the turn.
        tool: The tool.
        what: What it is, as a sentence with a code.
        server: The server it was on, or empty.
        state: ``running``, ``done``, ``failed``, ``refused`` or ``proposed``.
        outside: What it read was written by somebody else.
        size: How much it read (``{"unit": "lines" | "items" | "chars" | "results", "count"}``).
        masked: How many values that looked like secrets were hidden before they were sent.
        error: What the server answered, when it failed.
        proposal: The proposal it made, when it made one.
    """

    id: str
    tool: str
    what: SentenceOut | None = None
    server: str = ""
    state: str
    outside: bool = False
    size: dict[str, Any] | None = None
    masked: int | None = None
    error: str | None = None
    proposal: str | None = None


class PartOut(BaseModel):
    """
    A piece of an answer: its words, or lines of the system quoted as the system wrote them.

    Attributes:
        type: ``text`` (Markdown the console draws safely) or ``literal``.
        text: For ``text``, the words.
        source: For ``literal``, which text it is from (``{"id", "kind", "label"}``, the label a sentence).
        lines: For ``literal``, the lines with their numbers (``{"n", "text"}``).
    """

    type: str
    text: str | None = None
    source: dict[str, Any] | None = None
    lines: list[dict[str, Any]] | None = None


class CallOut(BaseModel):
    """
    The exact call a proposal makes.

    Attributes:
        method: The HTTP method.
        path: The path, from ``/api`` on (through ``/api/nodes/{server}/api`` for a server of the fleet).
        query: The query as pairs.
        body: The JSON body, or null.
        headers: What the browser sends with it: ``X-Noust-Request-Id``, which joins the audit trail to the
            proposal.
    """

    method: str
    path: str
    query: list[list[str]] = Field(default_factory=list)
    body: Any = None
    headers: dict[str, str]


class ProposalOut(BaseModel):
    """
    A call the assistant prepared for a person, or made for them where their policy says so.

    Attributes:
        id: Its identifier.
        turn: The turn it was made in.
        tool: The tool it came from.
        klass: How much of the decision is the person's: ``ask`` (their approval, once), ``you`` (it also asks
            them to confirm it is them: sudo mode), ``two`` (it also waits for a second person), or ``alone``
            (the assistant made it, as their policy lets it).
        state: ``waiting``, ``running`` (it became a job), ``second`` (it waits for a second person),
            ``done``, ``failed`` or ``rejected``.
        title: What it is, as a sentence with a code.
        effect: What the operation does, in a line, from the API's own description.
        why: Why it is of this class, as a sentence with a code.
        permission: What it needs.
        environment: ``production`` or ``other``.
        server: The server it is for, or empty.
        facts: The values of the call, each ``{"name", "value"}`` with ``foreign`` on one that is neither the
            person's nor Noust's (``{"kind", "source", "line", "label"}``: where it was found).
        plan: What the operation's simulation says it would do, when it has one.
        needs: What carrying it out asks for (``elevation``, ``second_person``).
        call: The exact call.
        fingerprint: The SHA-256 of the call, the one the approvals use.
        created_at: When it was prepared.
        decided_at: When it was decided.
        decided_by: Who carried it out or rejected it.
        status: The status the call got.
        result: What came of it.
        output: The system's own words when it failed.
        approval: The request of a second person, when it waits for one.
        job_id: The job it became.
        verified: The audit trail showed the call was made.
    """

    id: str
    turn: str
    tool: str
    klass: str
    state: str
    title: SentenceOut | None = None
    effect: str = ""
    why: SentenceOut | None = None
    permission: str = ""
    environment: str = "production"
    server: str = ""
    facts: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    needs: dict[str, Any] = Field(default_factory=dict)
    call: CallOut
    fingerprint: str
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    status: int | None = None
    result: Any = None
    output: str | None = None
    approval: int | None = None
    job_id: str | None = None
    verified: bool = False


class NotOfferedOut(BaseModel):
    """
    Something the assistant was not offered, which the person is told.

    Attributes:
        title: What it was, as a sentence with a code.
        why: Why not, as a sentence with a code: its fix says who changes it.
        floor: Nobody changes it (the floor), as against a cell of the approval policy.
    """

    title: SentenceOut | None = None
    why: SentenceOut | None = None
    floor: bool = False


class TurnOut(BaseModel):
    """
    One question and its answer, as the console draws it.

    Attributes:
        id: Its identifier.
        seq: Its place in the conversation.
        state: ``running``, ``done``, ``failed``, ``cancelled`` or ``stopped`` (by a limit, or because the
            assistant was turned off).
        question: What was asked.
        page: The page it was asked from, or empty.
        server: The server it was about.
        model: The model that answered: the answer says it is an AI, and which.
        location: Where that model is: ``machine``, ``network``, ``eu``, ``outside`` or ``unknown``.
        started_at: When it began.
        ended_at: When it ended.
        steps: What it looked at and did, in order.
        text: The answer as plain text.
        parts: The answer cut at the lines of the system it quotes.
        proposals: The calls it prepared in this turn.
        not_offered: What it was not offered.
        warnings: The limits that are near, as sentences.
        stop: The limit or the switch that ended it, as a sentence.
        error: What went wrong, with the provider's own words in ``output``.
        usage: What it used (``steps``, ``tokens``, ``spend``, ``conversation``, ``currency``).
    """

    id: str
    seq: int
    state: str
    question: str
    page: dict[str, Any] = Field(default_factory=dict)
    server: str = ""
    model: str = ""
    location: str = "unknown"
    started_at: str
    ended_at: str | None = None
    steps: list[StepOut] = Field(default_factory=list)
    text: str = ""
    parts: list[PartOut] = Field(default_factory=list)
    proposals: list[ProposalOut] = Field(default_factory=list)
    not_offered: list[NotOfferedOut] = Field(default_factory=list)
    warnings: list[SentenceOut] = Field(default_factory=list)
    stop: SentenceOut | None = None
    error: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class TurnsOut(BaseModel):
    """
    The turns of a conversation.

    Attributes:
        turns: In order.
    """

    turns: list[TurnOut]


class CancelledOut(BaseModel):
    """
    The result of asking a turn to stop.

    Attributes:
        cancelled: Whether it was running and is asked to stop.
    """

    cancelled: bool


class OutcomeIn(BaseModel):
    """
    What the browser says came of a call it carried out.

    Attributes:
        fingerprint: The proposal's fingerprint, which must be the one that was kept.
        status: The status the call got.
        approval: The request of a second person, when the call became one (the ``202``).
        job_id: The job the call queued, when it queued one.
        output: The system's own words when it failed.
        result: What the call answered, when it is small.
    """

    fingerprint: str = Field(min_length=64, max_length=64)
    status: int = Field(ge=100, le=599)
    approval: int | None = Field(default=None, ge=1)
    job_id: str | None = Field(default=None, max_length=100)
    output: str | None = Field(default=None, max_length=8_000)
    result: Any = None


# -- helpers ---------------------------------------------------------------------------------------


def _masker(chats: Any, conversation_id: str, account_id: int) -> Any:
    """
    Args:
        chats: The conversation store.
        conversation_id: The conversation.
        account_id: Its author.

    Returns:
        What puts back the emails and addresses that were disguised in it.
    """
    from noust.core.assistant.masking import Masker

    return Masker(mask_personal=True, markers=chats.markers(conversation_id, account_id))


def _turns_of(conversation_id: str, account_id: int) -> list[dict[str, Any]]:
    """
    Args:
        conversation_id: A conversation of the caller's.
        account_id: The caller.

    Returns:
        Its turns as the console draws them. One that says it runs and is running nowhere (the server
        restarted under it) is shown as what it is.
    """
    from noust.core.assistant.conversations import Conversations
    from noust.core.assistant.records import Proposals, Turns, interrupted
    from noust.web.assistant.outcome import followed
    from noust.web.assistant.present import present_turn
    from noust.web.assistant.turns import registry

    chats, turns, proposals = Conversations(), Turns(), Proposals()
    alive = registry().alive()
    restore = _masker(chats, conversation_id, account_id).restore
    kept = [
        followed(p, account_id, proposals=proposals)
        for p in proposals.for_conversation(conversation_id, account_id)
    ]
    # A turn that says it runs and runs nowhere is shown as what it is; it is closed in the record when the
    # next question begins, never by a reading: a GET writes nothing.
    return [
        present_turn(
            interrupted(turn) if turn.state == "running" and turn.id not in alive else turn,
            kept,
            restore,
        )
        for turn in turns.for_conversation(conversation_id, account_id)
    ]


def _account(session: dict[str, Any]) -> Any:
    """
    Args:
        session: The authenticated payload.

    Returns:
        The account the caller acts as.

    Raises:
        NoustPermissionError: It is not an account's.
    """
    account = caller_account(session)
    if account is None:
        raise NoustPermissionError.from_text(say("assistant.unavailable.account"))
    return account


def _require_own(conversation_id: str, account_id: int) -> None:
    """
    Args:
        conversation_id: A conversation.
        account_id: The caller.

    Raises:
        ConversationNotFound: It does not exist, or it is another person's: the same answer.
    """
    from noust.core.assistant.conversations import ConversationNotFound, Conversations

    if Conversations().get(conversation_id, account_id) is None:
        raise ConversationNotFound(
            "There is no such conversation", details="It may have been deleted."
        )


def _begin(
    request_loop: asyncio.AbstractEventLoop,
    request: Request,
    conversation_id: str,
    body: TurnIn,
    session: dict[str, Any],
) -> TurnStartedOut:
    """
    Begin a turn: judge what was asked, keep it and start it. Blocking.

    Args:
        request_loop: The event loop the console runs on, which the turn calls the API through.
        request: The request.
        conversation_id: The conversation.
        body: The question.
        session: The authenticated payload.

    Returns:
        The turn, begun.

    Raises:
        NoustPermissionError: The assistant is off, or the caller may not use it.
        ConversationNotFound: There is no such conversation of theirs.
        AssistantTurnRunningError: It is still answering the last question.
        AssistantBusyError: The server is answering as many as it may.
        ValidationError: The page or the server is not acceptable.
    """
    from noust.core.assistant.client import CancelToken
    from noust.core.assistant.records import Turns
    from noust.core.assistant.settings import AssistantSettings
    from noust.core.assistant.state import read_state
    from noust.core.exceptions import AssistantBusyError, AssistantTurnRunningError
    from noust.validators.assistant_page import validate_page
    from noust.web.assistant.loop import TurnContext, TurnRunner
    from noust.web.assistant.reach import resolve_reach
    from noust.web.assistant.toolbox import Reach
    from noust.web.assistant.turns import publisher, registry, tokens

    account = _account(session)
    state = read_state()
    if not state.enabled:
        raise NoustPermissionError.from_text(say("assistant.unavailable.off"))
    _require_own(conversation_id, int(account.id))
    turns = Turns()
    turns.close_stale(conversation_id, int(account.id), alive=registry().alive())
    if registry().running_in(conversation_id) is not None:
        raise AssistantTurnRunningError.from_text(say("assistant.turn.running"))
    page = validate_page(body.page)
    server, names = resolve_reach(
        body.server if body.server is not None else (page or {}).get("server")
    )
    settings = AssistantSettings().read()
    turn = turns.begin(
        conversation_id,
        int(account.id),
        question=body.message,
        model=settings.model,
        location=settings.location,
        page=page,
        server=server,
    )
    from noust.central import role

    context = TurnContext(
        account=account,
        person_permissions=frozenset(permissions_of(session)),
        scope=scope_of(session),
        conversation_id=conversation_id,
        turn=turn,
        question=body.message,
        page=page,
        reach=Reach(server, names),
        hub=role() == "hub",
        locale=body.locale,
        bridge=bridge_for(request, request_loop),
        openapi=request.app.openapi(),
        cancel=CancelToken(),
        publish=publisher(int(account.id)),
        tokens=tokens(),
    )
    try:
        registry().start(TurnRunner(context))
    except AssistantBusyError:
        # Nobody is going to answer it: it ends as what it is, and is not left running.
        turn.state = "failed"
        turns.save(turn)
        raise
    return TurnStartedOut(
        turn=turn.id,
        conversation=conversation_id,
        seq=turn.seq,
        state="running",
        model=turn.model,
        location=turn.location,
        server=server,
    )


def bridge_for(request: Request, loop: asyncio.AbstractEventLoop) -> Any:
    """
    Args:
        request: The request that begins a turn.
        loop: The event loop it is served on.

    Returns:
        The way the turn calls the API: back into this application, as the assistant. A test replaces it.
    """
    from noust.web.assistant.bridge import LoopBridge

    return LoopBridge.of(request, loop)


# -- the routes ------------------------------------------------------------------------------------


@router.post("/conversations", response_model=ConversationOut)
def begin_conversation(
    body: ConversationIn, session: Annotated[dict, Depends(get_current_session)]
) -> ConversationOut:
    """
    Begin a conversation with the assistant.

    Args:
        body: What to call it.
        session: The authenticated session.

    Returns:
        The conversation, empty.

    Raises:
        NoustPermissionError: The assistant is off, or the caller is not an account.
    """
    from noust.core.assistant.conversations import Conversations
    from noust.core.assistant.state import read_state

    account_id = _account_id(session)
    if not read_state().enabled:
        raise NoustPermissionError.from_text(say("assistant.unavailable.off"))
    return ConversationOut(**vars(Conversations().create(account_id, title=body.title)))


@router.post(
    "/conversations/{conversation_id}/turns", response_model=TurnStartedOut, status_code=202
)
async def ask(
    conversation_id: str,
    body: TurnIn,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> TurnStartedOut:
    """
    Ask a question in a conversation; the answer arrives on the event stream.

    The answer is ``202`` at once. The assistant reads, answers and proposes in the server, and what it does
    is announced as the ``assistant`` event of ``/events`` to the streams of this account. Asking while the
    conversation is still answering is a ``409``.

    Args:
        conversation_id: The conversation.
        body: The question, the page it is about and the server.
        request: The request.
        session: The authenticated session.

    Returns:
        The turn that began.

    Raises:
        NoustPermissionError: The assistant is off.
        ConversationNotFound: There is no such conversation of the caller's.
        AssistantTurnRunningError: It is still answering the last question.
        AssistantBusyError: The server is answering as many as it may.
        ValidationError: The page or the server is not acceptable.
    """
    loop = asyncio.get_running_loop()
    return await run_in_threadpool(_begin, loop, request, conversation_id, body, session)


@router.post("/conversations/{conversation_id}/turns/{turn_id}/cancel", response_model=CancelledOut)
def cancel_turn(
    conversation_id: str, turn_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> CancelledOut:
    """
    Ask a turn to stop. It stops between two steps, never in the middle of an action.

    Args:
        conversation_id: The conversation.
        turn_id: The turn.
        session: The authenticated session.

    Returns:
        Whether it was running.
    """
    from noust.core.assistant.records import Turns
    from noust.web.assistant.turns import registry

    account_id = _account_id(session)
    _require_own(conversation_id, account_id)
    turn = Turns().get(turn_id, account_id)
    if turn is None or turn.conversation_id != conversation_id:
        return CancelledOut(cancelled=False)
    return CancelledOut(cancelled=registry().cancel(turn_id))


@router.get("/conversations/{conversation_id}/turns", response_model=TurnsOut)
def list_turns(
    conversation_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> dict[str, Any]:
    """
    Read the turns of one of the caller's conversations: what was asked, what the assistant read, answered and
    proposed.

    Args:
        conversation_id: The conversation.
        session: The authenticated session.

    Returns:
        Its turns in order, with the disguised emails and addresses put back.

    Raises:
        ConversationNotFound: There is none, or it is another person's.
    """
    account_id = _account_id(session)
    _require_own(conversation_id, account_id)
    return {"turns": _turns_of(conversation_id, account_id)}


@router.get("/conversations/{conversation_id}/proposals", response_model=list[ProposalOut])
def list_proposals(
    conversation_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> list[dict[str, Any]]:
    """
    List the calls the assistant prepared in a conversation.

    Args:
        conversation_id: The conversation.
        session: The authenticated session.

    Returns:
        Them, in order.

    Raises:
        ConversationNotFound: There is none, or it is another person's.
    """
    from noust.core.assistant.records import Proposals
    from noust.web.assistant.outcome import followed
    from noust.web.assistant.present import present_proposal

    account_id = _account_id(session)
    _require_own(conversation_id, account_id)
    proposals = Proposals()
    return [
        present_proposal(followed(p, account_id, proposals=proposals))
        for p in proposals.for_conversation(conversation_id, account_id)
    ]


def _own_proposal(conversation_id: str, proposal_id: str, account_id: int) -> Any:
    """
    Args:
        conversation_id: The conversation.
        proposal_id: The proposal.
        account_id: The caller.

    Returns:
        The proposal.

    Raises:
        ConversationNotFound: There is no such proposal of the caller's in that conversation.
    """
    from noust.core.assistant.conversations import ConversationNotFound
    from noust.core.assistant.records import Proposals

    found = Proposals().get(proposal_id, account_id)
    if found is None or found.conversation_id != conversation_id:
        raise ConversationNotFound(
            "There is no such proposal", details="It may be from a conversation that was deleted."
        )
    return found


@router.get("/conversations/{conversation_id}/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(
    conversation_id: str, proposal_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> dict[str, Any]:
    """
    Read one proposal, brought up to date with the job it became.

    Args:
        conversation_id: The conversation.
        proposal_id: The proposal.
        session: The authenticated session.

    Returns:
        The proposal.

    Raises:
        ConversationNotFound: There is no such proposal of the caller's.
    """
    from noust.core.assistant.records import Proposals
    from noust.web.assistant.outcome import followed
    from noust.web.assistant.present import present_proposal

    account_id = _account_id(session)
    proposal = _own_proposal(conversation_id, proposal_id, account_id)
    return present_proposal(followed(proposal, account_id, proposals=Proposals()))


def _announce(proposal: Any, account_id: int) -> None:
    """
    Tell the streams of the proposal's author that it changed, so another tab of theirs draws it.

    Args:
        proposal: The proposal as it is now.
        account_id: Its author.
    """
    from noust.web.assistant.present import present_proposal
    from noust.web.assistant.turns import publisher

    try:
        publisher(account_id)(
            {
                "conversation": proposal.conversation_id,
                "turn": proposal.turn_id,
                "seq": 0,
                "type": "proposal",
                "proposal": present_proposal(proposal),
            }
        )
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("A proposal's change could not be announced: %s", exc)


@router.post(
    "/conversations/{conversation_id}/proposals/{proposal_id}/outcome", response_model=ProposalOut
)
def report_outcome(
    conversation_id: str,
    proposal_id: str,
    body: OutcomeIn,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """
    Say what came of a call the browser carried out, with the person's own session.

    The server gives it as made only when the audit trail shows the call (the browser sent the proposal's
    identifier as ``X-Noust-Request-Id``) and the fingerprint is the proposal's, and what happened is what the
    audit trail says. A call that became a request for a second person must be that proposal's request.

    Args:
        conversation_id: The conversation.
        proposal_id: The proposal.
        body: What the browser says came of it.
        session: The authenticated session.

    Returns:
        The proposal as it is now.

    Raises:
        ConversationNotFound: There is no such proposal of the caller's.
        ValidationError: The fingerprint is not the proposal's, the audit trail has no line for the call, or
            the call became a request that is not this one.
    """
    from noust.core.assistant.records import Proposals
    from noust.web.api.approvals import approvals
    from noust.web.assistant.outcome import result_of, seen_in_audit, state_for
    from noust.web.assistant.present import present_proposal

    account_id = _account_id(session)
    proposal = _own_proposal(conversation_id, proposal_id, account_id)
    if body.fingerprint != proposal.fingerprint:
        raise ValidationError(
            "That is not the call the assistant prepared",
            details="Carry out the call exactly as the proposal gives it.",
            field="fingerprint",
        )
    who = actor_label(session)
    seen = seen_in_audit(proposal, who)
    if seen.audited and not seen.found:
        raise ValidationError(
            "The audit trail has no record of this call being made",
            details="Make the call with the request identifier the proposal gives "
            "(X-Noust-Request-Id), by the person who approves it.",
            field="proposal",
        )
    status = seen.status if seen.status is not None else body.status
    if body.approval is not None and status == 202:
        held = approvals().get(body.approval)
        if held.fingerprint != proposal.fingerprint:
            raise ValidationError(
                "That request of a second person is for another call",
                details="It must be the request this very call made.",
                field="approval",
            )
    to = state_for(status, job_id=body.job_id, approval=body.approval)
    updated = Proposals().transition(
        proposal.id,
        account_id,
        to,
        by=who,
        status=status,
        result=result_of(body.result) if to != "failed" else None,
        output=body.output if to == "failed" else None,
        approval=body.approval,
        job_id=body.job_id,
        verified=seen.audited and seen.found,
    )
    audit_record(
        "assistant.outcome",
        actor=actor_of(session),
        target=f"conversation:{conversation_id[:8]}",
        outcome="ok" if to in ("done", "running", "second") else "failure",
        details={
            "proposal": proposal.id[:12],
            "tool": proposal.tool,
            "state": to,
            "status": status,
            "verified": updated.verified,
        },
    )
    _announce(updated, account_id)
    return present_proposal(updated)


@router.post(
    "/conversations/{conversation_id}/proposals/{proposal_id}/reject", response_model=ProposalOut
)
def reject_proposal(
    conversation_id: str,
    proposal_id: str,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """
    Reject a proposal: nothing is done, and the assistant is told at the next question.

    Args:
        conversation_id: The conversation.
        proposal_id: The proposal.
        session: The authenticated session.

    Returns:
        The proposal, rejected.

    Raises:
        ConversationNotFound: There is no such proposal of the caller's.
        ValidationError: It was already decided.
    """
    from noust.core.assistant.records import Proposals
    from noust.web.assistant.present import present_proposal

    account_id = _account_id(session)
    proposal = _own_proposal(conversation_id, proposal_id, account_id)
    updated = Proposals().transition(proposal.id, account_id, "rejected", by=actor_label(session))
    audit_record(
        "assistant.outcome",
        actor=actor_of(session),
        target=f"conversation:{conversation_id[:8]}",
        details={"proposal": proposal.id[:12], "tool": proposal.tool, "state": "rejected"},
    )
    _announce(updated, account_id)
    return present_proposal(updated)


__all__ = ["bridge_for", "router"]
