# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Compose stack is let to ask of the host, applied to a deployment.

:mod:`noust.deployers.helpers.compose_guard` sorts what a compose file asks of the host; this is
what the deployer does with the answer. It lives beside the deployer and not in it because the
deployer's module is past the length the project allows and does not grow.

A stack's findings are of three kinds. Root on the host (``privileged``, the Docker socket, the
state of Noust, another application's files, the volumes or the namespace of a container that is
not the stack's, a file that leaves the application) is refused for a new stack unless an operator
recorded an exception for the application, and is only warned about for a stack that is proven to
run with that very finding already. What is only worth knowing is warned about. And what the file
does not spell out (a service an ``include`` brought in, one an ``extends`` took from another
file, a service under a profile) is found by asking Compose for the model it resolves the file to,
which is judged by the same rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import DeploymentError

if TYPE_CHECKING:
    from noust.deployers.docker_compose import DockerComposeDeployer
    from noust.deployers.helpers.compose_guard import ComposeFindings


def check_host_privileges(
    deployer: DockerComposeDeployer, document: Any, *, accepted: Sequence[str] | None = None
) -> None:
    """
    Refuse, or warn about, what in a stack's compose file is root on this server.

    ``privileged`` and a mount of the Docker socket are refused unless an operator recorded an
    exception for the domain, or the stack is proven to run with that very finding already: the
    compose file that served had it. Only then is it warned about, so the stack is never stopped;
    whatever a push, a redeploy or an adopted directory adds is refused.

    What is judged is the file as written and, unless the file alone already decides it, what
    ``docker compose config`` resolves it to with every profile on.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file.
        accepted: The findings the stack already ran with; None reads the deployer's
            ``_accepted_refusals``, which is empty for a new stack.

    Raises:
        DeploymentError: The file asks for root the stack did not have, without an exception.
    """
    from noust.deployers.helpers import sandbox as build_sandbox
    from noust.deployers.helpers.compose_guard import inspect_compose

    findings = inspect_compose(document, deployer.app_path)
    exception = build_sandbox.get_compose_exception(deployer.domain, store=deployer.store)
    already = set(accepted if accepted is not None else deployer._accepted_refusals or ())
    # A file that is already refused is not read any further: resolving it is how an include that
    # leaves the application would be followed.
    if exception is not None or all(problem in already for problem in findings.refused):
        findings = with_what_compose_resolves(deployer, document, findings)
    for warning in findings.warned:
        deployer.logger.warning(f"Compose: {warning}")
    if not findings.refused:
        return
    if exception is not None:
        for problem in findings.refused:
            deployer.logger.warning(
                f"Compose: {problem}; allowed for {deployer.domain} by "
                f"{exception.allowed_by} ({exception.reason})"
            )
        return
    added = [problem for problem in findings.refused if problem not in already]
    for problem in findings.refused:
        if problem in already:
            deployer.logger.warning(
                f"Compose: {problem}. A new stack is refused this without an exception; "
                f"record why this one needs it: noust app sandbox compose-exception "
                f"{deployer.domain} --reason '...'"
            )
    if not added:
        return
    raise DeploymentError(
        f"{deployer.domain}'s compose file asks for root on this server",
        details="\n".join(f"- {problem}" for problem in added)
        + "\n\nA privileged container or the Docker socket is root on the host, and the "
        "compose file comes from the repository; the stack did not run with this before. "
        "Remove them, or record why this stack needs them and deploy again: noust app "
        f"sandbox compose-exception {deployer.domain} --reason '...'",
    )


def refusals_in(
    deployer: DockerComposeDeployer, document: Any, *, resolve: bool = False
) -> list[str]:
    """
    List what the guard refuses in a compose document.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file, or None.
        resolve: Also ask Compose for what the file on disk resolves to. Only the file the stack
            runs from can be asked: the file of an older commit is text, not a project.

    Returns:
        The refused findings; empty for no document.
    """
    from noust.deployers.helpers.compose_guard import inspect_compose

    if document is None:
        return []
    findings = inspect_compose(document, deployer.app_path)
    if resolve:
        findings = with_what_compose_resolves(deployer, document, findings)
    return findings.refused


def with_what_compose_resolves(
    deployer: DockerComposeDeployer, document: Any, findings: ComposeFindings
) -> ComposeFindings:
    """
    Add to the findings of a file what only the model Compose resolves it to shows.

    A stack's include, extends and profiles are part of what runs. A file that includes or extends
    another and cannot be resolved is refused rather than guessed, since what it brings in is what
    cannot be judged; one that brings nothing in is judged as written, and the log says Compose
    would not resolve it (a variable its ``.env`` has not got yet is the usual reason).

    Args:
        deployer: The deployer of the stack, whose project, file and profiles build the command.
        document: The parsed compose file.
        findings: What the file as written asks of the host.

    Returns:
        The findings with what the model adds.
    """
    from noust.deployers.helpers.compose_guard import with_resolved

    if not deployer.compose_path:
        return findings
    merged, unresolved = with_resolved(
        findings,
        document,
        deployer.runner,
        [
            deployer._compose("config", "--format", "json", all_profiles=True),
            deployer._compose("config", "--format", "json"),
        ],
        deployer.app_path,
    )
    if unresolved:
        deployer.logger.info(f"Compose: {unresolved}")
    return merged
