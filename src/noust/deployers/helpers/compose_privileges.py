# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Compose stack is let to ask of the host, applied to a deployment.

:mod:`noust.deployers.helpers.compose_guard` sorts what a compose file asks of the host and
:mod:`noust.deployers.helpers.compose_verdict` decides what to do with it; this is what the
deployer does with the answer. It lives beside the deployer and not in it because the deployer's
module is past the length the project allows and does not grow.

A stack's findings are of three kinds. What gives the machine (``privileged``, the Docker socket,
a device, a capability that reconfigures the kernel, a path of the server outside the application,
the state of Noust, another application's files or objects, a file that leaves the application, a
download) is refused for a new stack unless an operator recorded an exception for the application,
and is only warned about for a stack that is proven to run with that very finding already. What is
only worth knowing is warned about. And what the file does not spell out (a service an ``include``
brought in, one an ``extends`` took from another file, a service under a profile, a path made of
variables) is found by asking Compose for the model it resolves the file to, which is judged by the
same rules, once before anything is built and once more when the stack's own ``.env`` is written.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from noust.core.docker import names
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.server_text import say
from noust.deployers.helpers import compose_verdict
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    GuardContext,
    inspect_compose,
    with_resolved,
)
from noust.deployers.helpers.compose_owners import Owners
from noust.managers.source_links import links_changed as changed_links

if TYPE_CHECKING:
    from noust.deployers.docker_compose import DockerComposeDeployer


def guard_context(deployer: DockerComposeDeployer) -> GuardContext:
    """
    Say what the guard needs to know of a stack to judge its file.

    Args:
        deployer: The deployer of the stack.

    Returns:
        Where relative paths are read from (the compose file's directory), the project the
        application runs under (the store's, else the name Compose derives from that directory)
        and whose Docker objects are, from the store.
    """
    from noust.deployers.docker_compose import _env_project_name

    compose = deployer.compose_path
    project_dir = compose.parent if compose is not None else deployer.app_path
    reference = deployer._pinned_project() or names.project_of_directory(project_dir.name)
    try:
        owners: Owners | None = Owners.of_store(deployer.store, deployer.domain)
    except (NoustError, sqlite3.Error) as exc:
        deployer.logger.debug(f"Compose: who owns what is not known ({exc})")
        owners = None
    return GuardContext(
        app_path=deployer.app_path,
        project_dir=project_dir,
        domain=deployer.domain,
        project=reference or "default",
        env_project=(_env_project_name(compose) or "") if compose is not None else "",
        owners=owners,
    )


def check_host_privileges(
    deployer: DockerComposeDeployer,
    document: Any,
    *,
    accepted: Sequence[str] | None = None,
    final: bool = False,
) -> None:
    """
    Refuse, or warn about, what in a stack's compose file gives it this server.

    What gives the machine is refused unless an operator recorded an exception for the domain, or
    the stack is proven to run with that very finding already: the compose file that served had it.
    Only then is it warned about, so the stack is never stopped; whatever a push, a redeploy or an
    adopted directory adds is refused. What belongs to another application or to Noust is refused
    whatever the exception says, and is only warned about for a stack that already had it.

    What is judged is the file as written and, unless the file alone already decides it, what
    ``docker compose config`` resolves it to with every profile on.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file.
        accepted: The findings the stack already ran with; None reads the deployer's
            ``_accepted_refusals``, which is empty for a new stack.
        final: The stack's own ``.env`` is written, so the model Compose resolves is the one that
            will run: it is always asked for, and a path made of variables that it still cannot
            resolve is refused.

    Raises:
        DeploymentError: The file asks for what the stack did not have, without an exception.
    """
    from noust.deployers.helpers import sandbox as build_sandbox

    context = guard_context(deployer)
    findings = inspect_compose(document, deployer.app_path, context=context)
    exception = build_sandbox.get_compose_exception(deployer.domain, store=deployer.store)
    already = set(accepted if accepted is not None else deployer._accepted_refusals or ())
    verdict = compose_verdict.decide(findings, already=already, exception=exception)
    # A file that is already refused is not read any further: resolving it is how an include that
    # leaves the application would be followed.
    if final or not verdict.stop:
        findings = with_what_compose_resolves(deployer, document, findings, final=final)
        verdict = compose_verdict.decide(findings, already=already, exception=exception)
    for warning in findings.warned:
        deployer.logger.warning(f"Compose: {warning}")
    for problem in verdict.excused:
        deployer.logger.warning(
            f"Compose: {problem}; allowed for {deployer.domain} by "
            f"{exception.allowed_by if exception else ''} ({exception.reason if exception else ''})"
        )
    command = f"noust app sandbox compose-exception {deployer.domain} --reason '...'"
    for problem in verdict.had:
        said = (
            "This is not allowed to a new stack, with or without an exception"
            if problem in findings.hard
            else f"A new stack is refused this without an exception; record why this one needs it: {command}"
        )
        deployer.logger.warning(f"Compose: {problem}. {said}")
    if not verdict.stop:
        return
    lines = "\n".join(f"- {problem}" for problem in verdict.hard or verdict.stop)
    if verdict.hard:
        raise DeploymentError.from_text(
            say("compose_guard.asks_for_what_no_stack_may", domain=deployer.domain, lines=lines)
        )
    raise DeploymentError.from_text(
        say("compose_guard.asks_for_root", domain=deployer.domain, lines=lines, command=command)
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
    if document is None:
        return []
    findings = inspect_compose(document, deployer.app_path, context=guard_context(deployer))
    if resolve:
        findings = with_what_compose_resolves(deployer, document, findings)
    return findings.refused


def accepted_since(
    deployer: DockerComposeDeployer, served: Any, previous_commit: str | None
) -> list[str]:
    """
    List what the compose file that served had, which an update has not changed.

    The file is judged on the tree as it is now, so a link the update added or changed would make
    a line that reached a directory before look as if it always reached what the link leads to:
    git says which links the commits changed, and a finding through one of them is not carried
    over.

    Args:
        deployer: The deployer of the stack.
        served: The compose file of the commit that served, parsed.
        previous_commit: That commit.

    Returns:
        The refused sentences the stack already ran with.
    """
    findings = inspect_compose(served, deployer.app_path, context=guard_context(deployer))
    changed = (
        changed_links(deployer._source_manager(), deployer.app_path, previous_commit)
        if previous_commit
        else None
    )
    return compose_verdict.unchanged(findings, changed)


def with_what_compose_resolves(
    deployer: DockerComposeDeployer,
    document: Any,
    findings: ComposeFindings,
    *,
    final: bool = False,
) -> ComposeFindings:
    """
    Add to the findings of a file what only the model Compose resolves it to shows.

    A stack's include, extends and profiles are part of what runs. A file that includes or extends
    another and cannot be resolved is refused rather than guessed, since what it brings in is what
    cannot be judged; one that brings nothing in is judged as written, and the log says Compose
    would not resolve it (a variable its ``.env`` has not got yet is the usual reason), unless the
    ``.env`` is already written (``final``) and a path of the file is made of variables.

    Args:
        deployer: The deployer of the stack, whose project, file and profiles build the command.
        document: The parsed compose file.
        findings: What the file as written asks of the host.
        final: See :func:`check_host_privileges`.

    Returns:
        The findings with what the model adds.
    """
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
        context=guard_context(deployer),
        final=final,
    )
    if unresolved:
        deployer.logger.info(f"Compose: {unresolved}")
    return merged
