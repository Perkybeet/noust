# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one answer of every plan: what an operation would do, before it does it (item 183).

:class:`PlanOutModel` is :class:`noust.core.plans.shape.PlanOut` in the API's words. The operations
that had a plan before this one (the cleanup of Docker, the migration to releases, the promotion of a
release) keep the route and the answer they have, and add this shape beside it, so that the console
draws every plan with one piece.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from noust.core.plans.diffs import MAX_DIFF_LINES
from noust.core.plans.shape import MAX_COMMANDS, MAX_FILES, PlanOut
from noust.web.api.deps import CodedText
from noust.web.pydantic_compat import bounded_list

#: The most sentences of one kind a plan holds (findings, what it does not know).
MAX_SENTENCES = 50

#: The most steps, and the most confirmations, a plan holds.
MAX_STEPS = 100
MAX_CONFIRMATIONS = 10


class PlanSentence(CodedText):
    """
    A sentence of a plan: a code and its values for the console to say in its language, beside the
    English that the command line and a client with no catalog show.

    Attributes:
        text: The sentence in English.
        hint: How to fix it in English, when the sentence has a fix.
    """

    text: str
    hint: str | None = None


class PlanCommandOut(BaseModel):
    """
    One command of a plan, as it would run.

    Attributes:
        argv: The argument vector, redacted: no secret is in it.
        text: The command as it would be typed.
        probe: It only looks.
        cwd: The directory it runs in, when it is not the process's.
        runs_as: The account it runs as, or ``sandbox:<account>``.
        step: The key of its step, when the operation has steps.
        writes_to: The file whose content it captures (a dump).
        check: Not a process: a check Noust asks itself of the application after the change. The
            plan does not ask it.
        seconds: The longest a check waits.
    """

    argv: list[str]
    text: str
    probe: bool
    cwd: str | None = None
    runs_as: str | None = None
    step: str | None = None
    writes_to: str | None = None
    check: bool = False
    seconds: float | None = None


class PlanDiffLineOut(BaseModel):
    """
    One line of a file's change.

    Attributes:
        kind: ``same`` (context), ``add`` or ``del``.
        text: The line, masked; for a file of variables, only a name.
    """

    kind: Literal["same", "add", "del"]
    text: str


class PlanFileOut(BaseModel):
    """
    One path the operation would change.

    Attributes:
        path: Where.
        change: ``create``, ``modify``, ``delete``, ``move``, ``copy``, ``link``, ``mkdir`` or
            ``meta`` (its mode or owner).
        shown: What ``lines`` is: ``diff`` of the text, ``names`` of the variables (a file of
            variables never shows a value) or ``none``.
        lines: The change, when it is shown.
        truncated: The change is longer than what is shown.
        reason: Why nothing is shown, when nothing is: ``secret``, ``binary``, ``large`` or
            ``unreadable``.
        destination: The other end of a move, a copy or a link.
    """

    path: str
    change: str
    shown: str
    lines: list[PlanDiffLineOut] = bounded_list(MAX_DIFF_LINES)
    truncated: bool = False
    reason: str | None = None
    destination: str | None = None


class PlanStepOut(BaseModel):
    """
    A phase the operation announces, with its commands.

    Attributes:
        key: Its position in the operation.
        title: What the operation calls it, as it says it while it runs.
        commands: Which of the plan's commands belong to it, by position.
    """

    key: str
    title: str
    commands: list[int] = bounded_list(MAX_COMMANDS)


class PlanConfirmationOut(BaseModel):
    """
    Something the operation asks to confirm before it goes ahead.

    Attributes:
        kind: ``schema_changed`` or ``frozen``.
        sentence: What is confirmed.
        field: The field of the request that gives the confirmation.
    """

    kind: str
    sentence: PlanSentence
    field: str


class PlanNeedsOut(BaseModel):
    """
    What applying the operation asks of whoever applies it.

    Attributes:
        elevation: Sudo mode, which a plan never asks for.
        approval: A second person approves the call.
        confirmations: What the request itself confirms.
    """

    elevation: bool = False
    approval: bool = False
    confirmations: list[PlanConfirmationOut] = bounded_list(MAX_CONFIRMATIONS)


class PlanOutModel(BaseModel):
    """
    What an operation would do, before it does it.

    Nothing was executed to make it: a command that only looks was asked, in order that the plan
    does not report fiction, and nothing else.

    Attributes:
        operation: Which operation (``app.update``).
        subject: What it is done to.
        effect: What changes, in a sentence.
        commands: What would run, in order, probes included.
        files: What would change on disk.
        steps: The phases, when the operation has them.
        findings: What the plan found out by looking.
        unknown: What the plan does not know, as sentences. A plan is a statement of what Noust
            would do and of what it cannot promise.
        needs: What applying it asks for.
        blocked: Why it would be refused now, when it would.
        asked_remote: It asked a repository or a registry, over the network and within a deadline.
        omitted: How many commands and files were left out for the plan's size.
    """

    operation: str
    subject: str | None = None
    effect: PlanSentence
    commands: list[PlanCommandOut] = bounded_list(MAX_COMMANDS)
    files: list[PlanFileOut] = bounded_list(MAX_FILES)
    steps: list[PlanStepOut] = bounded_list(MAX_STEPS)
    findings: list[PlanSentence] = bounded_list(MAX_SENTENCES)
    unknown: list[PlanSentence] = bounded_list(MAX_SENTENCES)
    needs: PlanNeedsOut = Field(default_factory=PlanNeedsOut)
    blocked: PlanSentence | None = None
    asked_remote: bool = False
    omitted: int = 0


def plan_body(plan: PlanOut) -> PlanOutModel:
    """
    Args:
        plan: A plan.

    Returns:
        It, as the API's model.
    """
    return PlanOutModel(**plan.to_dict())
