# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a turn of the assistant kept, and the calls it prepared for a person (item 171).

A **turn** is one question and its answer: what the person asked and from which page, which server
it was about, which model answered and where that model is, what it read (the steps), what it was
not offered, what it said and the lines of the system it quoted, and what it used. A **proposal** is
a call the assistant prepared and did not make: exactly the call (method, path, query, body), its
fingerprint (the one the approvals use), the identifier the person's browser sends with it so that
the audit trail proves it was made, how much of the decision is the person's (its class), and what
came of it.

Both belong to the conversation and its author, who alone reads them: every method takes the
account and answers what is not the account's as what does not exist (rule 4, as the conversations
do). What is written is masked by shape, whoever writes it, and what the system said when a
proposal failed is kept as it said it, masked. A proposal moves forward through its states and never
back; once decided, the model is told what became of it at its next turn, once.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.core.scrubbing import hide_secrets, scrub_text
from noust.core.timeutil import utc_now

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "PROPOSAL_STATES",
    "TURN_STATES",
    "Proposal",
    "ProposalError",
    "Proposals",
    "Turn",
    "Turns",
    "interrupted",
    "new_request_id",
]

#: The states of a turn: it runs, ends well, fails, is cancelled by the person, or is stopped by a
#: limit or by turning the assistant off.
TURN_STATES: tuple[str, ...] = ("running", "done", "failed", "cancelled", "stopped")

#: The states of a proposal. ``waiting`` for the person; ``running`` once they made the call and it
#: became a job; ``second`` once it waits for a second person; ``done``, ``failed`` and
#: ``rejected`` are the ends.
PROPOSAL_STATES: tuple[str, ...] = ("waiting", "running", "second", "done", "failed", "rejected")

#: Where each state may go.
_FORWARD: dict[str, tuple[str, ...]] = {
    "waiting": ("running", "second", "done", "failed", "rejected"),
    "running": ("done", "failed"),
    "second": ("running", "done", "failed", "rejected"),
    "done": (),
    "failed": (),
    "rejected": (),
}

_MAX_TEXT = 60_000
_MAX_OUTPUT = 8_000


class ProposalError(ValidationError):
    """A proposal that does not exist or cannot go where it was asked to."""


def new_request_id() -> str:
    """
    Returns:
        An identifier for the request a person's browser makes to carry out a proposal: one a
        header may carry (``X-Noust-Request-Id``), so that what the audit trail records for the
        call is joined to the proposal.
    """
    return f"ap-{secrets.token_hex(8)}"


def _dump(value: Any) -> str | None:
    """
    Args:
        value: Anything JSON can hold, or None.

    Returns:
        It as JSON with the secret-named values hidden and every string masked, or None.
    """
    if value is None:
        return None
    return json.dumps(hide_secrets(value), ensure_ascii=False, default=str)


def _scrub_words(value: Any) -> Any:
    """
    Args:
        value: Anything JSON can hold.

    Returns:
        A copy in which every string is masked and nothing else is touched: a number is never hidden for
        the name of the key it is under.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {key: _scrub_words(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_words(item) for item in value]
    return value


def _dump_counts(value: Any) -> str | None:
    """
    Args:
        value: What a turn used: counts, spending and sentences with codes.

    Returns:
        It as JSON with the strings masked. :func:`_dump` would hide ``tokens``, which is how many
        tokens were used and not a credential, because the name of the key sounds like one.
    """
    if value is None:
        return None
    return json.dumps(_scrub_words(value), ensure_ascii=False, default=str)


def _load(text: str | None, default: Any) -> Any:
    """
    Args:
        text: JSON read from a column.
        default: What a missing or unreadable value is.

    Returns:
        The decoded value.
    """
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _text(value: str | None, longest: int = _MAX_TEXT) -> str | None:
    """
    Args:
        value: Free text, or None.
        longest: The most characters kept.

    Returns:
        It masked, and with its end cut and said so when it was too long.
    """
    if value is None:
        return None
    cleaned = scrub_text(value)
    if len(cleaned) <= longest:
        return cleaned
    return f"{cleaned[:longest]}\n... (cut: {len(cleaned) - longest} more characters)"


class _Records:
    """The store and the clock, for the two kinds of record."""

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: The moment now, in UTC; for tests.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def _now(self) -> str:
        return self._clock().replace(microsecond=0).isoformat()


# -- turns ---------------------------------------------------------------------------------------


@dataclass
class Turn:
    """
    One question and its answer.

    Attributes:
        id: Its identifier.
        conversation_id: The conversation it belongs to.
        account_id: Whose it is.
        seq: Its place in the conversation, from 1.
        state: One of :data:`TURN_STATES`.
        question: What the person asked, masked.
        page: Where it was asked from (``{"kind", ...}``), or ``{}``.
        server: The server it is about: a node's name, ``*`` for all of them, or ``""`` for this one.
        model: Which model answered.
        location: Where that model is (``machine``, ``network``, ``eu``, ``outside``, ``unknown``).
        answer: What it said, masked, with the lines it quoted as ``[[S1:14-15]]``.
        citations: What each quotation of ``answer`` stands for, by its text (``S1:14-15``).
        steps: What it read and did, in order.
        not_offered: What it was not offered, which the person is told.
        warnings: The limits that are near, as sentences with a code.
        stop: The limit or the switch that ended it, as a sentence with a code; None otherwise.
        error: What went wrong with the provider (``{"code", "text", "output"}``); None otherwise.
        usage: What it used so far (``tokens``, ``spend``, ``steps``).
        started_at: When it began (UTC).
        ended_at: When it ended; None while it runs.
    """

    id: str
    conversation_id: str
    account_id: int
    seq: int
    state: str
    question: str
    page: dict[str, Any]
    server: str
    model: str
    location: str
    answer: str = ""
    citations: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    not_offered: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stop: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    ended_at: str | None = None


_TURN_COLUMNS = (
    "id, conversation_id, account_id, seq, state, question, page, server, model, location, answer, "
    "citations, steps, not_offered, warnings, stop, error, usage, started_at, ended_at"
)
_TURN_SELECT = f"SELECT {_TURN_COLUMNS} FROM assistant_turns "  # noqa: S608 - this module's own names


def interrupted(turn: Turn) -> Turn:
    """
    Args:
        turn: A turn that says it runs and is running nowhere: the server restarted under it.

    Returns:
        It as what it is: a turn that failed with ``assistant.turn.interrupted``. A copy: nothing is
        written, so that reading a conversation never writes.
    """
    return replace(
        turn,
        state="failed",
        error={
            "code": "assistant.turn.interrupted",
            "params": {},
            "text": "The server restarted before the assistant finished this answer",
            "output": "",
        },
    )


def _turn(row: Mapping[str, Any]) -> Turn:
    """
    Args:
        row: A row of ``assistant_turns``.

    Returns:
        The turn.
    """
    return Turn(
        id=row["id"],
        conversation_id=row["conversation_id"],
        account_id=int(row["account_id"]),
        seq=int(row["seq"]),
        state=row["state"],
        question=row["question"],
        page=_load(row["page"], {}),
        server=row["server"],
        model=row["model"],
        location=row["location"],
        answer=row["answer"],
        citations=_load(row["citations"], {}),
        steps=_load(row["steps"], []),
        not_offered=_load(row["not_offered"], []),
        warnings=_load(row["warnings"], []),
        stop=_load(row["stop"], None),
        error=_load(row["error"], None),
        usage=_load(row["usage"], {}),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


class Turns(_Records):
    """The turns of every conversation, in the store's own connection."""

    def begin(
        self,
        conversation_id: str,
        account_id: int,
        *,
        question: str,
        model: str,
        location: str,
        page: Mapping[str, Any] | None = None,
        server: str = "",
    ) -> Turn:
        """
        Begin a turn.

        Args:
            conversation_id: The conversation.
            account_id: Whose it is.
            question: What the person asked.
            model: The model that will answer.
            location: Where it is.
            page: Where the question was asked from.
            server: The server it is about.

        Returns:
            The turn, running.
        """
        now = self._now()
        identifier = uuid.uuid4().hex
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM assistant_turns WHERE conversation_id = ?",
                (conversation_id,),
            )
            seq = int(cursor.fetchone()[0]) + 1
            cursor.execute(
                "INSERT INTO assistant_turns (id, conversation_id, account_id, seq, state, question, "
                "page, server, model, location, started_at) "
                "VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    account_id,
                    seq,
                    _text(question, 4_000),
                    _dump(dict(page or {})),
                    server[:253],
                    model[:200],
                    location,
                    now,
                ),
            )
        return Turn(
            identifier,
            conversation_id,
            account_id,
            seq,
            "running",
            _text(question, 4_000) or "",
            dict(page or {}),
            server[:253],
            model[:200],
            location,
            started_at=now,
        )

    def save(self, turn: Turn) -> None:
        """
        Write what a turn holds now. A turn that is no longer running gets its end.

        Args:
            turn: The turn.

        Raises:
            ValidationError: The state is not one of a turn.
        """
        if turn.state not in TURN_STATES:
            raise ValidationError(f"{turn.state!r} is not a state of a turn", field="state")
        if turn.state != "running" and turn.ended_at is None:
            turn.ended_at = self._now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE assistant_turns SET state = ?, answer = ?, citations = ?, steps = ?, "
                "not_offered = ?, warnings = ?, stop = ?, error = ?, usage = ?, ended_at = ? "
                "WHERE id = ? AND account_id = ?",
                (
                    turn.state,
                    _text(turn.answer),
                    _dump(turn.citations),
                    _dump(turn.steps),
                    _dump(turn.not_offered),
                    _dump(turn.warnings),
                    _dump(turn.stop),
                    _dump(turn.error),
                    _dump_counts(turn.usage),
                    turn.ended_at,
                    turn.id,
                    turn.account_id,
                ),
            )

    def get(self, turn_id: str, account_id: int) -> Turn | None:
        """
        Args:
            turn_id: The turn.
            account_id: Who asks.

        Returns:
            The turn, or None when there is none or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                _TURN_SELECT + "WHERE id = ? AND account_id = ?",
                (turn_id, account_id),
            )
            row = cursor.fetchone()
        return _turn(row) if row is not None else None

    def for_conversation(self, conversation_id: str, account_id: int) -> list[Turn]:
        """
        Args:
            conversation_id: The conversation.
            account_id: Who asks.

        Returns:
            Its turns in order; none when it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                _TURN_SELECT + "WHERE conversation_id = ? AND account_id = ? ORDER BY seq",
                (conversation_id, account_id),
            )
            return [_turn(row) for row in cursor.fetchall()]

    def running(self, conversation_id: str, account_id: int) -> Turn | None:
        """
        Args:
            conversation_id: The conversation.
            account_id: Who asks.

        Returns:
            Its turn that has not ended, if one.
        """
        for turn in reversed(self.for_conversation(conversation_id, account_id)):
            if turn.state == "running":
                return turn
        return None

    def close_stale(
        self, conversation_id: str, account_id: int, *, alive: Collection[str]
    ) -> list[Turn]:
        """
        End the turns that say they run and are not running anywhere: the server restarted under them.

        Args:
            conversation_id: The conversation.
            account_id: Whose it is.
            alive: The turns that really run in this process.

        Returns:
            The turns that were closed, as failed with ``assistant.turn.interrupted``.
        """
        closed = []
        for turn in self.for_conversation(conversation_id, account_id):
            if turn.state == "running" and turn.id not in alive:
                ended = interrupted(turn)
                self.save(ended)
                closed.append(ended)
        return closed


# -- proposals -----------------------------------------------------------------------------------


@dataclass
class Proposal:
    """
    A call the assistant prepared for a person.

    Attributes:
        id: Its identifier.
        conversation_id: The conversation.
        turn_id: The turn it was made in.
        account_id: Whose it is.
        seq: Its place in the conversation.
        tool: The tool it came from.
        klass: ``ask``, ``you``, ``two``, or ``alone`` for a change it made itself.
        state: One of :data:`PROPOSAL_STATES`.
        method: The HTTP method of the call.
        path: Its path, as the console's browser will send it (through the central for a server).
        query: Its query, as pairs.
        body: Its JSON body, or None.
        fingerprint: The SHA-256 of the exact call, the one the approvals use.
        request_id: What the browser sends as ``X-Noust-Request-Id``.
        permission: What the call needs.
        environment: ``production`` or ``other``.
        server: The server it is for: a node's name, or ``""`` for this one.
        title: What it is, as a sentence with a code.
        effect: What the operation does, in a line, from the API's own description.
        why: Why it is a proposal and of this class, as a sentence with a code.
        facts: The values of the call, each as ``{"name", "value"}``, with ``foreign`` for one that
            is not the person's or Noust's.
        plan: What the operation's simulation says it would do, when it has one.
        needs: What carrying it out asks for (``elevation``, ``second_person``).
        created_at: When it was prepared.
        decided_at: When it was decided.
        decided_by: Who carried it out or rejected it.
        result: What came of it, as the API answered.
        output: The system's own words when it failed, masked.
        status: The HTTP status the person's call got.
        approval: The request of a second person, when it waits for one.
        job_id: The job it became.
        verified: The audit trail showed the call was made.
        told: The model has been told how it ended.
    """

    id: str
    conversation_id: str
    turn_id: str
    account_id: int
    seq: int
    tool: str
    klass: str
    state: str
    method: str
    path: str
    query: list[list[str]]
    body: Any
    fingerprint: str
    request_id: str
    permission: str = ""
    environment: str = "production"
    server: str = ""
    title: dict[str, Any] = field(default_factory=dict)
    effect: str = ""
    why: dict[str, Any] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    needs: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    decided_at: str | None = None
    decided_by: str | None = None
    result: Any = None
    output: str | None = None
    status: int | None = None
    approval: int | None = None
    job_id: str | None = None
    verified: bool = False
    told: bool = False


_PROPOSAL_COLUMNS = (
    "id, conversation_id, turn_id, account_id, seq, tool, klass, state, method, path, query, body, "
    "fingerprint, request_id, permission, environment, server, title, effect, why, facts, plan, needs, "
    "created_at, decided_at, decided_by, result, output, status, approval, job_id, verified, told"
)
_PROPOSAL_SELECT = f"SELECT {_PROPOSAL_COLUMNS} FROM assistant_proposals "  # noqa: S608 - this module's own names


def _proposal(row: Mapping[str, Any]) -> Proposal:
    """
    Args:
        row: A row of ``assistant_proposals``.

    Returns:
        The proposal.
    """
    return Proposal(
        id=row["id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        account_id=int(row["account_id"]),
        seq=int(row["seq"]),
        tool=row["tool"],
        klass=row["klass"],
        state=row["state"],
        method=row["method"],
        path=row["path"],
        query=_load(row["query"], []),
        body=_load(row["body"], None),
        fingerprint=row["fingerprint"],
        request_id=row["request_id"],
        permission=row["permission"],
        environment=row["environment"],
        server=row["server"],
        title=_load(row["title"], {}),
        effect=row["effect"],
        why=_load(row["why"], {}),
        facts=_load(row["facts"], []),
        plan=_load(row["plan"], None),
        needs=_load(row["needs"], {}),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        result=_load(row["result"], None),
        output=row["output"],
        status=row["status"],
        approval=row["approval"],
        job_id=row["job_id"],
        verified=bool(row["verified"]),
        told=bool(row["told"]),
    )


class Proposals(_Records):
    """The proposals of every conversation, in the store's own connection."""

    def create(
        self,
        conversation_id: str,
        turn_id: str,
        account_id: int,
        *,
        tool: str,
        klass: str,
        method: str,
        path: str,
        query: Sequence[Sequence[str]],
        body: Any,
        fingerprint: str,
        permission: str,
        environment: str,
        server: str,
        title: Mapping[str, Any],
        effect: str,
        why: Mapping[str, Any],
        facts: Sequence[Mapping[str, Any]],
        plan: Mapping[str, Any] | None,
        needs: Mapping[str, Any],
        state: str = "waiting",
        status: int | None = None,
        result: Any = None,
        output: str | None = None,
        decided_by: str | None = None,
        approval: int | None = None,
    ) -> Proposal:
        """
        Keep a call the assistant prepared (or made, for ``alone``).

        Args:
            conversation_id: The conversation.
            turn_id: The turn.
            account_id: Whose it is.
            tool: The tool it came from.
            klass: ``ask``, ``you``, ``two`` or ``alone``.
            method: The call's method.
            path: Its path, ready for the browser.
            query: Its query pairs.
            body: Its JSON body.
            fingerprint: The call's fingerprint.
            permission: What it needs.
            environment: ``production`` or ``other``.
            server: The server it is for.
            title: What it is, as a sentence with a code.
            effect: What the operation does.
            why: Why, as a sentence with a code.
            facts: The call's values.
            plan: The operation's simulation, if it has one.
            needs: What carrying it out asks for.
            state: ``waiting``, or ``done`` or ``failed`` for what the assistant made itself.
            status: The status of what it made.
            result: What came of it.
            output: The system's words when it failed.
            decided_by: Who made it, for what the assistant made itself.
            approval: The request it waits on.

        Returns:
            The proposal.

        Raises:
            ValidationError: The state or the class is not one a proposal has.
        """
        if state not in PROPOSAL_STATES:
            raise ProposalError(f"{state!r} is not a state of a proposal", field="state")
        if klass not in ("ask", "you", "two", "alone"):
            raise ProposalError(f"{klass!r} is not a class of a proposal", field="klass")
        now = self._now()
        identifier = uuid.uuid4().hex
        request_id = new_request_id()
        decided_at = now if state in ("done", "failed") else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM assistant_proposals WHERE conversation_id = ?",
                (conversation_id,),
            )
            seq = int(cursor.fetchone()[0]) + 1
            cursor.execute(
                "INSERT INTO assistant_proposals (id, conversation_id, turn_id, account_id, seq, tool, "
                "klass, state, method, path, query, body, fingerprint, request_id, permission, "
                "environment, server, title, effect, why, facts, plan, needs, created_at, decided_at, "
                "decided_by, result, output, status, approval) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    turn_id,
                    account_id,
                    seq,
                    tool[:100],
                    klass,
                    state,
                    method.upper(),
                    path[:2000],
                    _dump([list(pair) for pair in query]),
                    _dump(body),
                    fingerprint,
                    request_id,
                    permission,
                    environment,
                    server[:253],
                    _dump(dict(title)),
                    _text(effect, 500),
                    _dump(dict(why)),
                    _dump([dict(f) for f in facts]),
                    _dump(dict(plan) if plan is not None else None),
                    _dump(dict(needs)),
                    now,
                    decided_at,
                    decided_by,
                    _dump(result),
                    _text(output, _MAX_OUTPUT),
                    status,
                    approval,
                ),
            )
        return self._created(self.get(identifier, account_id))

    @staticmethod
    def _created(found: Proposal | None) -> Proposal:
        """
        Args:
            found: A proposal just written, read back.

        Returns:
            It.

        Raises:
            ProposalError: It is not there: the conversation was deleted under the write.
        """
        if found is None:
            raise ProposalError("There is no such proposal")
        return found

    def get(self, proposal_id: str, account_id: int) -> Proposal | None:
        """
        Args:
            proposal_id: The proposal.
            account_id: Who asks.

        Returns:
            The proposal, or None when there is none or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                _PROPOSAL_SELECT + "WHERE id = ? AND account_id = ?",
                (proposal_id, account_id),
            )
            row = cursor.fetchone()
        return _proposal(row) if row is not None else None

    def for_conversation(self, conversation_id: str, account_id: int) -> list[Proposal]:
        """
        Args:
            conversation_id: The conversation.
            account_id: Who asks.

        Returns:
            Its proposals in order; none when it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                _PROPOSAL_SELECT + "WHERE conversation_id = ? AND account_id = ? ORDER BY seq",
                (conversation_id, account_id),
            )
            return [_proposal(row) for row in cursor.fetchall()]

    def transition(
        self,
        proposal_id: str,
        account_id: int,
        to: str,
        *,
        by: str,
        status: int | None = None,
        result: Any = None,
        output: str | None = None,
        approval: int | None = None,
        job_id: str | None = None,
        verified: bool = False,
    ) -> Proposal:
        """
        Move a proposal forward.

        Args:
            proposal_id: The proposal.
            account_id: Who asks.
            to: The state to go to.
            by: Who carried it out or rejected it.
            status: The status the person's call got.
            result: What the API answered.
            output: The system's own words when it failed.
            approval: The request of a second person, when it waits for one.
            job_id: The job it became.
            verified: The audit trail showed the call was made.

        Returns:
            The proposal as it is now.

        Raises:
            ProposalError: There is no such proposal, the state is not one, or the proposal cannot
                go there from where it is.
        """
        if to not in PROPOSAL_STATES:
            raise ProposalError(f"{to!r} is not a state of a proposal", field="state")
        now = self._now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT state FROM assistant_proposals WHERE id = ? AND account_id = ?",
                (proposal_id, account_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ProposalError(
                    "There is no such proposal",
                    details="It may be from a conversation that was deleted.",
                )
            if to not in _FORWARD[row["state"]]:
                raise ProposalError(
                    f"A proposal that is {row['state']} cannot become {to}",
                    details="A proposal is decided once.",
                    field="state",
                )
            cursor.execute(
                "UPDATE assistant_proposals SET state = ?, decided_at = ?, decided_by = ?, "
                "status = COALESCE(?, status), result = COALESCE(?, result), "
                "output = COALESCE(?, output), approval = COALESCE(?, approval), "
                "job_id = COALESCE(?, job_id), verified = ?, told = 0 "
                "WHERE id = ? AND account_id = ?",
                (
                    to,
                    now,
                    by[:120],
                    status,
                    _dump(result),
                    _text(output, _MAX_OUTPUT),
                    approval,
                    job_id[:100] if job_id else None,
                    int(verified),
                    proposal_id,
                    account_id,
                ),
            )
        return self._created(self.get(proposal_id, account_id))

    def untold(self, conversation_id: str, account_id: int) -> list[Proposal]:
        """
        Args:
            conversation_id: The conversation.
            account_id: Who asks.

        Returns:
            The proposals that were decided (or that moved on) since the model was last told.
        """
        return [
            p
            for p in self.for_conversation(conversation_id, account_id)
            if not p.told and p.state != "waiting" and p.klass != "alone"
        ]

    def mark_told(self, proposal_ids: Sequence[str]) -> None:
        """
        Args:
            proposal_ids: The proposals the model has now been told about.
        """
        with self.store._transaction() as cursor:
            for identifier in proposal_ids:
                cursor.execute(
                    "UPDATE assistant_proposals SET told = 1 WHERE id = ?", (identifier,)
                )
