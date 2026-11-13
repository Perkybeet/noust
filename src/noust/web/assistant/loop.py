# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The loop of the built-in assistant: it explains, investigates and proposes (item 171).

A turn is the simplest loop there is: the model is asked, it answers or calls a tool, the tool's
answer is given back, and again, until it answers. It runs in the server, in a thread of its own, as a
client of the API with a token of its own; the browser never speaks to the provider. Everything that
protects anybody is here, outside the model, and none of it depends on the model behaving:

- **Before every step the meter is asked** (:class:`~noust.core.assistant.limits.UsageMeter`), and a
  limit cuts between two steps and never in the middle of an action: the loop makes the calls of a step
  that was already paid for and only then asks again.
- **Every call is classed from what the API publishes of its operation** and judged by the policy of
  who calls (:mod:`noust.core.assistant.calls`). A read is made. A change is made only where the
  policy says "alone", and only if no value in it comes from text somebody else wrote and no such text was
  read in this conversation; everything else is **proposed**: kept exactly, for the person to carry out
  with their own session, where sudo mode and a second person are asked as they always are. What the
  floor or a cell forbids is not offered, and the model is told so.
- **What reaches the model is masked**: the values Noust knows are secret and the shapes of a credential
  are gone, emails and addresses are disguised for a provider outside, what comes from outside is marked as
  data, and every result is bounded (a log by its end, not its start).
- **Nothing it says about the system is its own word**: the lines of an error it leans on are quoted by Noust
  from its own copy of the text.
- **What it does is on the audit trail** as the assistant acting for the person: the tool, the application
  and how it ended; never the arguments' values and never the conversation.

The loop never raises: however a turn ends it leaves the turn's record in a final state and says so to the
person.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.accounts.approvals import rule_for, snapshot
from noust.core.accounts.model import Account
from noust.core.assistant.calls import (
    PROPOSE,
    REFUSE,
    REFUSED,
    Decision,
    decide,
    environment_for,
)
from noust.core.assistant.client import (
    CancelToken,
    Completion,
    Finish,
    MalformedArguments,
    ProviderClient,
    ProviderError,
    TextDelta,
    ToolCall,
    Usage,
    assistant_message,
    estimate_tokens,
)
from noust.core.assistant.conversations import Conversations
from noust.core.assistant.docsearch import DocIndex
from noust.core.assistant.limits import UsageMeter
from noust.core.assistant.masking import Masker, mark_as_data
from noust.core.assistant.prompt import (
    CONTEXT_TAG,
    SourceBook,
    StreamFilter,
    history_messages,
    resolve_citations,
    system_prompt,
)
from noust.core.assistant.provenance import Foreign, Provenance, known_names
from noust.core.assistant.records import Proposal, Proposals, Turn, Turns
from noust.core.assistant.settings import AssistantKeyError, AssistantSettings, Settings
from noust.core.audit import record as audit_record
from noust.core.caller_policy import Verdict
from noust.core.config import REDACTED
from noust.core.exceptions import NoustError
from noust.core.paths import docs_directories
from noust.core.scrubbing import hide_secrets
from noust.core.server_text import ServerText, say
from noust.core.store import NoustStore
from noust.core.teams.scope import Scope, bind
from noust.web.assistant import context as page_context
from noust.web.assistant.bridge import ApiBridge
from noust.web.assistant.identity import (
    AssistantIdentity,
    AssistantToken,
    assistant_actor,
    off_sentence,
)
from noust.web.assistant.outcome import followed
from noust.web.assistant.present import coded, present_proposal, present_turn, sentence_of
from noust.web.assistant.toolbox import (
    Prepared,
    Reach,
    Toolbox,
    ToolProblem,
    ToolRefused,
    proxy_path,
)
from noust.web.mcp import guardrails

__all__ = ["TokenCache", "TurnContext", "TurnRunner"]

logger = logging.getLogger(__name__)

#: The most characters of a log the model is given: its end, because what happened last is what matters.
LOG_CHARS = 12_000
#: The most characters of anything else a tool returns to the model.
RESULT_CHARS = 12_000
#: The most facts a proposal lists.
_MAX_FACTS = 14
#: The longest a turn may take, in seconds, whatever its steps: a turn is a thread of the server, and a
#: model that is slow at every one of twenty steps would hold it for hours.
TURN_SECONDS = 15 * 60
#: How much the live text is gathered before it is announced, so that a screen reader is not read a word at a time.
FLUSH_CHARS = 48
FLUSH_SECONDS = 0.2

#: Why a call is a proposal, by the word the decision gives. Written out: a code is a literal wherever it is said.
WHY_CODES: dict[str, str] = {
    "policy_propose": "assistant.proposal.why.policy_propose",
    "policy_alone": "assistant.proposal.why.policy_alone",
    "needs_sudo": "assistant.proposal.why.needs_sudo",
    "needs_second": "assistant.proposal.why.needs_second",
    "foreign_value": "assistant.proposal.why.foreign_value",
    "read_outside": "assistant.proposal.why.read_outside",
    "actions_limit": "assistant.proposal.why.actions_limit",
}

_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")


class _Stopped(Exception):
    """The turn must end here: a limit, or the assistant was turned off."""

    def __init__(self, sentence: ServerText) -> None:
        super().__init__(sentence.text)
        self.sentence = sentence


class _Cancelled(Exception):
    """The person asked for the turn to stop."""


class TokenCache:
    """The tokens of the conversations that are being answered, kept in memory and nowhere else."""

    def __init__(self) -> None:
        self._held: dict[str, AssistantToken] = {}

    def get(
        self,
        identity: AssistantIdentity,
        account: Account,
        conversation_id: str,
        *,
        fresh: bool = False,
    ) -> AssistantToken:
        """
        Args:
            identity: The issuer of the assistant's tokens.
            account: The person who asks.
            conversation_id: The conversation.
            fresh: Issue a new one even if one is held (the held one was taken back).

        Returns:
            The conversation's token: the one held while it has more than ten minutes to live,
            else a new one.
        """
        held = self._held.get(conversation_id)
        if held is not None and not fresh and held.expires_at - time.time() > 600:
            return held
        issued = identity.issue(account, conversation_id)
        self._held[conversation_id] = issued
        return issued

    def drop(self, conversation_id: str) -> None:
        """
        Args:
            conversation_id: A conversation whose tokens were taken back.
        """
        self._held.pop(conversation_id, None)

    def clear(self) -> None:
        """Forget every token: the assistant was turned off."""
        self._held.clear()


@dataclass
class TurnContext:
    """
    Everything one turn needs, given by the route that begins it.

    Attributes:
        account: The person who asks.
        person_permissions: What they hold: what a change the assistant proposes needs, because they
            carry it out.
        scope: The teams they see.
        conversation_id: The conversation.
        turn: The turn's record, running.
        question: What they asked, as they wrote it.
        page: The page it is asked from, judged, or None.
        reach: Where they are looking.
        hub: This Noust is a central that deploys nothing itself.
        locale: The language the console speaks, if it says.
        bridge: The way to the API.
        openapi: The API's description.
        cancel: Set to stop the turn.
        publish: Announces what the turn does, to the person's streams.
        tokens: The tokens held for conversations.
        store: The store; the process-wide one when None.
        identity: The issuer of the assistant's tokens.
        client_factory: Makes the client of the provider from the settings and the key.
        docs: The documentation, when there is any.
    """

    account: Account
    person_permissions: frozenset[str]
    scope: Scope
    conversation_id: str
    turn: Turn
    question: str
    page: dict[str, Any] | None
    reach: Reach
    hub: bool
    locale: str | None
    bridge: ApiBridge
    openapi: Mapping[str, Any]
    cancel: CancelToken
    publish: Callable[[dict[str, Any]], None]
    tokens: TokenCache
    store: NoustStore | None = None
    identity: AssistantIdentity | None = None
    client_factory: Callable[[Settings, str], ProviderClient] | None = None
    docs: DocIndex | None = None


def default_client(settings: Settings, key: str) -> ProviderClient:
    """
    Args:
        settings: The assistant's settings.
        key: The provider's key.

    Returns:
        The client of the provider.
    """
    return ProviderClient(settings.base_url, settings.model, api_key=key)


_DOCS: list[DocIndex] = []


def shared_docs() -> DocIndex:
    """
    Returns:
        The documentation found on this machine, read the first time and kept.
    """
    if not _DOCS:
        _DOCS.append(DocIndex(docs_directories()))
    return _DOCS[0]


@dataclass
class _Result:
    """What a tool call comes to, for the model."""

    text: str
    ok: bool = True
    outcome: str = "ok"


@dataclass
class _Totals:
    """What the turn has used."""

    steps: int = 0
    tokens: int = 0
    spend: float = 0.0
    tools: list[str] = field(default_factory=list)


class TurnRunner:
    """One turn of a conversation, from the question to the answer."""

    def __init__(self, ctx: TurnContext) -> None:
        """
        Args:
            ctx: What the turn needs.
        """
        self.ctx = ctx
        self.turn = ctx.turn
        self.cancel = ctx.cancel
        self.chats = Conversations(ctx.store)
        self.turns = Turns(ctx.store)
        self.proposals = Proposals(ctx.store)
        self.totals = _Totals()
        self.tainted = False
        self.book = SourceBook()
        self._began = time.monotonic()
        self._seq = 0
        self._text_buffer = ""
        self._text_at = time.monotonic()
        self._text_step = 0
        self._answer = ""
        self._last_text = ""
        self._ended = False
        # Set by _converse; the end of a turn reads them, so they exist before anything can fail.
        self.settings: Settings | None = None
        self.masker = Masker(mask_personal=True)
        self.token: AssistantToken | None = None
        self.actor: Any = None
        self._meter: UsageMeter | None = None
        self._toolbox: Toolbox | None = None
        self.provenance = Provenance()
        self._issuer: AssistantIdentity | None = None

    @property
    def meter(self) -> UsageMeter:
        """The meter of the limits; set when the turn begins."""
        if self._meter is None:
            raise RuntimeError("The turn has not begun")
        return self._meter

    @property
    def toolbox(self) -> Toolbox:
        """The tools of this turn; set when the turn begins."""
        if self._toolbox is None:
            raise RuntimeError("The turn has not begun")
        return self._toolbox

    @property
    def issuer(self) -> AssistantIdentity:
        """The issuer of the assistant's tokens; set when the turn begins."""
        if self._issuer is None:
            raise RuntimeError("The turn has not begun")
        return self._issuer

    # -- the end of every turn ---------------------------------------------------------------------

    def run(self) -> None:
        """
        Make the turn. However it ends, the turn's record says how, and the person is told.

        What goes wrong that is expected (the provider, the limits, a store that cannot be written)
        is handled here. A bug is recorded as a failed turn and raised again to the thread.
        """
        try:
            with bind(self.ctx.scope):
                self._converse()
            self._end("done")
        except _Stopped as stop:
            self._end("stopped", stop=stop.sentence)
        except _Cancelled:
            self._end("cancelled")
        except ProviderError as exc:
            if exc.code == "assistant.provider.cancelled" or self.cancel.is_set():
                self._ended_early()
            else:
                self._end("failed", error=_provider_error(exc))
        except (
            AssistantKeyError,
            NoustError,
            sqlite3.Error,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            logger.exception("A turn of the assistant failed")
            self._end("failed", error=_failure(str(exc)))
        finally:
            if not self._ended:
                # A bug of ours, which no handler above is for. The person is told the turn failed and
                # the turn does not stay "running" for ever, but nothing is caught here: the exception
                # goes on to the thread, which reports it, so a defect is never a quiet warning.
                kind = sys.exc_info()[0]
                logger.error("A turn of the assistant failed unexpectedly", exc_info=True)
                self._end("failed", error=_failure(kind.__name__ if kind else "unexpected error"))

    def _ended_early(self) -> None:
        """End a turn that was asked to stop: cancelled by the person, or stopped because it was turned off."""
        try:
            self._on()
        except _Stopped as stop:
            self._end("stopped", stop=stop.sentence)
        else:
            self._end("cancelled")

    def _end(
        self,
        state: str,
        *,
        stop: ServerText | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """
        Leave the turn in its final state, say so, and write it on the audit trail.

        Args:
            state: ``done``, ``failed``, ``cancelled`` or ``stopped``.
            stop: The limit or the switch that ended it.
            error: What went wrong with the provider or with the turn.
        """
        self._ended = True
        self._flush_text(force=True)
        turn = self.turn
        turn.state = state
        turn.stop = stop.to_dict() if stop is not None else None
        turn.error = error
        turn.answer = self._answer or self._last_text
        turn.citations = resolve_citations(turn.answer, self.book, lambda text: text)
        turn.usage = {**turn.usage, **self._usage_numbers()}
        try:
            self.turns.save(turn)
        except (sqlite3.Error, NoustError) as exc:
            logger.warning("The end of a turn of the assistant could not be kept: %s", exc)
        if stop is not None and stop.code.startswith("assistant.limit"):
            self._audit(
                "assistant.limit",
                outcome="denied",
                details={"limit": stop.code, "turn": turn.id[:12]},
            )
        self._audit(
            "assistant.summary",
            details={
                "state": state,
                "steps": self.totals.steps,
                "tokens": self.totals.tokens,
                "spend": round(self.totals.spend, 6),
                "tools": sorted(set(self.totals.tools)),
                "proposals": len(
                    [
                        p
                        for p in self._safe_proposals()
                        if p.turn_id == turn.id and p.klass != "alone"
                    ]
                ),
                "turn": turn.id[:12],
            },
        )
        proposals = self._safe_proposals()
        self._emit(
            "end",
            turn=present_turn(turn, proposals, self.masker.restore),
        )

    def _safe_proposals(self) -> list[Proposal]:
        """
        Returns:
            The conversation's proposals; none when the store cannot be read.
        """
        try:
            return self.proposals.for_conversation(self.ctx.conversation_id, self.ctx.account.id)
        except (sqlite3.Error, NoustError):
            return []

    # -- announcing --------------------------------------------------------------------------------

    def _emit(self, kind: str, **payload: Any) -> None:
        """
        Args:
            kind: The type of announcement.
            **payload: What it carries.
        """
        self._seq += 1
        try:
            self.ctx.publish(
                {
                    "conversation": self.ctx.conversation_id,
                    "turn": self.turn.id,
                    "seq": self._seq,
                    "type": kind,
                    **payload,
                }
            )
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("An announcement of the assistant could not be made: %s", exc)

    def _text(self, piece: str, *, step: int = 0) -> None:
        """
        Gather what the model says, and announce it in pieces large enough to be read.

        Args:
            piece: What it just said, safe to show.
            step: Which request to the provider it belongs to.
        """
        if not piece:
            return
        self._text_buffer += piece
        self._text_step = step
        self._flush_text()

    def _flush_text(self, *, force: bool = False) -> None:
        """
        Announce what is gathered, up to the last word that is whole: a secret that the model wrote is
        never cut in two between announcements, where no removal could know it.

        Args:
            force: Announce what is gathered whatever its size.
        """
        if not self._text_buffer:
            return
        now = time.monotonic()
        if not (
            force or len(self._text_buffer) >= FLUSH_CHARS or now - self._text_at >= FLUSH_SECONDS
        ):
            return
        if force:
            cut = len(self._text_buffer)
        else:
            cut = max(self._text_buffer.rfind(" "), self._text_buffer.rfind("\n")) + 1
        if cut <= 0:
            return
        delta, self._text_buffer = self._text_buffer[:cut], self._text_buffer[cut:]
        self._text_at = now
        self._emit("text", delta=self.masker.scrub_secrets(delta), step=self._text_step)

    def _audit(
        self, event: str, *, outcome: str = "ok", details: Mapping[str, Any] | None = None
    ) -> None:
        """
        Args:
            event: An event of the assistant.
            outcome: How it ended.
            details: Context; never an argument's value.
        """
        try:
            audit_record(
                event,
                actor=self.actor,
                target=f"conversation:{self.ctx.conversation_id[:8]}",
                outcome=outcome,
                details=dict(details or {}),
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("The audit trail could not take an event of the assistant: %s", exc)

    # -- the turn ----------------------------------------------------------------------------------

    def _on(self) -> Settings:
        """
        Returns:
            The settings, now.

        Raises:
            _Stopped: The assistant was turned off.
        """
        settings = AssistantSettings(self.ctx.store).read()
        if not settings.enabled:
            raise _Stopped(off_sentence())
        return settings

    def _converse(self) -> None:
        """Ask, read and answer, until the model has said its last word."""
        ctx = self.ctx
        settings = self.settings = self._on()
        key = AssistantSettings(ctx.store).api_key()
        client = (ctx.client_factory or default_client)(settings, key)
        self.masker = Masker.for_server(
            mask_personal=settings.mask_personal_effective,
            api_key=key,
            markers=self.chats.markers(ctx.conversation_id, ctx.account.id),
        )
        meter = UsageMeter(settings.limits, settings.prices, settings.currency, store=ctx.store)
        identity = ctx.identity or AssistantIdentity()
        self.token = ctx.tokens.get(identity, ctx.account, ctx.conversation_id)
        self.actor = assistant_actor(ctx.account, self.token.name)
        self._issuer = identity
        toolbox = Toolbox(
            ctx.openapi,
            token_permissions=frozenset(self.token.permissions),
            person_permissions=ctx.person_permissions,
            reach=ctx.reach,
            docs=ctx.docs if ctx.docs is not None else shared_docs(),
        )
        self._toolbox = toolbox
        offered = bool(settings.calls_tools)
        earlier = [
            t
            for t in self.turns.for_conversation(ctx.conversation_id, ctx.account.id)
            if t.id != self.turn.id
        ]
        self.tainted = any(step.get("outside") for t in earlier for step in t.steps)
        self.provenance = Provenance(
            [*(t.question for t in earlier), ctx.question],
            known=known_names(ctx.store),
        )
        self._meter = meter
        self._emit(
            "start",
            model=self.turn.model,
            location=self.turn.location,
            server=self.turn.server,
            page=self.turn.page,
        )

        user = self._user_message()
        self.chats.append(ctx.conversation_id, ctx.account.id, "user", user)
        prompt = system_prompt(
            tools=offered,
            docs=any(name == "search_docs" for name in toolbox.names),
            server=ctx.reach.server,
            hub=ctx.hub,
            servers=ctx.reach.servers,
            locale=ctx.locale,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.masker.mask(prompt)},
            *history_messages(self.chats.messages(ctx.conversation_id, ctx.account.id)),
        ]
        tools = toolbox.definitions() if offered else []
        step = 0
        while True:
            self._check()
            verdict = meter.check_step(
                ctx.conversation_id,
                ctx.account.id,
                step=step,
                model=settings.model,
                pending_input_tokens=estimate_tokens(messages) + estimate_tokens(tools),
            )
            self._warn(verdict.warnings)
            self._notes(verdict.notes)
            if verdict.stop is not None:
                raise _Stopped(verdict.stop)
            done = self._model_step(client, messages, tools, step)
            cost = meter.record_step(
                ctx.conversation_id, ctx.account.id, settings.model, done.usage
            )
            self._count(done.usage, cost.cost)
            step += 1
            # A model that was not offered tools and calls one anyway is not obeyed: what it was not given is not
            # a door, whatever it says.
            made = done.tool_calls if offered else ()
            calls = [
                {"id": call.id, "name": call.name, "arguments": call.arguments} for call in made
            ]
            self.chats.append(
                ctx.conversation_id,
                ctx.account.id,
                "assistant",
                done.text,
                tool_calls=calls or None,
            )
            if done.text:
                self._last_text = done.text
            if not made:
                self._answer = done.text
                return
            messages.append(assistant_message(done))
            for call in made:
                self._check()
                result = self._tool(call)
                self.chats.append(
                    ctx.conversation_id,
                    ctx.account.id,
                    "tool",
                    result.text,
                    tool_call_id=call.id,
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
                self.totals.tools.append(call.name[:60])

    def _check(self) -> None:
        """
        Raises:
            _Cancelled: The person asked to stop.
            _Stopped: The assistant was turned off.
        """
        self._on()
        if self.cancel.is_set():
            raise _Cancelled
        if time.monotonic() - self._began > TURN_SECONDS:
            raise _Stopped(say("assistant.turn.too_long", minutes=TURN_SECONDS // 60))

    def _count(self, usage: Usage, cost: float | None) -> None:
        """
        Args:
            usage: What a step used.
            cost: What it cost, when the model has a price.
        """
        self.totals.steps += 1
        self.totals.tokens += usage.total_tokens
        self.totals.spend += cost or 0.0
        self._emit("usage", usage=self._usage_numbers())

    def _usage_numbers(self) -> dict[str, Any]:
        """
        Returns:
            What the turn has used, and the conversation up to now.
        """
        numbers: dict[str, Any] = {
            "steps": self.totals.steps,
            "tokens": self.totals.tokens,
            "spend": round(self.totals.spend, 6),
        }
        if self._meter is None:
            return numbers
        try:
            report = self.meter.report(
                conversation_id=self.ctx.conversation_id, account_id=self.ctx.account.id
            )
        except (sqlite3.Error, NoustError):
            return numbers
        if report.conversation is not None:
            numbers["conversation"] = {
                "tokens": report.conversation.prompt_tokens,
                "spend": round(report.conversation.spend, 6),
                "steps": report.conversation.steps,
            }
        numbers["currency"] = report.currency
        numbers["actions_last_hour"] = report.actions_last_hour
        return numbers

    def _warn(self, sentences: Sequence[ServerText]) -> None:
        """
        Args:
            sentences: Limits at 80 % or more.
        """
        known = {w.get("code") for w in self.turn.warnings}
        for sentence in sentences:
            if sentence.code not in known:
                self.turn.warnings.append(sentence.to_dict())
                known.add(sentence.code)
                self._emit("warning", warning=coded(sentence))

    def _notes(self, sentences: Sequence[ServerText]) -> None:
        """
        Args:
            sentences: What the person should know of the numbers (no price, an estimate).
        """
        self.turn.usage["notes"] = [s.to_dict() for s in sentences]

    # -- the model ---------------------------------------------------------------------------------

    def _user_message(self) -> str:
        """
        Returns:
            What is said to the model this turn, masked: what became of the proposals since it last
            heard, the question and what Noust read of the page.
        """
        ctx = self.ctx
        parts: list[str] = []
        told = self._updates()
        if told:
            parts.append(told)
        parts.append(self.masker.mask(ctx.question))
        if ctx.page is not None:
            block = self._page_block(ctx.page)
            if block:
                parts.append(block)
        return "\n\n".join(parts)

    def _updates(self) -> str:
        """
        Returns:
            What became of the calls the assistant proposed, which the model has not heard yet,
            in Noust's words and the system's.
        """
        for held in self.proposals.for_conversation(self.ctx.conversation_id, self.ctx.account.id):
            followed(held, self.ctx.account.id, proposals=self.proposals, persist=True)
        pending = self.proposals.untold(self.ctx.conversation_id, self.ctx.account.id)
        if not pending:
            return ""
        lines = ["Since your last answer, this is what the person did with what you proposed:"]
        for proposal in pending:
            title = say(proposal.title["code"], **proposal.title.get("params", {})).text
            line = f"- {title}: {_ended(proposal)}"
            if proposal.output:
                line += "\n" + mark_as_data(
                    self.masker.mask(proposal.output), "what the system said"
                )
            lines.append(line)
        self.proposals.mark_told([p.id for p in pending])
        return "<proposal-updates>\n" + "\n".join(lines) + "\n</proposal-updates>"

    def _page_block(self, page: dict[str, Any]) -> str:
        """
        Read what a page shows, for the model, and say so as steps.

        Args:
            page: The page the question is asked from, judged.

        Returns:
            The text that goes after the question.
        """
        pieces = [f"The person asks from {page_context.describe(page)}."]
        for tool, arguments in page_context.reads_for(page):
            self._check()
            try:
                prepared = self.toolbox.prepare(tool, self._page_arguments(arguments, page))
            except ToolProblem as problem:
                pieces.append(f"(Noust could not read it: {problem})")
                continue
            pieces.append(self._read_step(prepared).text)
        return f"<{CONTEXT_TAG}>\n" + "\n".join(pieces) + f"\n</{CONTEXT_TAG}>"

    def _page_arguments(self, arguments: dict[str, Any], page: Mapping[str, Any]) -> dict[str, Any]:
        """
        Args:
            arguments: What a page's reading takes.
            page: The page.

        Returns:
            The arguments, with the server when the person is looking at all of them.
        """
        if self.ctx.reach.server == "*":
            chosen = page.get("server")
            return {**arguments, **({"server": chosen} if chosen else {})}
        return arguments

    def _model_step(
        self,
        client: ProviderClient,
        messages: list[dict[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        step: int,
    ) -> Completion:
        """
        Ask the model once and announce what it says as it says it.

        Args:
            client: The provider's client.
            messages: The conversation, masked.
            tools: The tools on offer.
            step: Which request of the turn this is.

        Returns:
            What it said and the calls it made.
        """
        said: list[str] = []
        calls: list[ToolCall] = []
        finish = Finish("stop", Usage(0, 0, estimated=True))
        shown = StreamFilter(self.masker.restore)
        for chunk in client.stream(messages, tools, cancel=self.cancel):
            if isinstance(chunk, TextDelta):
                said.append(chunk.text)
                self._text(shown.feed(chunk.text), step=step)
            elif isinstance(chunk, ToolCall):
                calls.append(chunk)
            else:
                finish = chunk
        self._text(shown.flush(), step=step)
        self._flush_text(force=True)
        return Completion(
            self.masker.scrub_secrets("".join(said)), tuple(calls), finish.reason, finish.usage
        )

    # -- a tool ------------------------------------------------------------------------------------

    def _tool(self, call: ToolCall) -> _Result:
        """
        Make what the model asked for, or say why not.

        Args:
            call: A call the model made.

        Returns:
            What the model is told: the answer, or what is wrong, in words it can act on.
        """
        try:
            arguments = self.masker.restore_value(call.arguments_object())
        except MalformedArguments as problem:
            self._audit(
                "assistant.tool",
                outcome="denied",
                details={"tool": call.name[:60], "reason": "arguments"},
            )
            return _Result(str(problem), ok=False, outcome="denied")
        try:
            prepared = self.toolbox.prepare(call.name, arguments)
        except ToolRefused as refused:
            return self._refused_by_name(call, refused)
        except ToolProblem as problem:
            self._audit(
                "assistant.tool",
                outcome="denied",
                details={"tool": call.name[:60], "reason": "invalid"},
            )
            return _Result(str(problem), ok=False, outcome="denied")
        if prepared.kind != "api":
            return self._search(prepared)
        if prepared.read_only:
            return self._read_step(prepared)
        return self._change(prepared)

    def _refused_by_name(self, call: ToolCall, refused: ToolRefused) -> _Result:
        """
        Say that an operation the model named is not offered: to the person as well as to the model.

        Args:
            call: The call.
            refused: What it was and why not.

        Returns:
            What the model is told.
        """
        title = {"code": refused.title_code, "params": refused.title_params}
        step: dict[str, Any] = {
            "id": f"s{len(self.turn.steps) + 1}",
            "tool": call.name[:60],
            "what": title,
            "server": "",
            "state": "refused",
            "outside": False,
        }
        self.turn.steps.append(step)
        self.turn.not_offered.append(
            {
                "title": title,
                "why": {"code": refused.why_code, "params": {}},
                "floor": refused.floor,
            }
        )
        self._save_turn()
        self._emit("step", step=_shown_step(step))
        self._audit(
            "assistant.tool",
            outcome="denied",
            details={"tool": call.name[:60], "reason": "not offered"},
        )
        return _Result(str(refused), ok=False, outcome="denied")

    def _step(self, prepared: Prepared) -> dict[str, Any]:
        """
        Begin a step: what the assistant is looking at, announced.

        Args:
            prepared: The call.

        Returns:
            The step, running.
        """
        step: dict[str, Any] = {
            "id": f"s{len(self.turn.steps) + 1}",
            "tool": prepared.name,
            "what": {"code": prepared.step_code, "params": prepared.step_params},
            "server": prepared.server,
            "state": "running",
            "outside": prepared.outside,
        }
        self.turn.steps.append(step)
        self._save_turn()
        self._emit("step", step=_shown_step(step))
        return step

    def _finish_step(self, step: dict[str, Any], state: str, **extra: Any) -> None:
        """
        Args:
            step: A step.
            state: ``done``, ``failed``, ``refused`` or ``proposed``.
            **extra: What it came to (``size``, ``masked``, ``error``).
        """
        step.update({"state": state, **extra})
        self._save_turn()
        self._emit("step", step=_shown_step(step))

    def _save_turn(self) -> None:
        """Keep the turn as it is, so a person who comes back mid-turn sees what has happened."""
        try:
            self.turns.save(self.turn)
        except (sqlite3.Error, NoustError) as exc:
            logger.warning("A turn of the assistant could not be kept: %s", exc)

    def _search(self, prepared: Prepared) -> _Result:
        """
        Args:
            prepared: A search of the documentation or of the operations.

        Returns:
            Its result for the model.
        """
        step = self._step(prepared)
        if prepared.kind == "docs":
            text, count = self.toolbox.search_docs(prepared.query_text)
        else:
            text, count = self.toolbox.find_operation(prepared.query_text)
        masked = self.masker.mask(text)
        self._finish_step(
            step,
            "done",
            size={"unit": "results", "count": count},
            masked=_hidden(text, masked),
        )
        self._audit("assistant.tool", details={"tool": prepared.name})
        self._audit(
            "assistant.result", details={"tool": prepared.name, "ok": True, "size": len(masked)}
        )
        return _Result(guardrails.limit(masked, RESULT_CHARS))

    # -- a read ------------------------------------------------------------------------------------

    def _call(self, prepared: Prepared, *, request_id: str | None = None) -> Any:
        """
        Make a call to the API as the assistant.

        Args:
            prepared: The call.
            request_id: What the audit trail joins it by.

        Returns:
            The API's answer.

        Raises:
            _Stopped: The assistant was turned off (its token was taken back).
        """
        assert self.token is not None  # noqa: S101 - set before any tool runs
        corr = request_id or f"as-{self.turn.id[:16]}"
        for attempt in (0, 1):
            response = self.ctx.bridge.call(
                prepared.method,
                prepared.path,
                token=self.token.token,
                query=prepared.query,
                body=prepared.body,
                request_id=corr,
            )
            if response.status != 401:
                return response
            self._on()
            if attempt == 0:
                self.ctx.tokens.drop(self.ctx.conversation_id)
                self.token = self.ctx.tokens.get(
                    self.issuer, self.ctx.account, self.ctx.conversation_id, fresh=True
                )
        return response

    def _read_step(self, prepared: Prepared) -> _Result:
        """
        Read something for the model, as a step.

        Args:
            prepared: A read.

        Returns:
            What the model is given.
        """
        step = self._step(prepared)
        decision = self._decide(prepared, foreign=[])
        if decision.action == REFUSE:
            return self._refused(prepared, step, decision)
        response = self._call(prepared)
        decoded = response.json()
        if response.status >= 400:
            return self._refusal_of(prepared, step, response.status, decoded)
        text, hidden, size = self._shape(prepared, decoded, step)
        self._finish_step(step, "done", size=size, masked=hidden)
        if prepared.outside:
            self.tainted = True
        self._audit("assistant.tool", details=_tool_details(prepared))
        self._audit(
            "assistant.result", details={"tool": prepared.name, "ok": True, "size": len(text)}
        )
        return _Result(text)

    def _shape(
        self, prepared: Prepared, decoded: Any, step: dict[str, Any]
    ) -> tuple[str, int, dict[str, Any]]:
        """
        Turn the API's answer into what the model reads: masked, bounded and marked.

        Args:
            prepared: The call.
            decoded: What the API answered.
            step: The step it belongs to.

        Returns:
            The text, how many values that looked like secrets were hidden, and how much was read.
        """
        shaped = prepared.shape(decoded) if prepared.shape is not None else decoded
        label = f"{prepared.name}{' of ' + prepared.domain if prepared.domain else ''}"
        if prepared.log or isinstance(shaped, str):
            raw = str(shaped)
            lines = raw.splitlines()
            kept = _tail_lines(lines, LOG_CHARS, per_line=len(str(len(lines))) + 3)
            first = len(lines) - len(kept) + 1
            masked = self.masker.mask("\n".join(kept), domain=prepared.domain)
            hidden = _hidden("\n".join(kept), masked)
            source = self.book.add("log", step["what"], masked, first_line=first)
            if prepared.outside:
                self.provenance.add_outside(source.id, sorted(source.lines.items()))
            cut = f" (the end of the text: lines {first} to {len(lines)})" if first > 1 else ""
            body = f"{source.id}{cut}\n{self.book.render(source.id)}"
            size = {"unit": "lines", "count": len(kept)}
        else:
            safe = self.masker.mask_value(shaped, domain=prepared.domain)
            raw_json = json.dumps(shaped, indent=1, ensure_ascii=False, default=str)
            body = guardrails.limit(
                json.dumps(safe, indent=1, ensure_ascii=False, default=str), RESULT_CHARS
            )
            hidden = _hidden(raw_json, body)
            size = _size_of(shaped, body)
            if prepared.outside:
                source = self.book.add("output", step["what"], body)
                self.provenance.add_outside(source.id, sorted(source.lines.items()))
                body = f"{source.id}\n{self.book.render(source.id)}"
        return (mark_as_data(body, label) if prepared.outside else body), hidden, size

    def _refusal_of(
        self, prepared: Prepared, step: dict[str, Any], status: int, decoded: Any
    ) -> _Result:
        """
        Args:
            prepared: The call that was refused.
            step: Its step.
            status: The API's status.
            decoded: Its error body.

        Returns:
            What the model is told: the server's own words.
        """
        if prepared.server and status == 404 and _route_missing(decoded):
            text = f"The server {prepared.server} does not have this: it may run an older version of Noust."
        else:
            text = guardrails.refusal_text(status, decoded)
        masked = self.masker.mask(text, domain=prepared.domain)
        self._finish_step(step, "failed", error=masked[:600])
        self._audit(
            "assistant.tool",
            outcome="denied" if status in (401, 403, 404) else "failure",
            details={**_tool_details(prepared), "status": status},
        )
        return _Result(masked, ok=False, outcome="failure")

    # -- a change ----------------------------------------------------------------------------------

    def _second_person(self, prepared: Prepared) -> bool:
        """
        Args:
            prepared: A change.

        Returns:
            Whether a second person has to approve this very call (the four-eyes rule is on and covers it).
        """
        from noust.web.api.approvals import approvals

        if not approvals().policy().enabled:
            return False
        params = {
            name: str(prepared.arguments.get(name, ""))
            for name in _PATH_PARAMETER.findall(prepared.template)
        }
        return (
            rule_for(prepared.method, prepared.template, prepared.needed, prepared.body, params)
            is not None
        )

    def _decide(self, prepared: Prepared, *, foreign: Sequence[Foreign]) -> Decision:
        """
        Args:
            prepared: A call.
            foreign: The values of it that are not the person's or Noust's.

        Returns:
            What to do with it.
        """
        if prepared.operation is None:
            return Decision(
                REFUSE,
                REFUSED,
                "not_offered",
                Verdict("never", "unclassified", "assistant", "production"),
                floor=True,
            )
        changes = prepared.operation.changes
        actions_ok = True
        if changes:
            actions_ok = self.meter.check_action(self.ctx.account.id).allowed
        return decide(
            prepared.operation,
            prepared.needed,
            environment=environment_for(prepared.domain, proxied=bool(prepared.server)),
            foreign=bool(foreign),
            tainted=self.tainted,
            second_person=self._second_person(prepared) if changes else False,
            actions_allowed=actions_ok,
        )

    def _change(self, prepared: Prepared) -> _Result:
        """
        Do what the policy lets the assistant do on its own, propose the rest, and refuse what is not offered.

        Args:
            prepared: A call that changes something.

        Returns:
            What the model is told.
        """
        step = self._step(prepared)
        if hide_secrets(prepared.arguments) != prepared.arguments:
            return self._refused_for(
                prepared, step, "assistant.refused.secret_value", {}, floor=True
            )
        foreign = self.provenance.foreign(prepared.arguments)
        decision = self._decide(prepared, foreign=foreign)
        if decision.action == REFUSE:
            return self._refused(prepared, step, decision)
        if decision.action == PROPOSE:
            if decision.why == "actions_limit":
                self._audit("assistant.limit", outcome="denied", details={"limit": "actions"})
            return self._propose(prepared, step, decision, foreign)
        return self._do(prepared, step, decision, foreign)

    def _environment(self, prepared: Prepared) -> str:
        """
        Args:
            prepared: A call.

        Returns:
            The environment of its resource.
        """
        return environment_for(prepared.domain, proxied=bool(prepared.server))

    def _facts(
        self, prepared: Prepared, foreign: Sequence[Foreign], environment: str
    ) -> list[dict[str, Any]]:
        """
        Args:
            prepared: A call.
            foreign: The values in it that are not the person's or Noust's.
            environment: Where the resource is.

        Returns:
            The values of the call as the card shows them, with where each foreign one came from.
        """
        origin = {f.path: f for f in foreign}
        facts: list[dict[str, Any]] = []
        for name, value in _flatten(prepared.arguments):
            fact: dict[str, Any] = {"name": name, "value": value}
            if name in origin:
                found = origin[name]
                source = self.book.get(found.origin.source) if found.origin.source else None
                fact["foreign"] = {
                    "kind": found.kind,
                    "source": found.origin.source,
                    "line": found.origin.line,
                    "label": source.label if source is not None else None,
                }
            facts.append(fact)
            if len(facts) >= _MAX_FACTS:
                break
        if prepared.server:
            facts.append({"name": "server", "value": prepared.server})
        facts.append({"name": "environment", "value": environment})
        return facts

    def _plan(self, prepared: Prepared) -> dict[str, Any] | None:
        """
        Args:
            prepared: A call whose operation may publish what it would do before it does it.

        Returns:
            What the operation's simulation says, masked; None when it has none or it could not be read.
        """
        if not prepared.plan:
            return None
        method, _, template = prepared.plan.partition(" ")
        path = template
        for name in _PATH_PARAMETER.findall(template):
            if name not in prepared.arguments:
                return None
            path = path.replace("{" + name + "}", _quote(str(prepared.arguments[name])))
        if prepared.server:
            path = proxy_path(prepared.server, path)
        assert self.token is not None  # noqa: S101 - set before any tool runs
        # A plan is what a proposal shows when it has one: a proposal without it is still the exact
        # call, so nothing that goes wrong reading a plan stops the turn.
        try:
            response = self.ctx.bridge.attempt(
                method,
                path,
                token=self.token.token,
                body=prepared.body if method != "GET" else None,
                request_id=f"as-{self.turn.id[:16]}",
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.info("The plan of %s could not be read: %s", prepared.template, exc)
            return None
        if response is None:
            return None
        decoded = response.json()
        if response.status >= 400 or not isinstance(decoded, dict):
            return None
        safe = self.masker.mask_value(decoded)
        return safe if len(json.dumps(safe, default=str)) <= 8_000 else None

    def _propose(
        self,
        prepared: Prepared,
        step: dict[str, Any],
        decision: Decision,
        foreign: Sequence[Foreign],
    ) -> _Result:
        """
        Keep the call, exactly, for the person to carry out.

        Args:
            prepared: The call.
            step: Its step.
            decision: What was decided.
            foreign: The values in it that are not the person's or Noust's.

        Returns:
            What the model is told: that it is NOT done.
        """
        ctx = self.ctx
        environment = self._environment(prepared)
        raw = json.dumps(prepared.body).encode() if prepared.body is not None else b""
        fingerprint, _shown = snapshot(prepared.method, prepared.path, list(prepared.query), raw)
        title = {"code": prepared.step_code, "params": prepared.step_params}
        proposal = self.proposals.create(
            ctx.conversation_id,
            self.turn.id,
            ctx.account.id,
            tool=prepared.name,
            klass=decision.klass,
            method=prepared.method,
            path=prepared.path,
            query=[list(pair) for pair in prepared.query],
            body=prepared.body,
            fingerprint=fingerprint,
            permission=prepared.needed[0] if prepared.needed else "",
            environment=environment,
            server=prepared.server,
            title=title,
            effect=prepared.summary,
            why={"code": WHY_CODES[decision.why], "params": {}},
            facts=self._facts(prepared, foreign, environment),
            plan=self._plan(prepared),
            needs={
                "elevation": bool(prepared.operation and prepared.operation.elevation),
                "second_person": decision.klass == "two",
            },
        )
        self._finish_step(step, "proposed", proposal=proposal.id)
        self._emit("proposal", proposal=present_proposal(proposal))
        self._audit("assistant.tool", outcome="proposed", details=_tool_details(prepared))
        self._audit(
            "assistant.proposal",
            details={
                **_tool_details(prepared),
                "class": decision.klass,
                "proposal": proposal.id[:12],
            },
        )
        words = say(prepared.step_code, **prepared.step_params).text
        how = {
            "ask": "they see the exact call and approve it",
            "you": "it also asks them to confirm it is them (sudo mode), so they do it in their own session",
            "two": "it also waits for a second person to approve it",
        }[decision.klass]
        text = (
            f"Proposed, NOT done: {words}. It waits for the person: {how}. Do not make it again and "
            "do not say it is done: tell the person what it will do and that it waits for them."
        )
        if foreign:
            text += " One value in it is not from the person or from Noust: it is shown to them highlighted."
        return _Result(text, outcome="proposed")

    def _do(
        self,
        prepared: Prepared,
        step: dict[str, Any],
        decision: Decision,
        foreign: Sequence[Foreign],
    ) -> _Result:
        """
        Make a change the policy lets the assistant make on its own.

        Args:
            prepared: The call.
            step: Its step.
            decision: What was decided (``alone``).
            foreign: Nothing: a foreign value makes it a proposal.

        Returns:
            What the model is told.
        """
        ctx = self.ctx
        response = self._call(prepared)
        decoded = response.json()
        environment = self._environment(prepared)
        title = {"code": prepared.step_code, "params": prepared.step_params}
        held = (
            response.status == 202
            and isinstance(decoded, dict)
            and decoded.get("error") == "approval_required"
        )
        base: dict[str, Any] = {
            "tool": prepared.name,
            "method": prepared.method,
            "path": prepared.path,
            "query": [list(pair) for pair in prepared.query],
            "body": prepared.body,
            "fingerprint": snapshot(
                prepared.method,
                prepared.path,
                list(prepared.query),
                json.dumps(prepared.body).encode() if prepared.body is not None else b"",
            )[0],
            "permission": prepared.needed[0] if prepared.needed else "",
            "environment": environment,
            "server": prepared.server,
            "title": title,
            "effect": prepared.summary,
            "facts": self._facts(prepared, foreign, environment),
            "plan": None,
            "needs": {"elevation": False, "second_person": False},
        }
        if held:
            approval = str((decoded.get("fields") or {}).get("approval") or "")
            proposal = self.proposals.create(
                ctx.conversation_id,
                self.turn.id,
                ctx.account.id,
                klass="ask",
                why={"code": WHY_CODES["policy_propose"], "params": {}},
                approval=int(approval) if approval.isdigit() else None,
                **base,
            )
            self._finish_step(step, "proposed", proposal=proposal.id)
            self._emit("proposal", proposal=present_proposal(proposal))
            self._audit("assistant.tool", outcome="proposed", details=_tool_details(prepared))
            return _Result(
                "Not done: the server holds this call for a person to approve. Do not make it again; "
                "tell the person it waits for them.",
                outcome="proposed",
            )
        if response.status >= 400:
            output = self.masker.mask(
                guardrails.refusal_text(response.status, decoded), domain=prepared.domain
            )
            proposal = self.proposals.create(
                ctx.conversation_id,
                self.turn.id,
                ctx.account.id,
                klass="alone",
                state="failed",
                status=response.status,
                output=output,
                decided_by="assistant",
                why={"code": WHY_CODES["policy_alone"], "params": {}},
                **base,
            )
            self._finish_step(step, "failed", proposal=proposal.id, error=output[:600])
            self._emit("proposal", proposal=present_proposal(proposal))
            self._audit(
                "assistant.tool",
                outcome="denied" if response.status in (401, 403, 404) else "failure",
                details={**_tool_details(prepared), "status": response.status},
            )
            return _Result(f"It did not work. {output}", ok=False, outcome="failure")
        safe = self.masker.mask_value(decoded, domain=prepared.domain)
        shown = json.dumps(safe, indent=1, ensure_ascii=False, default=str)
        self.meter.record_action(ctx.conversation_id, ctx.account.id, prepared.name)
        proposal = self.proposals.create(
            ctx.conversation_id,
            self.turn.id,
            ctx.account.id,
            klass="alone",
            state="done",
            status=response.status,
            result=safe if len(shown) <= 4_000 else {"truncated": shown[:4_000]},
            decided_by="assistant",
            why={"code": WHY_CODES["policy_alone"], "params": {}},
            **base,
        )
        self._finish_step(step, "done", proposal=proposal.id)
        self._emit("proposal", proposal=present_proposal(proposal))
        self._audit("assistant.tool", details=_tool_details(prepared))
        self._audit(
            "assistant.result", details={"tool": prepared.name, "ok": True, "size": len(shown)}
        )
        return _Result(
            f"Done, as the person's policy lets you do. The server answered:\n{guardrails.limit(shown, 4_000)}"
        )

    def _refused(self, prepared: Prepared, step: dict[str, Any], decision: Decision) -> _Result:
        """
        Say that something is not offered: to the model, to the person and to the audit trail.

        Args:
            prepared: The call.
            step: Its step.
            decision: The refusal.

        Returns:
            What the model is told.
        """
        from noust.web.api.caller_gate import never_sentence

        if decision.why == "not_offered":
            return self._refused_for(
                prepared, step, "assistant.refused.not_offered", {}, floor=True
            )
        sentence = never_sentence(decision.verdict)
        return self._refused_for(
            prepared, step, sentence.code, dict(sentence.params), floor=decision.floor
        )

    def _refused_for(
        self,
        prepared: Prepared,
        step: dict[str, Any],
        code: str,
        params: Mapping[str, Any],
        *,
        floor: bool,
    ) -> _Result:
        """
        Args:
            prepared: The call.
            step: Its step.
            code: The sentence that says why.
            params: Its values.
            floor: Nobody changes it (the floor), as against a cell of the table.

        Returns:
            What the model is told.
        """
        why = say(code, **params)
        self.turn.not_offered.append(
            {
                "title": {"code": prepared.step_code, "params": prepared.step_params},
                "why": why.to_dict(),
                "floor": floor,
            }
        )
        self._finish_step(step, "refused")
        self._audit("assistant.tool", outcome="denied", details=_tool_details(prepared))
        title = say(prepared.step_code, **prepared.step_params).text
        hint = f" {why.hint}" if why.hint else ""
        return _Result(
            f"Not offered: {title}. {why.text}.{hint} Tell the person plainly that you cannot do this and "
            "who changes it. Do not look for another way to do it.",
            ok=False,
            outcome="denied",
        )


# -- helpers -------------------------------------------------------------------------------------


def _provider_error(exc: ProviderError) -> dict[str, Any]:
    """
    Args:
        exc: What went wrong with the provider.

    Returns:
        It as a turn keeps it: the sentence, and the provider's own words as it said them.
    """
    return {**exc.sentence.to_dict(), "text": exc.sentence.text, "output": exc.sentence.text}


def _failure(reason: str) -> dict[str, Any]:
    """
    Args:
        reason: What went wrong.

    Returns:
        A turn's error for what is not the provider's.
    """
    sentence = say("assistant.turn.failed", reason=reason[:300])
    return {**sentence.to_dict(), "text": sentence.text, "output": ""}


def _ended(proposal: Proposal) -> str:
    """
    Args:
        proposal: A proposal the person dealt with.

    Returns:
        How it ended, for the model.
    """
    if proposal.state == "done":
        return "approved and done."
    if proposal.state == "failed":
        return "approved, but it failed. The system said:"
    if proposal.state == "rejected":
        return "rejected; nothing was done."
    if proposal.state == "second":
        return "waits for a second person to approve it."
    return "is being done."


def _tool_details(prepared: Prepared) -> dict[str, Any]:
    """
    Args:
        prepared: A call.

    Returns:
        What the audit trail keeps of it: the tool, the application and the server, never a value.
    """
    details: dict[str, Any] = {"tool": prepared.name}
    if prepared.domain:
        details["domain"] = prepared.domain
    if prepared.server:
        details["server"] = prepared.server
    return details


def _shown_step(step: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        step: A step as the turn keeps it.

    Returns:
        It as the console is told it.
    """
    return {**step, "what": sentence_of(step.get("what"))}


def _hidden(raw: str, masked: str) -> int:
    """
    Args:
        raw: Text before it was masked.
        masked: The same after.

    Returns:
        How many values that looked like secrets were hidden in it.
    """
    return max(masked.count(REDACTED) - raw.count(REDACTED), 0)


def _tail_lines(lines: Sequence[str], longest: int, *, per_line: int = 1) -> list[str]:
    """
    Args:
        lines: The lines of a text.
        longest: The most characters to keep, with what each line is given (its number).
        per_line: What each line takes besides its text.

    Returns:
        The last lines that fit: what happened last is what matters.
    """
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        size += len(line) + per_line
        if size > longest and kept:
            break
        kept.append(line[:longest])
    kept.reverse()
    return kept


def _size_of(shaped: Any, text: str) -> dict[str, Any]:
    """
    Args:
        shaped: What a tool returned.
        text: It as text.

    Returns:
        How much was read: how many things when it is a list, else how many characters.
    """
    if isinstance(shaped, list):
        return {"unit": "items", "count": len(shaped)}
    if isinstance(shaped, Mapping):
        for value in shaped.values():
            if isinstance(value, list):
                return {"unit": "items", "count": len(value)}
    return {"unit": "chars", "count": len(text)}


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """
    Args:
        value: The arguments of a call.
        prefix: Where in them this is.

    Returns:
        ``(path, text)`` for each value in them, as the card shows them.
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for name, item in value.items():
            found.extend(_flatten(item, f"{prefix}.{name}" if prefix else str(name)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_flatten(item, f"{prefix}[{index}]"))
    elif value is not None:
        found.append((prefix, str(value)[:500]))
    return found


def _route_missing(decoded: Any) -> bool:
    """
    Args:
        decoded: A 404's body.

    Returns:
        Whether it is the framework's own "there is no such route" and not Noust's "there is no such thing".
    """
    return (
        isinstance(decoded, dict)
        and decoded.get("detail") == "Not Found"
        and "error" not in decoded
    )


def _quote(value: str) -> str:
    """
    Args:
        value: A value that becomes a segment of a path.

    Returns:
        It quoted as one segment.
    """
    return urllib.parse.quote(value, safe="")
