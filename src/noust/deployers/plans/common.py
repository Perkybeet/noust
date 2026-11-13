# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every plan of an operation does the same way.

The rehearsal itself is :func:`noust.core.plans.engine.rehearse`; this is what stands around it: the
scrubber of the application the plan is about, the confirmations the operation asks for (a freeze, a
schema change), what stops it, and the sentences of what the plan cannot know. Each operation's own
module says only what is its own.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from noust.core import applock
from noust.core.exceptions import DeploysFrozenError, NoustError, SchemaChangedError
from noust.core.logger import Logger
from noust.core.plans import engine
from noust.core.plans.shape import PlanBlock, PlanConfirmation, PlanNeeds, PlanOut
from noust.core.redact import scrubber_for
from noust.core.server_text import ServerText, say

T = TypeVar("T")


def quiet_logger() -> Logger:
    """
    Returns:
        A logger that says nothing: the operation under rehearsal reports its progress to a logger,
        and a plan is not where that goes.
    """
    return Logger(verbose=False, no_color=True, stream=io.StringIO())


def scrub_for(domain: str | None) -> Callable[[str], str]:
    """
    Args:
        domain: The application the plan is about, when it is about one.

    Returns:
        What takes out of a text the values that application holds, Noust's own credentials and
        every shape of a credential.
    """
    return scrubber_for(domain).scrub


def refusal(error: NoustError) -> PlanBlock:
    """
    Say that the operation would be refused, with the operation's own words.

    Args:
        error: What the operation raised.

    Returns:
        The block: its message as the reason and its details as the fix, when it has them.
    """
    if error.details:
        return PlanBlock(say("plans.blocked.refused", reason=error.message, fix=error.details))
    return PlanBlock(say("plans.blocked.refused_plain", reason=error.message))


def busy(domain: str) -> PlanBlock | None:
    """
    Args:
        domain: An application's domain.

    Returns:
        A block that says what is running on it, or None when nothing is. The rehearsal does not
        take the application's lock (a lock file is a change), so who holds it is read.
    """
    holder = applock.holder_of(domain)
    if holder is None:
        return None
    return PlanBlock(
        say(
            "plans.blocked.busy", operation=holder.operation, domain=domain, since=holder.started_at
        )
    )


def frozen(error: DeploysFrozenError, domain: str) -> PlanConfirmation:
    """
    Args:
        error: The refusal of a freeze.
        domain: The application.

    Returns:
        The confirmation a person gives to deploy during it.
    """
    reason = str(error.freeze.get("reason") or "")
    ends = error.freeze.get("ends_at")
    sentence = (
        say("plans.confirm.frozen", domain=domain, until=str(ends), reason=reason)
        if ends
        else say("plans.confirm.frozen_open", domain=domain, reason=reason)
    )
    return PlanConfirmation("frozen", sentence, "frozen_ok")


def schema_changed(error: SchemaChangedError, domain: str, target: str) -> PlanConfirmation:
    """
    Args:
        error: The refusal of going back past a migration.
        domain: The application.
        target: What is gone back to, in words.

    Returns:
        The confirmation a person gives to go back anyway.
    """
    return PlanConfirmation(
        "schema_changed",
        say(
            "plans.confirm.schema_changed",
            target=target,
            domain=domain,
            count=len(error.deployments),
            deployments=", ".join(str(number) for number in error.deployments),
        ),
        "schema_changed_ok",
    )


@dataclass(frozen=True)
class Confirmed(Generic[T]):
    """
    A rehearsal that was run again with what a person would confirm.

    Attributes:
        rehearsal: The last run.
        confirmations: What had to be confirmed to get that far, in the order it was asked.
    """

    rehearsal: engine.Rehearsal[T]
    confirmations: tuple[PlanConfirmation, ...] = field(default_factory=tuple)


def confirming(
    domain: str,
    target: str,
    run: Callable[[bool, bool], engine.Rehearsal[T]],
) -> Confirmed[T]:
    """
    Rehearse an operation, and where it asks for a confirmation, rehearse it again as confirmed.

    The operation's own guards decide (rule 4): a freeze is the guard of ``deploy_guard`` and a
    schema change the guard of ``require_schema_change_confirmed``, and a plan that asked them
    again in its own words would be a second implementation. What the operation would ask is what
    it raised.

    Args:
        domain: The application.
        target: What is gone back to, in words, for the sentence of a schema change.
        run: Rehearses the operation given whether a freeze and a schema change are confirmed.

    Returns:
        The rehearsal that got furthest, and the confirmations it took.
    """
    confirmations: list[PlanConfirmation] = []
    frozen_ok = schema_ok = False
    attempt = run(frozen_ok, schema_ok)
    for _ in range(2):
        error = attempt.error
        if isinstance(error, DeploysFrozenError) and not frozen_ok:
            confirmations.append(frozen(error, domain))
            frozen_ok = True
        elif isinstance(error, SchemaChangedError) and not schema_ok:
            confirmations.append(schema_changed(error, domain, target))
            schema_ok = True
        else:
            break
        attempt = run(frozen_ok, schema_ok)
    return Confirmed(attempt, tuple(confirmations))


def undone_probes(attempt: engine.Rehearsal[object]) -> list[ServerText]:
    """
    Args:
        attempt: A rehearsal.

    Returns:
        The sentence of the looks it did not make for want of time, when there were any.
    """
    if attempt.skipped_probes:
        return [say("plans.unknown.probes_skipped", count=attempt.skipped_probes)]
    return []


def stopped(attempt: engine.Rehearsal[object]) -> tuple[PlanBlock | None, list[ServerText]]:
    """
    Say what an operation that raised during its rehearsal means for the plan.

    Refused before it ran or changed anything, it is what the operation would answer now: a block.
    Raised after some steps, it may be the rehearsal's own limit (a step that reads what an
    earlier, unmade one would have written), so the plan ends there and says so.

    Args:
        attempt: A rehearsal whose operation raised.

    Returns:
        The block, or the sentence that the plan stops there; neither when it did not raise.
    """
    error = attempt.error
    if error is None:
        return None, []
    changed = any(not command.probe for command in attempt.commands) or bool(attempt.files)
    if not changed:
        return refusal(error), []
    return None, [say("plans.unknown.stopped", reason=error.message)]


def assemble(
    operation: str,
    subject: str | None,
    effect: ServerText,
    attempt: engine.Rehearsal[object],
    *,
    findings: list[ServerText],
    unknown: list[ServerText],
    confirmations: tuple[PlanConfirmation, ...],
    blocked: PlanBlock | None,
) -> PlanOut:
    """
    Put a rehearsal and what the operation found out into a plan.

    Args:
        operation: The operation's key in the registry.
        subject: What it is done to.
        effect: What changes, in a sentence.
        attempt: The rehearsal.
        findings: What the plan found out by looking.
        unknown: What it does not know.
        confirmations: What applying it will ask to confirm.
        blocked: Why it would be refused now, when it would.

    Returns:
        The plan.
    """
    return PlanOut(
        operation=operation,
        subject=subject,
        effect=effect,
        commands=attempt.commands,
        files=attempt.files,
        steps=attempt.steps,
        findings=tuple(findings),
        unknown=tuple(unknown),
        needs=PlanNeeds(confirmations=confirmations),
        blocked=blocked,
        asked_remote=attempt.asked_remote,
        omitted=attempt.omitted,
    )


def plan_call(
    operation: str,
    subject: str | None,
    effect: ServerText,
    call: Callable[[], object],
    *,
    domain: str | None,
    findings: Sequence[ServerText] = (),
    unknown: Sequence[ServerText] = (),
    phases: engine.Phases | None = None,
) -> PlanOut:
    """
    The plan of an operation that asks for nothing to be confirmed: rehearse its call and say it.

    Args:
        operation: The operation's key in the registry.
        subject: What it is done to.
        effect: What changes, in a sentence.
        call: The code that applies the operation, as it is called to apply it.
        domain: The application it is about, when it is about one: its secrets are scrubbed from the
            plan and what is running on it is said.
        findings: What the caller found out by looking.
        unknown: What the caller knows the plan cannot know.
        phases: Where the operation announces its phases, when it does.

    Returns:
        The plan.
    """
    attempt = engine.rehearse(call, scrub=scrub_for(domain), phases=phases)
    blocked = busy(domain) if domain else None
    said: list[ServerText] = list(unknown)
    if blocked is None:
        blocked, stopped_here = stopped(attempt)
        said.extend(stopped_here)
    if attempt.files or any(not command.probe for command in attempt.commands):
        said.append(say("plans.unknown.readback"))
    said.extend(undone_probes(attempt))
    return assemble(
        operation,
        subject,
        effect,
        attempt,
        findings=list(findings),
        unknown=said,
        confirmations=(),
        blocked=blocked,
    )
