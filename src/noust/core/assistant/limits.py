# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ceilings a conversation with the built-in assistant runs under, and the meter that keeps them
(item 171).

A model that is let loop with tools can spend a month's budget in an afternoon, so the limits are
never optional: 20 steps in one answer, 400,000 input tokens in a conversation, a spending limit
for a conversation and one for the month, and 10 changes an hour. A value of zero or "no limit"
is refused; the operator (``settings.manage``) changes the number, never removes it. A warning
comes at 80 % of a limit and the stop at all of it, in a sentence that names the limit and who
changes it.

**The meter is asked before every step and told after it.** :meth:`UsageMeter.check_step` is what
the loop calls before each request to the provider; :meth:`UsageMeter.record_step` is what it calls
with the ``usage`` that came back, and never refuses: a step that crossed a limit is paid for and
written down, and the *next* check stops. That is how a limit cuts between steps and never in the
middle of an action. :meth:`UsageMeter.check_action` and :meth:`UsageMeter.record_action` keep the
count of changes per person per hour, which the loop asks before a call that changes something.

**Spending is the tokens the provider reported times a price table the operator writes** (per
million tokens, in the currency they say): prices change and no provider's list is Noust's to
keep. A model with no price has no spending limit and the verdict says so (a note), and the limits
of tokens apply. Without a ``usage`` from the provider the tokens are estimated and the verdict
says that too.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from noust.core.assistant.client import Usage
from noust.core.exceptions import ValidationError
from noust.core.server_text import ServerText, say
from noust.core.timeutil import utc_now

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "WARN_AT",
    "Limits",
    "Price",
    "StepCost",
    "Totals",
    "UsageMeter",
    "UsageReport",
    "Verdict",
    "cost_of",
]

#: The fraction of a limit at which the person is warned.
WARN_AT = 0.8


def _number(name: str, value: Any, low: float, high: float, *, whole: bool) -> float:
    """
    Read one number of a limit, refusing everything that is not a bounded, finite one.

    Args:
        name: The limit's name, for the message.
        value: What was given: a number or a numeric text.
        low: The least allowed.
        high: The most allowed.
        whole: Whether it must be a whole number.

    Returns:
        The number.

    Raises:
        ValidationError: It is missing, a flag, not a number, not finite or out of range. The
            message says "no limit" is not one of the choices.
    """
    problem = ValidationError(
        f"The limit {name} must be a number from {low:g} to {high:g}: a limit is never removed",
        details="Raise or lower it, but give it a value.",
        field=name,
    )
    if isinstance(value, bool) or value is None:
        raise problem
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise problem from None
    if not math.isfinite(number) or not low <= number <= high:
        raise problem
    if whole and number != int(number):
        raise problem
    return number


@dataclass(frozen=True)
class Limits:
    """
    What a conversation may use. Every field has a value, always.

    Attributes:
        steps_per_turn: Requests to the provider in one answer.
        input_tokens_per_conversation: Tokens sent to the provider, added over every step.
        spend_per_conversation: What one conversation may cost, in the operator's currency.
        spend_per_month: What the assistant may cost in a calendar month (UTC), everyone's.
        actions_per_hour: Changes the assistant may make for one person in an hour.
    """

    steps_per_turn: int = 20
    input_tokens_per_conversation: int = 400_000
    spend_per_conversation: float = 2.0
    spend_per_month: float = 20.0
    actions_per_hour: int = 10

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> Limits:
        """
        Read limits, the ones not named staying as they are by default.

        Args:
            values: Names and numbers (numbers may be text, as a command line gives them).

        Returns:
            The limits.

        Raises:
            ValidationError: A name is not a limit, or a value is not an allowed number.
        """
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValidationError(
                f"There is no limit called {', '.join(unknown)}",
                details=f"The limits are: {', '.join(cls.__dataclass_fields__)}.",
            )
        base = cls()
        return cls(
            steps_per_turn=int(
                _number(
                    "steps_per_turn",
                    values.get("steps_per_turn", base.steps_per_turn),
                    1,
                    200,
                    whole=True,
                )
            ),
            input_tokens_per_conversation=int(
                _number(
                    "input_tokens_per_conversation",
                    values.get("input_tokens_per_conversation", base.input_tokens_per_conversation),
                    1_000,
                    100_000_000,
                    whole=True,
                )
            ),
            spend_per_conversation=_number(
                "spend_per_conversation",
                values.get("spend_per_conversation", base.spend_per_conversation),
                0.01,
                1_000_000,
                whole=False,
            ),
            spend_per_month=_number(
                "spend_per_month",
                values.get("spend_per_month", base.spend_per_month),
                0.01,
                10_000_000,
                whole=False,
            ),
            actions_per_hour=int(
                _number(
                    "actions_per_hour",
                    values.get("actions_per_hour", base.actions_per_hour),
                    1,
                    1000,
                    whole=True,
                )
            ),
        )

    def to_dict(self) -> dict[str, float | int]:
        """Returns the limits as the store keeps them."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class Price:
    """
    What a model costs, per million tokens, in the operator's currency.

    Attributes:
        input_per_million: What a million tokens sent cost.
        output_per_million: What a million tokens said cost.
    """

    input_per_million: float
    output_per_million: float

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> Price:
        """
        Args:
            values: ``input_per_million`` and ``output_per_million``.

        Returns:
            The price.

        Raises:
            ValidationError: One is missing, is not a number or is negative.
        """
        for name in ("input_per_million", "output_per_million"):
            if name not in values:
                raise ValidationError(
                    f"A price needs {name}", details="Give both the input and the output price."
                )
        return cls(
            _price("input_per_million", values["input_per_million"]),
            _price("output_per_million", values["output_per_million"]),
        )

    def to_dict(self) -> dict[str, float]:
        """Returns the price as the store keeps it."""
        return {
            "input_per_million": self.input_per_million,
            "output_per_million": self.output_per_million,
        }


def _price(name: str, value: Any) -> float:
    """
    Args:
        name: The field, for the message.
        value: What was given.

    Returns:
        A finite number from 0 to a million.

    Raises:
        ValidationError: It is anything else.
    """
    try:
        return _number(name, value, 0.0, 1_000_000.0, whole=False)
    except ValidationError:
        raise ValidationError(
            f"The price {name} must be a number from 0 to 1000000",
            details="Prices are per million tokens, in the currency of the settings.",
            field=name,
        ) from None


def cost_of(price: Price, usage: Usage) -> float:
    """
    Args:
        price: The model's price.
        usage: What a step used.

    Returns:
        What the step cost.
    """
    return (
        usage.prompt_tokens * price.input_per_million
        + usage.completion_tokens * price.output_per_million
    ) / 1_000_000


@dataclass(frozen=True)
class StepCost:
    """
    What a recorded step cost.

    Attributes:
        usage: What it used.
        cost: What that cost, or None when the model has no price.
    """

    usage: Usage
    cost: float | None


@dataclass(frozen=True)
class Verdict:
    """
    What the meter says before a step.

    Attributes:
        stop: The sentence of the limit that was reached; None when the step may go on.
        warnings: Limits at 80 % or more of their value.
        notes: Things the person should know about the numbers (no price, an estimate).
    """

    stop: ServerText | None = None
    warnings: tuple[ServerText, ...] = ()
    notes: tuple[ServerText, ...] = ()

    @property
    def allowed(self) -> bool:
        """Whether the step may go on."""
        return self.stop is None


@dataclass(frozen=True)
class Totals:
    """
    What a stretch of conversation used.

    Attributes:
        steps: Requests to the provider.
        prompt_tokens: Tokens sent.
        completion_tokens: Tokens said.
        spend: The cost of the steps that have a price.
        unpriced_steps: Steps whose model had no price, which spend leaves out.
        estimated_steps: Steps whose tokens are an estimate.
    """

    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    spend: float = 0.0
    unpriced_steps: int = 0
    estimated_steps: int = 0


@dataclass(frozen=True)
class UsageReport:
    """
    The numbers behind ``GET /api/assistant/usage``.

    Attributes:
        conversation: One conversation's totals, when one was asked for.
        month: The calendar month's totals, of everyone.
        actions_last_hour: The changes the asked-for person made in the last hour; None when no
            person was asked for.
        limits: The limits in force.
        currency: The currency spending is in.
        month_start: The first moment of the month counted, UTC.
        priced_models: The models that have a price.
    """

    conversation: Totals | None
    month: Totals
    actions_last_hour: int | None
    limits: Limits
    currency: str
    month_start: str
    priced_models: tuple[str, ...] = field(default_factory=tuple)


def _near(used: float, limit: float) -> bool:
    """
    Args:
        used: How much was used.
        limit: The limit.

    Returns:
        Whether the use reached the warning point of the limit.
    """
    return used >= limit * WARN_AT


class UsageMeter:
    """The meter of the limits, over the store's ledger."""

    def __init__(
        self,
        limits: Limits,
        prices: Mapping[str, Price],
        currency: str,
        *,
        store: NoustStore | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """
        Args:
            limits: The limits in force.
            prices: What each model costs, by the model's name.
            currency: The currency of the prices and of the spending limits.
            store: The store; the process-wide one when None.
            clock: The moment now, in UTC; for tests.
        """
        self.limits = limits
        self.prices = dict(prices)
        self.currency = currency
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

    def _month_start(self) -> str:
        moment = self._clock()
        return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # -- before a step ---------------------------------------------------------------------------

    def check_step(
        self,
        conversation_id: str,
        account_id: int,
        *,
        step: int,
        model: str,
        pending_input_tokens: int = 0,
    ) -> Verdict:
        """
        Say whether the next request to the provider may be made.

        Args:
            conversation_id: The conversation.
            account_id: The person who asks.
            step: How many requests this answer has made already (0 for the first).
            model: The model about to be asked.
            pending_input_tokens: An estimate of what is about to be sent.

        Returns:
            The verdict: the first limit reached stops it, every limit at 80 % or more warns.
        """
        limits = self.limits
        warnings: list[ServerText] = []
        notes: list[ServerText] = []
        stop: ServerText | None = None

        if step >= limits.steps_per_turn:
            stop = say("assistant.limit.steps", limit=limits.steps_per_turn)
        elif _near(step, limits.steps_per_turn):
            warnings.append(
                say("assistant.limit.steps_near", used=step, limit=limits.steps_per_turn)
            )

        conversation = self._totals("conversation_id = ?", (conversation_id,))
        used_tokens = conversation.prompt_tokens + max(pending_input_tokens, 0)
        if used_tokens >= limits.input_tokens_per_conversation:
            stop = stop or say(
                "assistant.limit.input_tokens", limit=limits.input_tokens_per_conversation
            )
        elif _near(used_tokens, limits.input_tokens_per_conversation):
            warnings.append(
                say(
                    "assistant.limit.input_tokens_near",
                    used=used_tokens,
                    limit=limits.input_tokens_per_conversation,
                )
            )

        if model in self.prices:
            spent, month_spent = (
                conversation.spend,
                self._totals("at >= ?", (self._month_start(),)).spend,
            )
            money = {"currency": self.currency}
            if spent >= limits.spend_per_conversation:
                stop = stop or say(
                    "assistant.limit.conversation_spend",
                    limit=round(limits.spend_per_conversation, 2),
                    **money,
                )
            elif _near(spent, limits.spend_per_conversation):
                warnings.append(
                    say(
                        "assistant.limit.conversation_spend_near",
                        used=round(spent, 2),
                        limit=round(limits.spend_per_conversation, 2),
                        **money,
                    )
                )
            if month_spent >= limits.spend_per_month:
                stop = stop or say(
                    "assistant.limit.month_spend", limit=round(limits.spend_per_month, 2), **money
                )
            elif _near(month_spent, limits.spend_per_month):
                warnings.append(
                    say(
                        "assistant.limit.month_spend_near",
                        used=round(month_spent, 2),
                        limit=round(limits.spend_per_month, 2),
                        **money,
                    )
                )
        else:
            notes.append(say("assistant.limit.no_price", model=model or "unnamed"))
        if conversation.estimated_steps:
            notes.append(say("assistant.limit.estimated"))
        return Verdict(stop, tuple(warnings), tuple(notes))

    def check_action(self, account_id: int) -> Verdict:
        """
        Say whether the assistant may make one more change for a person.

        Args:
            account_id: The person.

        Returns:
            The verdict for the hour's limit.
        """
        made = self._actions(account_id)
        limit = self.limits.actions_per_hour
        if made >= limit:
            return Verdict(stop=say("assistant.limit.actions", limit=limit))
        if _near(made, limit):
            return Verdict(warnings=(say("assistant.limit.actions_near", used=made, limit=limit),))
        return Verdict()

    # -- after a step ----------------------------------------------------------------------------

    def record_step(
        self, conversation_id: str, account_id: int, model: str, usage: Usage
    ) -> StepCost:
        """
        Write down what a step used. Never refuses: the next check is what stops.

        Args:
            conversation_id: The conversation.
            account_id: The person who asked.
            model: The model that answered.
            usage: What the provider reported, or the estimate.

        Returns:
            What the step cost.
        """
        price = self.prices.get(model)
        cost = cost_of(price, usage) if price is not None else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO assistant_usage (conversation_id, account_id, at, model, "
                "prompt_tokens, completion_tokens, estimated, cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    account_id,
                    self._now(),
                    model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    int(usage.estimated),
                    cost,
                ),
            )
        return StepCost(usage, cost)

    def record_action(self, conversation_id: str, account_id: int, tool: str) -> None:
        """
        Write down a change the assistant made.

        Args:
            conversation_id: The conversation.
            account_id: The person it was made for.
            tool: The tool that made it.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO assistant_actions (account_id, conversation_id, tool, at) "
                "VALUES (?, ?, ?, ?)",
                (account_id, conversation_id, tool, self._now()),
            )

    # -- reading ---------------------------------------------------------------------------------

    def report(
        self, *, conversation_id: str | None = None, account_id: int | None = None
    ) -> UsageReport:
        """
        Add up what was used.

        Args:
            conversation_id: One conversation, to give its totals.
            account_id: One person, to give their changes of the last hour.

        Returns:
            The totals and the limits.
        """
        return UsageReport(
            conversation=(
                self._totals("conversation_id = ?", (conversation_id,))
                if conversation_id is not None
                else None
            ),
            month=self._totals("at >= ?", (self._month_start(),)),
            actions_last_hour=self._actions(account_id) if account_id is not None else None,
            limits=self.limits,
            currency=self.currency,
            month_start=self._month_start(),
            priced_models=tuple(sorted(self.prices)),
        )

    def _totals(self, where: str, params: tuple[Any, ...]) -> Totals:
        """
        Args:
            where: A fixed condition on ``assistant_usage``.
            params: Its values.

        Returns:
            The totals of the rows it picks.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "  # noqa: S608 - fixed text
                "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost), 0.0), "
                "COALESCE(SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(estimated), 0) "
                f"FROM assistant_usage WHERE {where}",
                params,
            )
            row = cursor.fetchone()
        return Totals(
            steps=int(row[0]),
            prompt_tokens=int(row[1]),
            completion_tokens=int(row[2]),
            spend=float(row[3]),
            unpriced_steps=int(row[4]),
            estimated_steps=int(row[5]),
        )

    def _actions(self, account_id: int) -> int:
        """
        Args:
            account_id: A person.

        Returns:
            How many changes the assistant made for them in the last hour.
        """
        since = (self._clock() - timedelta(hours=1)).replace(microsecond=0).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM assistant_actions WHERE account_id = ? AND at >= ?",
                (account_id, since),
            )
            return int(cursor.fetchone()[0])
