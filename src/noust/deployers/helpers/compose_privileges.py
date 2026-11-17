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

**What a stack already had** is the compose file that served, as written, and what its containers were
given (:mod:`noust.deployers.helpers.compose_running`): a stack whose mount is ``${DATA_DIR}:/data``
shows nothing in the file as written, and resolving the file with the ``.env`` of today would let anyone
who can change a variable say the stack always had what it now names. Docker is asked only when a
finding that the file alone does not cover could be vouched for by a container, and once.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.docker import names
from noust.core.docker.errors import DockerUnavailableError
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.server_text import say
from noust.core.teams.scope import Scope
from noust.deployers.helpers import compose_running, compose_verdict
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    GuardContext,
    inspect_compose,
    merge_findings,
    with_resolved,
)
from noust.deployers.helpers.compose_owners import Owners, declared_project
from noust.deployers.helpers.compose_verdict import Heritage
from noust.managers.source_links import links_changed as changed_links

if TYPE_CHECKING:
    from noust.core.store import App
    from noust.deployers.docker_compose import DockerComposeDeployer


def stack_project(pinned: str | None, app_path: Path, compose_path: Path | None) -> str | None:
    """
    Name the Compose project a stack's commands address: the one place that decides it.

    Args:
        pinned: The project the store records for the application (an adopted stack keeps the name it
            already ran under).
        app_path: The application directory.
        compose_path: The compose file in use.

    Returns:
        The store's project, else the name Compose would derive from the directory (which the stack's
        own ``name:`` replaces); None when the stack names its own project and says it nowhere Noust
        can read.
    """
    from noust.deployers.docker_compose import compose_project_name

    return (
        pinned
        or compose_project_name(app_path, compose_path)
        or (declared_project(compose_path) if compose_path is not None else None)
    )


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


def _reader(deployer: DockerComposeDeployer) -> Any:
    """
    Make what asks Docker for what the stack's containers have.

    Args:
        deployer: The deployer of the stack.

    Returns:
        A function that reads them, for :class:`~noust.deployers.helpers.compose_verdict.Heritage` to
        call when it matters. The project is the one the stack's own commands address, and the gate is
        the one door to Docker.
    """

    def read() -> compose_running.Running:
        from noust.core.docker.gate import DockerGate

        project = stack_project(
            deployer._pinned_project(), deployer.app_path, deployer.compose_path
        )
        project = project or names.project_of_directory(deployer.app_path.name) or "default"
        gate = DockerGate(deployer.runner, scope=Scope.everything())
        return compose_running.read(lambda: gate.project_grants(project), guard_context(deployer))

    return read


def heritage_of(
    deployer: DockerComposeDeployer,
    served: Any = None,
    *,
    changed: frozenset[Path] | None = None,
) -> Heritage:
    """
    Say what a stack that already runs is known to have had.

    Args:
        deployer: The deployer of the stack.
        served: The compose file that served, parsed, judged as written; None when there is none to
            judge (the stack's containers are all that is left to say).
        changed: The links of the tree the commits since changed; None when it cannot be said, which
            is every link.

    Returns:
        The heritage: the findings of the file that served that the commits did not change, and what
        the stack's containers have, asked of Docker the first time it matters.
    """
    sentences: frozenset[str] = frozenset()
    if served is not None:
        findings = inspect_compose(served, deployer.app_path, context=guard_context(deployer))
        sentences = frozenset(compose_verdict.unchanged(findings, changed))
    return Heritage(sentences, changed, _reader(deployer))


def heritage_of_update(deployer: DockerComposeDeployer) -> Heritage:
    """
    Say what a stack had before an update pulled: the file of the commit that served and its containers.

    Args:
        deployer: The deployer of the stack, whose ``previous_commit`` is the commit that served.

    Returns:
        What the stack already had. The containers are known whatever the file that served says, or
        whether it can be read at all.
    """
    compose = deployer._compose_file_path()
    if deployer.previous_commit and compose.is_relative_to(deployer.app_path):
        served = deployer._source_manager().file_at_commit(
            deployer.app_path, deployer.previous_commit, str(compose.relative_to(deployer.app_path))
        )
        if served is not None:
            try:
                return accepted_since(deployer, load_compose(served), deployer.previous_commit)
            except yaml.YAMLError as exc:
                deployer.logger.debug(f"The compose file that served is not valid YAML: {exc}")
    else:
        deployer.logger.debug("No commit served before this update: nothing to compare with")
    return heritage_of(deployer)


def heritage_before_a_redeploy(deployer: DockerComposeDeployer, served: Any) -> Heritage:
    """
    Say what a stack had, before a redeploy replaces the file that served.

    The file on disk is judged before anything is fetched, so what it reaches through a link is what it
    reached when the stack was made and no commit has changed it yet; the containers are compared with
    the tree after the fetch, so a finding of theirs that goes through a link is not inherited (nothing
    says whether the new tree changed the link).

    Args:
        deployer: The deployer of the stack.
        served: The compose file on disk, parsed.

    Returns:
        What the stack already had.
    """
    return Heritage(
        frozenset(refusals_in(deployer, served)), changed_links=None, running=_reader(deployer)
    )


def _heritage(deployer: DockerComposeDeployer, accepted: Any) -> Heritage:
    """
    Args:
        deployer: The deployer of the stack.
        accepted: What the caller proved the stack already ran with: a heritage, the sentences of a
            file, or None to read the deployer's own.

    Returns:
        A heritage; an empty one for a new stack.
    """
    chosen = accepted if accepted is not None else deployer._accepted_refusals
    if isinstance(chosen, Heritage):
        return chosen
    return Heritage(frozenset(chosen or ()))


def check_host_privileges(
    deployer: DockerComposeDeployer,
    document: Any,
    *,
    accepted: Heritage | Sequence[str] | None = None,
    final: bool = False,
) -> None:
    """
    Refuse, or warn about, what in a stack's compose file gives it this server.

    What gives the machine is refused unless an operator recorded an exception for the domain, or
    the stack is proven to run with that very finding already: the compose file that served had it,
    or its containers have it. Only then is it warned about, so the stack is never stopped; whatever a
    push, a redeploy or an adopted directory adds is refused. What belongs to another application or to
    Noust is refused whatever the exception says, and is only warned about for a stack that already had
    it.

    What is judged is the file as written and, unless the file alone already decides it, what
    ``docker compose config`` resolves it to with every profile on.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file.
        accepted: What the stack already ran with (a :class:`Heritage`, or the sentences of the file that
            served); None reads the deployer's ``_accepted_refusals``, which is empty for a new stack.
        final: The stack's own ``.env`` is written, so the model Compose resolves is the one that
            will run: it is always asked for, and a path made of variables that it still cannot
            resolve is refused.

    Raises:
        DeploymentError: The file asks for what the stack did not have, without an exception; or what it
            had could not be told because Docker did not say.
    """
    from noust.deployers.helpers import sandbox as build_sandbox

    context = guard_context(deployer)
    findings = inspect_compose(document, deployer.app_path, context=context)
    exception = build_sandbox.get_compose_exception(deployer.domain, store=deployer.store)
    heritage = _heritage(deployer, accepted)
    command = f"noust app sandbox compose-exception {deployer.domain} --reason '...'"
    try:
        verdict = compose_verdict.decide(findings, already=heritage, exception=exception)
        # A file that is already refused is not read any further: resolving it is how an include that
        # leaves the application would be followed.
        if final or not verdict.stop:
            findings = with_what_compose_resolves(deployer, document, findings, final=final)
            verdict = compose_verdict.decide(findings, already=heritage, exception=exception)
    except DockerUnavailableError as exc:
        lines = "\n".join(f"- {problem}" for problem in heritage.undecided(findings))
        raise DeploymentError.from_text(
            say(
                "compose_guard.cannot_tell_what_it_has",
                domain=deployer.domain,
                lines=lines,
                command=command,
            ),
            output=exc.output or exc.message,
        ) from exc
    for warning in findings.warned:
        deployer.logger.warning(f"Compose: {warning}")
    for problem in verdict.excused:
        deployer.logger.warning(
            f"Compose: {problem}; allowed for {deployer.domain} by "
            f"{exception.allowed_by if exception else ''} ({exception.reason if exception else ''})"
        )
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
) -> Heritage:
    """
    Say what the stack had that an update has not changed.

    The file that served is judged on the tree as it is now, so a link the update added or changed would
    make a line that reached a directory before look as if it always reached what the link leads to: git
    says which links the commits changed, and a finding through one of them is carried over from neither
    the file nor the containers.

    Args:
        deployer: The deployer of the stack.
        served: The compose file of the commit that served, parsed.
        previous_commit: That commit.

    Returns:
        What the stack already had.
    """
    changed = (
        changed_links(deployer._source_manager(), deployer.app_path, previous_commit)
        if previous_commit
        else None
    )
    return heritage_of(deployer, served, changed=changed)


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


@dataclass(frozen=True)
class StackReport:
    """
    What a deployed stack has of the server, for the report of the machine's health.

    Attributes:
        findings: What the file as written asks of the host and what the containers were given, said
            once each, in the guard's words.
        unread: Why the containers could not be read, when they could not; empty otherwise. What they
            have is then not listed, and the report says so.
    """

    findings: ComposeFindings
    unread: str = ""


def stack_findings(app: App) -> StackReport:
    """
    Say what a deployed stack has of the server: by its compose file and by its containers.

    This is the one function behind ``noust health``, the check of the ENS profile and the card of an
    application: the same judgement as an update's, applied to what is there now.

    Args:
        app: The stack's row.

    Returns:
        The findings of the compose file it runs from, as written, and of its containers; the file's
        alone, with the reason, when Docker cannot be asked.
    """
    from noust.deployers.docker_compose import COMPOSE_FILE_PRIORITY

    path = Path(app.app_path)
    compose = next(
        (
            path / name
            for name in COMPOSE_FILE_PRIORITY
            if (path / name).is_file() and not (path / name).is_symlink()
        ),
        None,
    )
    findings = ComposeFindings()
    context = GuardContext(app_path=path, project_dir=compose.parent if compose else path)
    if compose is not None:
        try:
            document = load_compose(compose.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            document = None
        findings = inspect_compose(document, path, context=context)
    project = stack_project(app.compose_project, path, compose) or names.project_of_directory(
        path.name
    )
    from noust.core.docker.gate import DockerGate

    gate = DockerGate(scope=Scope.everything())
    try:
        running = compose_running.read(lambda: gate.project_grants(project or "default"), context)
    except DockerUnavailableError as exc:
        return StackReport(findings, f"{exc.message}: {exc.output}" if exc.output else exc.message)
    return StackReport(merge_findings(findings, running.findings))
