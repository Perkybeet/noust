# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing the Compose stacks of a server that already runs them under the gate, without one failing to start.

A stack that existed before the gate (:mod:`noust.deployers.compose_gate`) has no record of what it was
accepted with and a unit that starts it without asking. What the version it ran under would have started
today is what the stack is accepted with: the model Compose resolves for it now and what its containers
were given. This step writes that down, marked as taken over so that ``noust health`` lists it to be looked
at, and only then writes the unit again with the gate in it and runs ``daemon-reload``. The order is the
promise: a unit never has a gate before its stack has a record, so nothing that started before can fail to
start because of this. Nothing is started, stopped or recreated, and the containers are not touched.

It is idempotent and it is safe to run at any time: the package runs it at an update, the console runs
it again while a stack is still waiting (``noust health`` says which and why), and ``noust setup
compose-gate`` runs it by hand. The monitor does not: it runs with the system read-only and promises to
modify no file. A stack that cannot be resolved (Compose fails, Docker does not answer, a
file is not there) is left exactly as it was, and said; the next pass tries again. A stack whose unit is
not Noust's to write keeps its record and says so.

The same module takes the gate out of the units again (:func:`remove_gates`), which is what going back to a
version that does not know it needs: a unit that runs a command the older Noust does not have would not
start.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from noust.core import compose_acceptance, paths
from noust.core.applock import AppBusyError, app_lock
from noust.core.docker import names
from noust.core.docker.errors import DockerUnavailableError
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandRunner
from noust.core.server_text import say
from noust.core.store import App, AppType, NoustStore, get_store
from noust.core.teams.scope import Scope
from noust.deployers.docker_compose import DockerComposeDeployer, stack_deployer
from noust.deployers.helpers import compose_privileges, compose_running
from noust.deployers.helpers.compose_guard import inspect_compose
from noust.managers.unit_execution import UnitExecution, read_execution
from noust.managers.unit_lines import drop_lines

__all__ = [
    "Effect",
    "Outcome",
    "bring_app",
    "bring_up_to_date",
    "gate_effect",
    "remove_gates",
    "shared_projects",
    "with_gate",
    "without_gate",
]

#: The three commands a stack's unit runs, as the template has always written them.
_START = re.compile(
    r"ExecStart=/usr/bin/docker compose(?: -p (?P<project>\S+))? up -d --remove-orphans"
)
_STOP = re.compile(r"ExecStop=/usr/bin/docker compose(?: -p (?P<project>\S+))? down")
_RELOAD = re.compile(
    r"ExecReload=/usr/bin/docker compose(?: -p (?P<project>\S+))? up -d --remove-orphans --build"
)

#: A line of a unit that runs the gate, in either place and in either form: the command itself (what the
#: first 4.3 builds wrote, which stops a start for ever once Noust is removed) or the line that only
#: runs it where it exists (:func:`compose_privileges.unit_gate`).
_GATE_PRE = re.compile(r"ExecStartPre=.* app compose gate .*")
_GATE_RELOAD = re.compile(r"ExecReload=.* app compose gate .*")

#: The form that tolerates Noust being removed, whichever path it names: a unit that has it is left.
_TOLERANT = re.compile(
    r"Exec(?:StartPre|Reload)=/bin/sh -c '\[ ! -x \S+ \] \|\| exec \S+ app compose gate \S+(?: --project \S+)?'"
)

#: What a pass found of a stack's unit.
WRITTEN, AS_IT_WAS, THEIRS = "written", "as it was", "theirs"

#: What systemd makes of the check a unit's file has: it runs it, an addition of the unit takes it away, or
#: systemd could not be asked.
IN_PLACE, OVERRIDDEN, UNVERIFIED = "in place", "overridden", "unverified"

#: Why systemd runs a unit without a check its file has when no addition explains it.
NOT_LOADED = (
    "systemd runs this unit without the check its file has and no addition of the unit explains it: "
    "it has probably not loaded the unit as it is on disk (run: systemctl daemon-reload)"
)

#: What Compose says in the model it prints, for a stack that could not be resolved.
_UNRESOLVED = {"unresolved", "unresolved_paths"}

COMPOSE_TIMEOUT = 60


@dataclass(frozen=True)
class Outcome:
    """
    What one pass did with one stack.

    Attributes:
        domain: The application.
        state: ``done`` (something was written), ``unchanged`` (it was already under the gate),
            ``own`` (its unit is its operator's, which Noust never rewrites: it was recorded and the unit
            is as it was, and what Noust itself starts is judged all the same), ``overridden`` (its
            unit has the check and an addition of the unit, which Noust never changes, takes it away:
            ``detail`` names the additions; the stack starts without the check, and Noust looks again
            at the next pass), ``unverified`` (systemd could not be asked, or does not run the check and
            nothing explains it: ``detail`` says why; nothing is written for it and the next pass asks
            again), ``pending`` (it could not be brought up to date, and ``detail`` says why; nothing of
            it was changed) or ``busy`` (another operation holds it; the next pass takes it).
        detail: Why, for ``pending``, ``busy`` and ``unverified``; the additions, for ``overridden``.
        recorded: A record of what the stack was accepted with was written.
        unit: The stack's unit was written again, with the gate.
    """

    domain: str
    state: str
    detail: str = ""
    recorded: bool = False
    unit: bool = False


@dataclass(frozen=True)
class Effect:
    """
    What systemd will do with the check a stack's unit has in its file.

    Attributes:
        state: ``IN_PLACE`` (it runs the check before it starts and before a reload rebuilds),
            ``OVERRIDDEN`` (an addition of the unit takes it away) or ``UNVERIFIED`` (systemd could not
            say, or says it runs the unit without the check and nothing explains it).
        paths: The additions systemd read, for ``OVERRIDDEN``.
        directives: Which of ``ExecStartPre=`` and ``ExecReload=`` do not run the check, for
            ``OVERRIDDEN``.
        reason: Why Noust could not tell, for ``UNVERIFIED``.
    """

    state: str
    paths: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()
    reason: str = ""


def _runs_the_check(entry: str, domain: str) -> bool:
    """Say whether one command systemd prints is the check of this application."""
    return re.search(rf" app compose gate {re.escape(domain)}(?: |$)", entry) is not None


def _directives_without_the_check(seen: UnitExecution, domain: str) -> tuple[str, ...]:
    """
    Name the directives of a unit that do not run the check where it has to run.

    Args:
        seen: What systemd runs.
        domain: The application.

    Returns:
        ``ExecStartPre=`` when no command before the start is the check, and ``ExecReload=`` when none
        of a reload is, or when the rebuild comes before it.
    """
    missing: list[str] = []
    if not any(_runs_the_check(entry, domain) for entry in seen.pre):
        missing.append("ExecStartPre=")
    first = next((i for i, entry in enumerate(seen.reload) if _runs_the_check(entry, domain)), None)
    if first is None or any("--build" in entry for entry in seen.reload[:first]):
        missing.append("ExecReload=")
    return tuple(missing)


def gate_effect(deployer: DockerComposeDeployer, text: str | None = None) -> Effect | None:
    """
    Ask systemd whether the check a stack's unit has in its file is what it will run.

    Rule 4: the answer is decided where the unit is written and told wherever it is read, so that
    what Noust says of a stack is what systemd will do when it starts it. Noust never touches an
    addition of the operator's: it says which one.

    Args:
        deployer: The deployer of the stack.
        text: The unit's body, when the caller has read it.

    Returns:
        None when there is nothing to ask: no unit (Docker keeps the stack alive where there is no
        systemd) or a file that has no check in it. Otherwise what systemd makes of it.

    Raises:
        ValidationError: The unit's name is not a safe one.
    """
    services = deployer._units()
    body = services.get_service_config(deployer.app_name) if text is None else text
    if body is None or without_gate(body) == body:
        return None
    seen = read_execution(services, deployer.app_name)
    if seen.problem is not None:
        return Effect(UNVERIFIED, reason=seen.problem)
    missing = _directives_without_the_check(seen, deployer.domain)
    if not missing:
        return Effect(IN_PLACE)
    if seen.drop_ins:
        return Effect(OVERRIDDEN, paths=seen.drop_ins, directives=missing)
    return Effect(UNVERIFIED, reason=NOT_LOADED)


def with_gate(text: str, *, domain: str, executable: str, project: str | None) -> str:
    """
    Write the gate into the text of a stack's unit, and the project every command of it passes.

    Only the lines the template wrote are touched, so what an operator added to the unit stays. A line
    that runs the gate in the form the first builds of 4.3 wrote (the command itself) becomes the line
    that tolerates Noust being removed, where it stands; a unit that already has that form and the
    project is returned as it is, whatever path its line names.

    Args:
        text: The unit's body.
        domain: The application the gate judges.
        executable: Where Noust's command is.
        project: The project the store records for the stack; a unit that already passes one with
            ``-p`` keeps it, and the gate is given that one.

    Returns:
        The new body; the same text for something that is not the unit of a stack.

    Raises:
        ValueError: A value cannot be written into the line of a unit
            (:func:`compose_privileges.unit_gate`).
    """
    lines = text.split("\n")
    starts = [match for line in lines if (match := _START.fullmatch(line))]
    if not starts:
        return text
    unit_project = starts[0].group("project") or project
    command = compose_privileges.unit_gate(executable, domain, unit_project)
    has_pre = any(_GATE_PRE.fullmatch(line) for line in lines)
    has_reload = any(_GATE_RELOAD.fullmatch(line) for line in lines)

    def passing(line: str) -> str:
        if not unit_project or " -p " in line:
            return line
        return line.replace("docker compose", f"docker compose -p {unit_project}", 1)

    out: list[str] = []
    for line in lines:
        if _GATE_PRE.fullmatch(line):
            out.append(line if _TOLERANT.fullmatch(line) else f"ExecStartPre={command}")
        elif _GATE_RELOAD.fullmatch(line):
            out.append(line if _TOLERANT.fullmatch(line) else f"ExecReload={command}")
        elif _START.fullmatch(line):
            if not has_pre:
                out.append(f"ExecStartPre={command}")
            out.append(passing(line))
        elif _RELOAD.fullmatch(line):
            if not has_reload:
                out.append(f"ExecReload={command}")
            out.append(passing(line))
        elif _STOP.fullmatch(line):
            out.append(passing(line))
        else:
            out.append(line)
    return "\n".join(out)


def is_gate_line(line: str) -> bool:
    """
    Say whether a line of a unit is the one that runs the gate, in either form.

    Args:
        line: One line of the unit, without its newline.

    Returns:
        True for the line Noust wrote before ``ExecStart`` or before the rebuild of a reload.
    """
    return bool(_GATE_PRE.fullmatch(line) or _GATE_RELOAD.fullmatch(line))


def without_gate(text: str) -> str:
    """
    Take the gate out of the text of a stack's unit.

    Args:
        text: The unit's body.

    Returns:
        The body without the lines that run the gate: what the template of before it wrote, which a
        Noust that does not have the command starts as it always did.
    """
    return "\n".join(line for line in text.split("\n") if not is_gate_line(line))


def _project_of_today(deployer: DockerComposeDeployer) -> str | None:
    """
    Say the project a stack runs under today: the one its unit has started it as.

    The static answer is the one every command of Noust has passed with ``-p`` (the name of the compose
    file's directory, or the one the stack names itself); a name made of variables only Compose can read,
    so Compose is asked when the file does not say.

    Args:
        deployer: The deployer of the stack, with its compose file found.

    Returns:
        The project, or None when it cannot be told.
    """
    path = deployer.compose_path
    named = compose_privileges.stack_project(None, deployer.app_path, path)
    if named is not None or path is None:
        return named
    # The deployer writes the command, as it does every other one for this stack (its file, its
    # variables and its profiles); with no project recorded yet it passes no -p, so the name Compose
    # prints is the one the stack runs under.
    result = deployer.runner.run(
        deployer._compose("config", "--format", "json"),
        cwd=deployer.app_path,
        timeout=COMPOSE_TIMEOUT,
    )
    if not result.success:
        return None
    try:
        model = json.loads(result.stdout)
    except ValueError:
        return None
    given = model.get("name") if isinstance(model, dict) else None
    return names.project_of_directory(str(given)) if given else None


def _take_over(deployer: DockerComposeDeployer, app: App, store: NoustStore) -> str | None:
    """
    Write down what a stack that was never recorded runs with today, and pin its project.

    Args:
        deployer: The deployer of the stack.
        app: The application.
        store: The store.

    Returns:
        Why the stack cannot be taken over now (and nothing was written), or None once it is.
    """
    from noust.core.docker.gate import DockerGate

    try:
        deployer._discover_compose_file()
    except DeploymentError as exc:
        return exc.message
    document = deployer._load_compose_document()
    if document is None:
        return "its compose file could not be read as YAML"
    project = app.compose_project or _project_of_today(deployer)
    if project is None:
        return "Docker Compose did not say which project it runs as"
    deployer._stored_project = project
    context = compose_privileges.guard_context(deployer)
    found, unresolved = compose_privileges.resolve_findings(
        deployer,
        document,
        inspect_compose(document, deployer.app_path, context=context),
        final=True,
    )
    if unresolved or any(key[1] in _UNRESOLVED for key in found.keys.values()):
        return f"Docker Compose could not resolve the stack ({unresolved or 'what its files bring in'})"
    gate = DockerGate(deployer.runner, scope=Scope.everything())
    try:
        running = compose_running.read(
            lambda: gate.project_grants(project, within=deployer.app_path), context
        )
    except DockerUnavailableError as exc:
        return f"Docker did not say what its containers have: {exc.message}"
    keys = compose_privileges.keys_of(found) | compose_privileges.keys_of(running.findings)
    try:
        if app.compose_project is None:
            store.set_app_compose_project(app.domain, project)
    except ValidationError as exc:
        return f"its project {project!r} is not a name Compose accepts: {exc.message}"
    compose_acceptance.record(
        app.domain, keys, source="upgrade", inherited=True, replace=True, store=store
    )
    return None


def is_theirs(text: str) -> bool:
    """
    Say whether a stack's unit is its operator's: it does not carry the line that says Noust wrote it.

    Noust never rewrites such a unit (``docs/leaving.md``), so it puts no gate in one.

    Args:
        text: The unit's body.

    Returns:
        True when neither ``# Generated by Noust`` nor the line of before 3.0 is in it.
    """
    return not paths.carries_unit_marker(text)


def _write_unit(deployer: DockerComposeDeployer, project: str | None) -> str:
    """
    Write a stack's unit again with the gate, when it has a unit that is Noust's and it lacks it.

    Args:
        deployer: The deployer of the stack.
        project: The project the store records for it.

    Returns:
        ``WRITTEN``; ``AS_IT_WAS`` for a machine that has no unit or one that is as it should be; or
        ``THEIRS`` for a unit its operator made theirs, which is left exactly as it is.

    Raises:
        ServiceError: The unit cannot be written.
        ValueError: Noust's command is at a path a unit's line cannot carry.
    """
    services = deployer._units()
    text = services.get_service_config(deployer.app_name)
    if text is None or deployer.APP_TYPE != "docker-compose":
        return AS_IT_WAS
    if is_theirs(text):
        return THEIRS
    new = with_gate(
        text,
        domain=deployer.domain,
        executable=compose_privileges.gate_executable(),
        project=project,
    )
    if new == text:
        return AS_IT_WAS
    services.rewrite_unit(deployer.app_name, new)
    return WRITTEN


def bring_app(
    app: App, *, store: NoustStore | None = None, runner: CommandRunner | None = None
) -> Outcome:
    """
    Bring one stack under the gate, if it is not.

    Args:
        app: The application, of the Docker Compose type.
        store: The store; the process-wide one by default.
        runner: What runs ``docker``; the process-wide runner by default.

    Returns:
        What was done.
    """
    target = store or get_store()
    try:
        with app_lock(app.domain, "compose gate"):
            return _bring(app, target, runner)
    except AppBusyError as exc:
        return Outcome(app.domain, "busy", exc.message)


def _bring(app: App, store: NoustStore, runner: CommandRunner | None) -> Outcome:
    """
    Do :func:`bring_app` with the stack held.

    Args:
        app: The application.
        store: The store.
        runner: What runs ``docker``.

    Returns:
        What was done.
    """
    try:
        deployer = stack_deployer(app, runner=runner)
        deployer.store = store
        unrecorded = compose_acceptance.get(app.domain, store=store) is None
        if unrecorded:
            problem = _take_over(deployer, app, store)
            if problem is not None:
                return _pending(store, app.domain, problem)
        fresh = store.get_app(app.domain) or app
        how = _write_unit(deployer, fresh.compose_project)
    except (NoustError, OSError, sqlite3.Error, ValueError) as exc:
        return _pending(store, app.domain, exc.message if isinstance(exc, NoustError) else str(exc))
    note = compose_acceptance.note_of(app.domain, store=store)
    if note is not None and note.state == "pending":
        compose_acceptance.clear_note(app.domain, store=store)
    # What the file says is not yet what systemd runs: an addition of the unit may take the check away.
    try:
        effect = gate_effect(deployer)
    except (NoustError, OSError) as exc:
        return _pending(store, app.domain, exc.message if isinstance(exc, NoustError) else str(exc))
    if effect is not None and effect.state == OVERRIDDEN:
        return Outcome(
            app.domain,
            OVERRIDDEN,
            ", ".join(effect.paths),
            recorded=unrecorded,
            unit=how == WRITTEN,
        )
    if effect is not None and effect.state == UNVERIFIED:
        return Outcome(
            app.domain, UNVERIFIED, effect.reason, recorded=unrecorded, unit=how == WRITTEN
        )
    if how == THEIRS:
        return Outcome(app.domain, "own", recorded=unrecorded)
    if unrecorded or how == WRITTEN:
        return Outcome(app.domain, "done", recorded=unrecorded, unit=how == WRITTEN)
    return Outcome(app.domain, "unchanged")


def _pending(store: NoustStore, domain: str, why: str) -> Outcome:
    """
    Say that a stack was left as it was, and why.

    Args:
        store: The store.
        domain: The application.
        why: The reason.

    Returns:
        The outcome.
    """
    compose_acceptance.note(domain, "pending", why, store=store)
    return Outcome(domain, "pending", why)


def _stacks(store: NoustStore, only: str | None) -> list[App]:
    """Return the Compose applications, or the one asked for."""
    return sorted(
        (
            app
            for app in store.list_apps()
            if app.app_type == AppType.DOCKER_COMPOSE.value and (only is None or app.domain == only)
        ),
        key=lambda app: app.domain,
    )


def bring_up_to_date(
    *,
    store: NoustStore | None = None,
    runner: CommandRunner | None = None,
    only: str | None = None,
) -> list[Outcome]:
    """
    Bring every Compose stack of the server under the gate.

    Args:
        store: The store; the process-wide one by default.
        runner: What runs ``docker``; the process-wide runner by default.
        only: One application's domain; every stack when None.

    Returns:
        What was done with each, in order; nothing in a rehearsal.
    """
    if is_rehearsal():
        return []
    target = store or get_store()
    return [bring_app(app, store=target, runner=runner) for app in _stacks(target, only)]


def remove_gates(
    *,
    store: NoustStore | None = None,
    runner: CommandRunner | None = None,
    only: str | None = None,
    on_each: Callable[[str], None] | None = None,
) -> list[str]:
    """
    Take the gate out of the unit of every stack that has it.

    What going back to a version that does not know the command needs, and what removing Noust does
    (``noust setup compose-gate --remove`` runs from the package's removal script), also for a unit its
    operator made theirs: the line is Noust's, and nothing else of that unit is touched. The records
    stay, so that bringing the stacks up again changes nothing.

    Args:
        store: The store; the process-wide one by default.
        runner: What runs ``docker``; the process-wide runner by default.
        only: One application's domain; every stack when None.
        on_each: Told the domain of each unit that was written.

    Returns:
        The domains whose unit was written.
    """
    if is_rehearsal():
        return []
    target = store or get_store()
    changed: list[str] = []
    for app in _stacks(target, only):
        deployer = stack_deployer(app, runner=runner)
        services = deployer._units()
        text = services.get_service_config(deployer.app_name)
        if text is None or without_gate(text) == text:
            continue
        # The line is Noust's wherever it stands, also in a unit its operator made theirs.
        if drop_lines(services, deployer.app_name, is_gate_line):
            changed.append(app.domain)
            if on_each is not None:
                on_each(app.domain)
    return changed


def shared_projects(*, store: NoustStore | None = None) -> dict[str, tuple[str, ...]]:
    """
    List the Compose projects that more than one application runs as.

    Two applications whose file is in a subdirectory of the same name ran as one project before a new
    application got its own: what one recreates the other loses. Neither is changed (their volumes are
    named after the project); ``noust health`` says it, with both.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        Project to the domains that use it, for those that have more than one.
    """
    found: dict[str, list[str]] = {}
    for app in _stacks(store or get_store(), None):
        if app.compose_project:
            found.setdefault(app.compose_project, []).append(app.domain)
    return {project: tuple(domains) for project, domains in found.items() if len(domains) > 1}


def _said(code: str, **params: str) -> str:
    """Say a coded sentence as a line of ``noust health``: what happens, then what to do about it."""
    sentence = say(code, **params)
    return f"{sentence.text}. {sentence.hint}" if sentence.hint else sentence.text


def _start_sentences(store: NoustStore, runner: CommandRunner | None) -> list[str]:
    """
    Name the stacks that start without the check, one sentence each, and why.

    A stack whose unit is its operator's and has no check, one whose unit has it in its file but an
    addition of the unit takes it away, and one whose systemd could not be asked.

    Args:
        store: The store.
        runner: What runs ``systemctl`` to read a unit.

    Returns:
        The sentences; a unit that cannot be read is not named (it is not known to start without it).
    """
    said: list[str] = []
    for app in _stacks(store, None):
        try:
            deployer = stack_deployer(app, runner=runner)
            text = deployer._units().get_service_config(deployer.app_name)
            effect = gate_effect(deployer, text)
        except (NoustError, OSError):
            continue
        retry = f"noust setup compose-gate --app {app.domain}"
        if effect is None:
            if text is not None and is_theirs(text):
                said.append(_said("compose_gate.own", domain=app.domain, command=retry))
        elif effect.state == OVERRIDDEN:
            said.append(
                _said(
                    "compose_gate.overridden",
                    domain=app.domain,
                    paths=", ".join(effect.paths),
                    directives=", ".join(effect.directives),
                    command="systemctl daemon-reload",
                    retry_command=retry,
                )
            )
        elif effect.state == UNVERIFIED:
            said.append(
                _said(
                    "compose_gate.unverified",
                    domain=app.domain,
                    reason=effect.reason,
                    command=f"systemctl show {deployer.app_name}.service -p ExecStartPre -p ExecReload",
                    retry_command=retry,
                )
            )
    return said


def attention(*, store: NoustStore | None = None, runner: CommandRunner | None = None) -> list[str]:
    """
    Say what the operator is to look at about how the stacks of this server start.

    The sentences ``noust health`` shows: a stack not yet brought under the gate (and why), a start the
    gate held back (with the line that is new), a stack whose record was taken over from what the
    version before the gate had started, a stack whose unit is its operator's (it starts without the
    check) and applications that run as one project.

    Args:
        store: The store; the process-wide one by default.
        runner: What runs ``systemctl`` to read a unit; the process-wide runner by default.

    Returns:
        One sentence each; none when everything is as it should be.
    """
    from noust.deployers.helpers import sandbox

    target = store or get_store()
    if not _stacks(target, None):
        return []
    said: list[str] = []
    for domain, note in compose_acceptance.notes(store=target).items():
        detail = "; ".join(line.removeprefix("- ") for line in note.detail.splitlines())
        if note.state == "pending":
            said.append(
                f"{domain}: Noust has not brought this stack under the check of its start yet "
                f"({detail}). It starts as it always did, and Noust tries again by itself; or "
                f"run: noust setup compose-gate --app {domain}"
            )
        else:
            said.append(
                f"{domain}: its last start was held back ({detail}). Deploy it again, which "
                f"judges the change (noust update {domain}), or record why it needs it: "
                f"noust app sandbox compose-exception {domain} --reason '...'"
            )
    for domain in compose_acceptance.inherited_domains(store=target):
        accepted = compose_acceptance.get(domain, store=target)
        if (
            accepted
            and accepted.keys
            and sandbox.get_compose_exception(domain, store=target) is None
        ):
            said.append(
                f"{domain}: what it ran with before 4.3 ({len(accepted.keys)} thing(s) that give a "
                "container this server) was taken over as accepted, so that it goes on starting. "
                f"Look at it: remove what it does not need, or record why it does: noust app "
                f"sandbox compose-exception {domain} --reason '...'"
            )
    said.extend(_start_sentences(target, runner))
    for project, domains in shared_projects(store=target).items():
        said.append(
            f"{' and '.join(domains)} run as one Compose project, {project}: what one recreates "
            "the other loses. Nothing was renamed (the volumes are named after the project); to "
            "separate them, deploy one again as a new application, which runs as its own name."
        )
    return said
