# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the assistant does with a call it has prepared (item 171).

Every call has a class computed from what the API publishes of its operation
(:mod:`noust.core.operation_classes`: never from the verb, never from the model's words), and the
policy of who calls (:mod:`noust.core.caller_policy`) is asked what the assistant may do with it in
the environment of the resource. The answer is one of three:

- **run**: a read, or a change the policy puts in "does it", which the loop makes with its own token;
- **propose**: nothing is made; the call is kept, exactly, for a person to carry out with their own
  session. Three kinds, by how much of the decision is the person's: ``ask`` (their approval, once),
  ``you`` (it also asks them to confirm it is them: sudo mode) and ``two`` (it also waits for a
  second person, which a person never is for their own call);
- **refuse**: not offered. The floor, and a cell that says "never". The model is told it is not
  offered and the person is told who changes it; there is no call and no proposal.

Where the policy says "does it" the assistant still only proposes a call that carries a value that
is not the person's or Noust's (:mod:`noust.core.assistant.provenance`), a call made in a
conversation that has read text from outside, and a call past the hour's limit on changes. None of
this widens anything: it is the policy's answer or something stricter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from noust.core.caller_policy import (
    ALONE,
    ASSISTANT_NEVER_REASONS,
    NEVER,
    PRODUCTION,
    VERDICT_ASSISTANT_STRONG,
    CallerPolicy,
    Verdict,
    environment_of,
)
from noust.core.callers import ASSISTANT
from noust.core.operation_classes import (
    NEUTRAL_PERMISSIONS,
    PUBLIC_CLASS,
    OperationClass,
)

__all__ = [
    "ASK",
    "PROPOSAL_CLASSES",
    "READ",
    "REFUSED",
    "RUN",
    "TWO",
    "YOU",
    "Decision",
    "decide",
    "environment_for",
]

#: What the loop does.
RUN = "run"
PROPOSE = "propose"
REFUSE = "refuse"

#: The class of what is done: a read, or a change the policy lets it make.
READ = "read"
DONE_ALONE = "alone"
#: The class of what is proposed, by how much of the decision is the person's.
ASK = "ask"
YOU = "you"
TWO = "two"
#: The class of what is not offered.
REFUSED = "refused"

PROPOSAL_CLASSES: tuple[str, ...] = (ASK, YOU, TWO)


@dataclass(frozen=True)
class Decision:
    """
    What to do with one call, and why.

    Attributes:
        action: ``run``, ``propose`` or ``refuse``.
        klass: ``read`` or ``alone`` for a call that runs; ``ask``, ``you`` or ``two`` for one that
            is proposed; ``refused`` for one that is not offered.
        why: The reason in a word, which is the end of the sentence's code
            (``assistant.proposal.why.<why>``): ``reads``, ``policy_alone``, ``policy_propose``,
            ``needs_sudo``, ``needs_second``, ``foreign_value``, ``read_outside``, ``actions_limit``,
            ``floor``, ``policy_never`` or ``not_offered``.
        verdict: What the policy said.
        floor: For a refusal: it is the floor, which nobody changes, and not a cell of the table.
    """

    action: str
    klass: str
    why: str
    verdict: Verdict
    floor: bool = False


def environment_for(domain: str | None, *, proxied: bool = False) -> str:
    """
    Say which environment the resource of a call is in, as the guard of the API does.

    Args:
        domain: The application the call names, when it names one.
        proxied: The call is made on another server, whose applications this one does not hold.

    Returns:
        ``production`` or ``other``. Production for anything that cannot be placed: the prudent one.
    """
    if proxied or not domain:
        return PRODUCTION
    return environment_of(domain)


def decide(
    operation: OperationClass,
    needed: Sequence[str],
    *,
    environment: str,
    foreign: bool,
    tainted: bool,
    second_person: bool,
    actions_allowed: bool = True,
    policy: CallerPolicy | None = None,
) -> Decision:
    """
    Decide what the assistant does with a call.

    Args:
        operation: What the operation is.
        needed: Every permission the call needs.
        environment: Where the resource is (:func:`environment_for`).
        foreign: The call carries a value that is not the person's or Noust's.
        tainted: The conversation has read text from outside.
        second_person: A second person has to approve this very call (the four-eyes rule is on and
            covers it).
        actions_allowed: The hour's limit on changes has room.
        policy: The policy of who calls; the server's when None.

    Returns:
        The decision.
    """
    verdict = (policy or CallerPolicy()).decide(ASSISTANT, environment, operation, needed)
    if operation.kind == PUBLIC_CLASS or operation.permission in NEUTRAL_PERMISSIONS:
        # Somebody's own session, tokens and second factor, and the right to use the assistant
        # itself, are not what it is for.
        return Decision(REFUSE, REFUSED, "not_offered", verdict, floor=True)
    if verdict.value == NEVER:
        if verdict.reason == VERDICT_ASSISTANT_STRONG:
            # A person does it, with their own session: the model prepares the call and the
            # console shows it. The cell has nothing to say about it.
            if second_person:
                return Decision(PROPOSE, TWO, "needs_second", verdict)
            return Decision(PROPOSE, YOU, "needs_sudo", verdict)
        floor = verdict.reason in ASSISTANT_NEVER_REASONS
        return Decision(REFUSE, REFUSED, "floor" if floor else "policy_never", verdict, floor=floor)
    if not operation.changes:
        return Decision(RUN, READ, "reads", verdict)
    if second_person:
        return Decision(PROPOSE, TWO, "needs_second", verdict)
    if verdict.value == ALONE:
        if foreign:
            return Decision(PROPOSE, ASK, "foreign_value", verdict)
        if tainted:
            return Decision(PROPOSE, ASK, "read_outside", verdict)
        if not actions_allowed:
            return Decision(PROPOSE, ASK, "actions_limit", verdict)
        return Decision(RUN, DONE_ALONE, "policy_alone", verdict)
    return Decision(PROPOSE, ASK, "policy_propose", verdict)
