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

**What a stack already had** is what it was accepted with (every activation that passed this guard writes
down in the store what the stack was given of the server, :mod:`noust.core.compose_acceptance`) and what its
containers were given (:mod:`noust.deployers.helpers.compose_running`): a stack whose mount is
``${DATA_DIR}:/data`` shows nothing in the file as written, and resolving the file with the ``.env`` of today
would let anyone who can change a variable say the stack always had what it now names. The tree is not a
record, and is never read as one: a container that mounts it writes the compose file and the ``.git`` folder
the file that served would be read from. A stack with no record has its containers and nothing else, and a
stack with neither is a new stack; the upgrade writes the record of every stack that runs
(:mod:`noust.deployers.compose_upgrade`), from its containers when Compose cannot resolve its file. Docker is
asked only when a finding that the record does not cover could be vouched for by a container, and once. The
same judgement holds a stack's start to what was judged (:mod:`noust.deployers.compose_gate`).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from noust.core import compose_acceptance
from noust.core.compose_yaml import load_compose
from noust.core.docker import names
from noust.core.docker.errors import DockerUnavailableError
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.server_text import say
from noust.core.teams.scope import Scope
from noust.deployers.helpers import compose_running, compose_verdict
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    FindingKey,
    GuardContext,
    inspect_compose,
    merge_findings,
    with_resolved,
)
from noust.deployers.helpers.compose_owners import Owners, declared_project
from noust.deployers.helpers.compose_verdict import Heritage

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


def own_project(deployer: DockerComposeDeployer, existing: App | None) -> None:
    """
    Give a new application's stack the Compose project of its own name.

    What Compose derives from the directory of the compose file is the name of whatever directory
    that is, and two applications whose file is in a subdirectory of the same name would run as one
    project: the second recreates the first's services, mounts its volumes and, with
    ``--remove-orphans``, removes the rest. The application's name is its own, and the store records it
    (:meth:`~noust.deployers.helpers.registration.StoreRegistrar.register_app`), so that every command
    and the unit pass it with ``-p`` and no variable of the stack's can change it. An application that
    exists keeps what it has: renaming a project is renaming its volumes.

    Args:
        deployer: The deployer of the stack, about to deploy.
        existing: The application's row, or None for a new one.
    """
    if existing is None:
        deployer._stored_project = names.project_of_directory(deployer.app_name)


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
        return compose_running.read(
            lambda: gate.project_grants(project, within=deployer.app_path), guard_context(deployer)
        )

    return read


def heritage_of(deployer: DockerComposeDeployer) -> Heritage:
    """
    Say what a stack that already runs is known to have had, from its containers and nothing else.

    Args:
        deployer: The deployer of the stack.

    Returns:
        The heritage: what the stack's containers have, asked of Docker the first time it matters. A
        stack with no container has nothing: it is a new stack. Every link counts as changed, since
        nothing says which ones are new.
    """
    return Heritage(frozenset(), None, _reader(deployer))


def _heritage_of_what_it_was_given(deployer: DockerComposeDeployer) -> Heritage:
    """
    Say what a stack had: what it was accepted with, else what its containers were given.

    A stack has an entry in the store once an activation of this version passed the guard or the
    upgrade took it over (:mod:`noust.deployers.compose_upgrade`, which takes a stack from its
    containers when Compose cannot resolve its file). The entry and the containers are the only
    witnesses nothing in the stack's tree can write: the compose file that served and the one on disk
    are written by whatever mounts the tree, ``.git`` included, and a stack with neither an entry nor
    a container is a new stack.

    Args:
        deployer: The deployer of the stack.

    Returns:
        The heritage made of the entry (when there is one) and the containers.
    """
    accepted = compose_acceptance.get(deployer.domain, store=deployer.store)
    if accepted is None:
        return heritage_of(deployer)
    return Heritage(changed_links=None, running=_reader(deployer), keys=accepted.keys)


def heritage_of_update(deployer: DockerComposeDeployer) -> Heritage:
    """
    Say what a stack had before an update pulled: what it was accepted with and its containers.

    Args:
        deployer: The deployer of the stack.

    Returns:
        What the stack already had; of a stack with no entry in the store, what its containers were
        given and nothing else.
    """
    return _heritage_of_what_it_was_given(deployer)


def heritage_of_redeploy(deployer: DockerComposeDeployer) -> Heritage:
    """
    Say what a stack had, before a redeploy replaces its compose file.

    The file on disk is not a witness (the stack's own code can write it), and neither is the file
    resolved with the ``.env`` of today, since whoever can change a variable would say the stack always
    had what the variable names.

    Args:
        deployer: The deployer of the stack.

    Returns:
        What the stack already had.
    """
    return _heritage_of_what_it_was_given(deployer)


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


@dataclass(frozen=True)
class Judgement:
    """
    What the guard made of a stack's compose file.

    Attributes:
        findings: What the file asks of the host, with what Compose resolves it to when that was asked.
        verdict: What to do with it.
        exception: The exception recorded for the application, if any.
        unavailable: Docker could not say what the stack's containers have, which the verdict needed.
        undecided: Then, the sentences only a container could have vouched for.
    """

    findings: ComposeFindings
    verdict: compose_verdict.Verdict
    exception: Any
    unavailable: DockerUnavailableError | None = None
    undecided: tuple[str, ...] = ()


def _exception_command(domain: str) -> str:
    """Name the command that records why a stack needs what gives it this server."""
    return f"noust app sandbox compose-exception {domain} --reason '...'"


def judge(
    deployer: DockerComposeDeployer,
    document: Any,
    *,
    heritage: Heritage,
    final: bool = False,
) -> Judgement:
    """
    Judge a stack's compose file and say what to do with it, without stopping or logging anything.

    The one judgement a deployment, an update, a redeploy, an adoption and the start of a stack's
    unit all make (rule 3): what differs is the heritage each brings.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file.
        heritage: What the stack already had.
        final: See :func:`check_host_privileges`.

    Returns:
        The judgement.
    """
    from noust.deployers.helpers import sandbox as build_sandbox

    context = guard_context(deployer)
    findings = inspect_compose(document, deployer.app_path, context=context)
    exception = build_sandbox.get_compose_exception(deployer.domain, store=deployer.store)
    try:
        verdict = compose_verdict.decide(findings, already=heritage, exception=exception)
        # A file that is already refused is not read any further: resolving it is how an include that
        # leaves the application would be followed.
        if final or not verdict.stop:
            findings = with_what_compose_resolves(deployer, document, findings, final=final)
            verdict = compose_verdict.decide(findings, already=heritage, exception=exception)
    except DockerUnavailableError as exc:
        return Judgement(
            findings,
            compose_verdict.Verdict(),
            exception,
            unavailable=exc,
            undecided=tuple(heritage.undecided(findings)),
        )
    return Judgement(findings, verdict, exception)


def check_host_privileges(
    deployer: DockerComposeDeployer,
    document: Any,
    *,
    accepted: Heritage | Sequence[str] | None = None,
    final: bool = False,
    record: str | None = None,
) -> None:
    """
    Refuse, or warn about, what in a stack's compose file gives it this server.

    What gives the machine is refused unless an operator recorded an exception for the domain, or
    the stack is proven to run with that very finding already: what it was accepted with has it, or its
    containers do. Only then is it warned about, so the stack is never stopped; whatever a
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
        record: How the activation that follows comes to be (``deploy``, ``update``, ``release``,
            ``adopt``...). Given, a stack that passes has what it was given of the server written down
            in the store (:mod:`noust.core.compose_acceptance`), which is what its unit's start and
            every restart are compared with. Nothing is written for a stack that is stopped.

    Raises:
        DeploymentError: The file asks for what the stack did not have, without an exception; or what it
            had could not be told because Docker did not say.
    """
    judged = judge(deployer, document, heritage=_heritage(deployer, accepted), final=final)
    findings, verdict, exception = judged.findings, judged.verdict, judged.exception
    command = _exception_command(deployer.domain)
    if judged.unavailable is not None:
        exc = judged.unavailable
        raise DeploymentError.from_text(
            say(
                "compose_guard.cannot_tell_what_it_has",
                domain=deployer.domain,
                lines="\n".join(f"- {problem}" for problem in judged.undecided),
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
        if record:
            _remember(deployer, findings, record)
        return
    lines = "\n".join(f"- {problem}" for problem in verdict.hard or verdict.stop)
    if verdict.hard:
        raise DeploymentError.from_text(
            say("compose_guard.asks_for_what_no_stack_may", domain=deployer.domain, lines=lines)
        )
    raise DeploymentError.from_text(
        say("compose_guard.asks_for_root", domain=deployer.domain, lines=lines, command=command)
    )


#: What may be written into the line of a unit that runs the gate: no quote, space, ``%`` or ``$`` (which
#: systemd or the shell would read), nothing that ends the line.
_UNIT_SAFE = re.compile(r"[A-Za-z0-9_./+:@,=-]+")

#: Where Noust is when nothing says otherwise.
DEFAULT_EXECUTABLE = "/usr/bin/noust"


def gate_executable() -> str:
    """
    Say where Noust's command is, as the line of a unit that runs the gate names it.

    Returns:
        The absolute path ``noust`` has on this machine, or the place the packages put it.
    """
    from noust.core.utils import find_noust_executable

    return find_noust_executable() or DEFAULT_EXECUTABLE


def unit_gate(executable: str, domain: str, project: str | None) -> str:
    """
    Write the command a stack's unit runs before ``docker compose up``, as the line of the unit.

    A unit outlives the package that wrote it: ``apt remove noust`` (or ``dnf``, ``zypper``, ``pip
    uninstall``) touches nothing else, and a line that ran a command that is no longer there would stop
    every stack from starting for ever. So the line runs Noust's command only where it exists
    (``[ ! -x EXECUTABLE ]`` is true when it does not, and the line ends well: the stack starts as it did
    before 4.3, since without Noust there is nothing to judge with) and replaces itself with the command
    where it does (``exec``), so that what the command ends with is what the line ends with: a gate that
    refuses, or a Noust too old to know the command, is a start that does not happen. Everything is
    absolute paths and in the line itself, never in a file that could go with the package.

    Args:
        executable: Where Noust's command is.
        domain: The application.
        project: What the unit passes with ``-p``; None for a stack that leaves it to Compose.

    Returns:
        What follows ``ExecStartPre=`` (and ``ExecReload=``): ``/bin/sh -c '[ ! -x EXE ] || exec EXE app
        compose gate DOMAIN --project P'``.

    Raises:
        ValueError: A value cannot be written into the line without a shell or systemd reading it as
            something else. A path that cannot is never swapped for another: the gate would then look
            for Noust where it is not and let every start through.
    """
    for value in (executable, domain, project or "x"):
        if _UNIT_SAFE.fullmatch(value) is None:
            raise ValueError(f"{value!r} cannot be written into the line of a unit")
    command = f"{executable} app compose gate {domain}" + (
        f" --project {project}" if project else ""
    )
    return f"/bin/sh -c '[ ! -x {executable} ] || exec {command}'"


def gate_command(deployer: DockerComposeDeployer, project: str | None) -> str | None:
    """
    Write the command a stack's unit runs before ``docker compose up``.

    Args:
        deployer: The deployer of the stack. A container application's compose file is rendered by
            Noust from its own settings and is not a repository's, so there is nothing to hold its
            start to.
        project: What the unit passes with ``-p``; None for a stack that leaves it to Compose.

    Returns:
        The line (:func:`unit_gate`), whose directory, compose file and profiles are the unit's own
        working directory and environment; None for a deployer that is not a repository's stack.

    Raises:
        ValueError: As :func:`unit_gate`.
    """
    if deployer.APP_TYPE != "docker-compose":
        return None
    return unit_gate(gate_executable(), deployer.domain, project)


def keys_of(findings: ComposeFindings) -> frozenset[FindingKey]:
    """
    Name what a stack was given of the server, as the guard names each finding.

    Args:
        findings: What the guard found.

    Returns:
        The identity of every refused finding (an exception or a heritage allowed it; the others
        stopped the stack and never get here), with the real place each mount leads to beside the
        spelled one.
    """
    keys = {key for sentence in findings.refused if (key := findings.keys.get(sentence))}
    return compose_running.with_real_paths(keys)


def _remember(deployer: DockerComposeDeployer, findings: ComposeFindings, source: str) -> None:
    """
    Write down what a stack that passed the guard is given of the server.

    Added to what was accepted before, until the stack serves (:func:`settle`): an activation that
    fails goes back to the stack it replaced, and that one must still be accepted.

    Args:
        deployer: The deployer of the stack.
        findings: What the guard found, judged on the model that will run.
        source: How the activation comes to be.
    """
    keys = keys_of(findings)
    deployer._accepting = keys
    compose_acceptance.record(
        deployer.domain,
        keys,
        source=source,
        actor=getattr(deployer, "trigger", "") or "",
        store=deployer.store,
    )


def forget_rolled_back(deployer: DockerComposeDeployer) -> None:
    """
    Clean the store after a first deployment that failed.

    An application that was registered goes, with everything kept for its name (its compose exception
    included). One that never got as far had only what the guard wrote down, which goes too, and the
    exception an operator recorded ahead of the first deployment stays for the next attempt.

    Args:
        deployer: The deployer of the stack.
    """
    if deployer.store.get_app(deployer.domain) is not None:
        deployer.store.delete_app(deployer.domain)
    else:
        compose_acceptance.forget_record(deployer.domain, store=deployer.store)


def settle(deployer: DockerComposeDeployer) -> None:
    """
    Keep as accepted only what the stack that now serves was given.

    Args:
        deployer: The deployer of a stack whose containers passed the health gate.
    """
    keys = getattr(deployer, "_accepting", None)
    if keys is None:
        return
    entry = compose_acceptance.get(deployer.domain, store=deployer.store)
    if entry is not None:
        compose_acceptance.record(
            deployer.domain,
            keys,
            source=entry.source,
            actor=entry.actor,
            replace=True,
            store=deployer.store,
        )


def settled(deployer: DockerComposeDeployer, outcome: tuple[bool, str]) -> tuple[bool, str]:
    """
    Settle what a stack was accepted with when its activation passed.

    Args:
        deployer: The deployer of the stack.
        outcome: Whether the activation passed, and the evidence when it did not.

    Returns:
        The outcome, as it came.
    """
    if outcome[0]:
        settle(deployer)
    return outcome


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
    merged, unresolved = resolve_findings(deployer, document, findings, final=final)
    if unresolved:
        deployer.logger.info(f"Compose: {unresolved}")
    return merged


def resolve_findings(
    deployer: DockerComposeDeployer,
    document: Any,
    findings: ComposeFindings,
    *,
    final: bool = False,
) -> tuple[ComposeFindings, str]:
    """
    Do what :func:`with_what_compose_resolves` does, and say whether Compose could not resolve it.

    Args:
        deployer: The deployer of the stack.
        document: The parsed compose file.
        findings: What the file as written asks of the host.
        final: See :func:`check_host_privileges`.

    Returns:
        The findings with what the model adds, and a sentence when Compose could not resolve the stack
        and what was judged is the file as written (empty when the model is what was judged).
    """
    if not deployer.compose_path:
        return findings, "there is no compose file to resolve"
    return with_resolved(
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
        running = compose_running.read(
            lambda: gate.project_grants(project or "default", within=path), context
        )
    except DockerUnavailableError as exc:
        return StackReport(findings, f"{exc.message}: {exc.output}" if exc.output else exc.message)
    return StackReport(merge_findings(findings, running.findings))
