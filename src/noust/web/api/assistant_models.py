# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The parts of the built-in assistant's answer, each with the shape the server gives it (item 171).

A turn, a step and a proposal carried loose objects (what it read and how much, what it quotes, the values of
a call and where each was found, what an operation's simulation says, what the call needs, what it used and
what went wrong), and the console narrowed each by hand to draw it. The shapes are here, once, so the schema
names them and the console's types are generated from it. What is always sent is required: a list with a
default factory has no default in the schema, and the generator draws it optional.

Nothing here reads or decides anything: these are the words of :mod:`noust.web.assistant.present`'s answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from noust.web.api.assistant import SentenceOut
from noust.web.api.deps import CodedText

#: Where a value of a call was found when it is not the person's or Noust's.
ForeignKind = Literal["host", "address", "url", "email", "command", "value"]

#: What a step counted.
SizeUnit = Literal["lines", "items", "chars", "results"]


class TurnPageOut(BaseModel):
    """
    The page a question was asked from; every field is absent for a question asked from none.

    Attributes:
        kind: ``deployment``, ``diagnosis``, ``alert`` or ``page``.
        id: The deployment, for ``deployment``.
        domain: The application, for ``diagnosis``.
        rule: The alert's rule, for ``alert``, when it names one.
        route: The place in the console, for ``page``.
        server: The server it was on, when it was on one.
    """

    kind: Literal["deployment", "diagnosis", "alert", "page"] | None = None
    id: int | None = None
    domain: str | None = None
    rule: str | None = None
    route: str | None = None
    server: str | None = None


class StepSizeOut(BaseModel):
    """
    How much a step read.

    Attributes:
        unit: What was counted: ``lines``, ``items``, ``chars`` or ``results``.
        count: How many.
    """

    unit: SizeUnit
    count: int


class PartSourceOut(BaseModel):
    """
    Which text a quotation is from.

    Attributes:
        id: Its identifier in the turn (the ``S1`` of ``[[S1:14-15]]``).
        kind: What it is (a log, a deployment, a diagnosis...).
        label: What it is called, as a sentence with a code.
    """

    id: str
    kind: str
    label: CodedText


class PartLineOut(BaseModel):
    """
    One line quoted as the system wrote it.

    Attributes:
        n: Its number in the text.
        text: The line, with the disguised emails and addresses put back.
    """

    n: int
    text: str


class FactForeignOut(BaseModel):
    """
    Where a value of a call that is neither the person's nor Noust's was found.

    Attributes:
        kind: ``host``, ``address``, ``url``, ``email``, ``command`` or ``value``.
        source: The text it was found in; null when it was found in none.
        line: Which line of it.
        label: What that text is called, as a sentence with a code.
    """

    kind: ForeignKind
    source: str | None = None
    line: int | None = None
    label: CodedText | None = None


class FactOut(BaseModel):
    """
    One value of the call a proposal makes, as the card shows it.

    Attributes:
        name: Where in the call it is (``body.domain``, ``server``, ``environment``).
        value: The value, as text and cut at 500 characters.
        foreign: Where it was found, when it came from a text somebody else wrote: it is highlighted and
            makes the proposal one the person decides.
    """

    name: str
    value: str
    foreign: FactForeignOut | None = None


class ProposalNeedsOut(BaseModel):
    """
    What carrying a proposal out asks of the person.

    Attributes:
        elevation: Sudo mode: the call is one that asks them to confirm it is them.
        second_person: A second person approves it.
    """

    elevation: bool = False
    second_person: bool = False


class TurnErrorOut(SentenceOut):
    """
    What went wrong in a turn.

    Attributes:
        output: The provider's own words, bounded and with the credentials masked; null when the failure was
            not the provider's.
    """

    output: str | None = None


class ConversationUsageOut(BaseModel):
    """
    What a conversation has used so far.

    Attributes:
        tokens: Tokens sent.
        spend: What it cost, in the currency of the settings.
        steps: Requests to the provider.
    """

    tokens: int = 0
    spend: float = 0.0
    steps: int = 0


class TurnUsageOut(BaseModel):
    """
    What a turn used, and the conversation up to it.

    Attributes:
        steps: Requests to the provider in this turn.
        tokens: Tokens it used.
        spend: What it cost, in the currency of the settings.
        conversation: What the whole conversation used; null before the meter said.
        currency: What the spending is in.
        actions_last_hour: How many changes the assistant made for the person in the last hour.
        notes: What to know about the numbers (no price for the model, an estimate), as sentences.
    """

    steps: int = 0
    tokens: int = 0
    spend: float = 0.0
    conversation: ConversationUsageOut | None = None
    currency: str | None = None
    actions_last_hour: int | None = None
    notes: list[SentenceOut]
