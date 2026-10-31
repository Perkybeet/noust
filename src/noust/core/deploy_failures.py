# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a deployment that did not finish says so, as sentences the console says in its own language.

The first line of a failed deployment's error is one of Noust's own sentences ("Release 20261031-1
did not pass its health check"), and what follows is another program's words: the probes of the
health gate, the journal, the package manager's and the build's output. A job keeps that error as
text and so does a deployment, and a Spanish console showed the first line in English, in the
toast that says the job ended and in the Overview's list of what needs attention.

The raise sites build the error here, so each sentence exists once (rule 3) and carries its code on
the exception; :func:`coded` reads a stored first line back as the sentence that made it, which is
what the two readers that only have the text (the job's notice and the attention list) ask for.
Another program's words are never read back: only the whole of a first line that is exactly one of
these sentences is.
"""

from __future__ import annotations

from typing import Literal

from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.server_text import ServerText, read_back, say

#: Every sentence a failed deployment can lead with. The most specific first: the order is the one
#: a line is tried against, and none is a prefix of another because a line has to be all of one.
_CODES: tuple[str, ...] = (
    "deploy_failure.release_unhealthy",
    "deploy_failure.release_unhealthy_back",
    "deploy_failure.release_unhealthy_down",
    "deploy_failure.app_unhealthy",
    "deploy_failure.rollback_unhealthy",
    "deploy_failure.stack_unhealthy",
    "deploy_failure.stack_nothing_before",
    "deploy_failure.stack_not_restored",
    "deploy_failure.stack_back",
    "deploy_failure.stack_back_down",
    "deploy_failure.install_failed",
    "deploy_failure.install_timeout",
    "deploy_failure.install_oom",
    "deploy_failure.build_failed",
    "deploy_failure.build_timeout",
    "deploy_failure.build_oom",
)


def release_unhealthy(
    release: str, *, previous: str | None, restored: bool, evidence: str
) -> DeploymentError:
    """
    The error of a release that did not pass its health gate.

    Args:
        release: The release that failed.
        previous: The release that was put back, or None when there was none to put back.
        restored: Whether the release put back answers; only meaningful with ``previous``.
        evidence: What the gate saw (the probes and the journal), verbatim.

    Returns:
        A :class:`RolledBackError` when the previous release answers again, a plain
        :class:`DeploymentError` when none was put back or the one put back does not answer.
    """
    if previous is None:
        sentence = say("deploy_failure.release_unhealthy", release=release)
    elif restored:
        sentence = say("deploy_failure.release_unhealthy_back", release=release, previous=previous)
    else:
        sentence = say("deploy_failure.release_unhealthy_down", release=release, previous=previous)
    # Only a previous release that answers makes this a rollback: one that does not leaves the
    # application down, which is a plain failure.
    error_class = RolledBackError if previous is not None and restored else DeploymentError
    return error_class(sentence.text, details=evidence, coded=sentence)


def app_unhealthy(domain: str, *, evidence: str) -> DeploymentError:
    """
    The error of an application restarted in place that did not pass its health gate.

    Args:
        domain: The application.
        evidence: What the gate saw, verbatim.

    Returns:
        The error.
    """
    sentence = say("deploy_failure.app_unhealthy", domain=domain)
    return DeploymentError(sentence.text, details=evidence, coded=sentence)


def rollback_unhealthy(domain: str, *, details: str) -> DeploymentError:
    """
    The error of an application that did not pass its health gate after a rollback.

    Args:
        domain: The application.
        details: What the gate saw and the way back, verbatim.

    Returns:
        The error.
    """
    sentence = say("deploy_failure.rollback_unhealthy", domain=domain)
    return DeploymentError(sentence.text, details=details, coded=sentence)


def stack_update_unhealthy(domain: str) -> ServerText:
    """
    Args:
        domain: A Compose stack whose update did not pass its health gate.

    Returns:
        The sentence the log says before the stack is put back.
    """
    return say("deploy_failure.stack_unhealthy", domain=domain)


#: How a Compose stack's update ended after its new containers failed the gate, and the sentence of each.
StackOutcome = Literal["nothing_before", "not_restored", "back", "back_down"]


def stack_went_back(domain: str, outcome: StackOutcome, details: str) -> DeploymentError:
    """
    The error of a Compose stack whose update did not pass its health gate.

    Args:
        domain: The stack.
        outcome: ``nothing_before`` (nothing ran before the update, so nothing was put back),
            ``not_restored`` (what served could not be put back entirely), ``back`` (it runs
            again and answers) or ``back_down`` (it is back and does not answer either).
        details: What the gate saw and the containers' output, verbatim.

    Returns:
        A :class:`RolledBackError` when what served runs again and answers, a plain
        :class:`DeploymentError` for each other ending.
    """
    if outcome == "nothing_before":
        sentence = say("deploy_failure.stack_nothing_before", domain=domain)
    elif outcome == "not_restored":
        sentence = say("deploy_failure.stack_not_restored", domain=domain)
    elif outcome == "back":
        sentence = say("deploy_failure.stack_back", domain=domain)
    else:
        sentence = say("deploy_failure.stack_back_down", domain=domain)
    error_class = RolledBackError if outcome == "back" else DeploymentError
    return error_class(sentence.text, details=details, coded=sentence)


def coded(message: str | None) -> ServerText | None:
    """
    Args:
        message: An error as a job or a deployment keeps it. Only its first non-empty line is
            read: the rest is what a program printed.

    Returns:
        The sentence the first line is, or None when it is anything else (a tool's own error, a
        sentence of an area that has no code yet).
    """
    for line in (message or "").splitlines():
        if line.strip():
            return read_back(line.strip(), _CODES, integers=("seconds",))
    return None
