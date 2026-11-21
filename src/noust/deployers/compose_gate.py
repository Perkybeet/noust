# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The start of a Compose stack is held to what was judged.

A deployment is judged by the guard, and the stack's unit runs ``docker compose up -d`` again at every
start (and at every restart, and after the machine reboots) with the files and the variables that are
there then. Whoever can write the stack's ``.env`` (``PUT /api/apps/{d}/env``), a file of its tree or a
variable, and can restart it, changes what Compose resolves without anybody judging it. The gate is where
that is stopped (rule 4: a rule enforced in the callers has as many holes as there are callers):

- the **unit** runs ``noust app compose gate`` before ``up`` (``ExecStartPre``, and before the rebuild of
  ``ExecReload``), so whoever starts the stack, systemd at boot included, goes through it;
- every path of Noust that starts or restarts a stack asks :func:`check_before` first
  (:class:`~noust.managers.service_manager.ServiceManager` and the supervisor of a machine with no
  systemd), so a refusal leaves a running stack running instead of stopping it and then failing to start
  it: a restart of a oneshot unit is a ``down`` and an ``up``;
- the one writer of the stack's ``.env`` asks before it writes (:func:`check_environment`).

All three resolve the model Compose will run as it is now with
:func:`~noust.deployers.helpers.compose_privileges.judge`, the function that judges a deployment, and
compare it with what the stack was accepted with (:mod:`noust.core.compose_acceptance`) and the exception
recorded for it. What adds something that gives the server to a container is held back, saying which line
is new and the two ways out (deploy it again, which judges it; or record why it needs it). What cannot be
judged is held back too, as the sandbox fails closed. A stack with no record starts as it always did, and
says so: the record is what an upgrade or a deployment writes, and a unit only carries the gate once its
stack has one (:mod:`noust.deployers.compose_upgrade`).

The database engines Noust runs in containers (``managers/database/managed.py``) and Noust's own project
(``core/docker/project.py``) are not applications and carry no gate: nothing a repository, an ``.env`` or
a tree the operator does not own can write reaches their compose files, which Noust renders itself.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import compose_acceptance, sealing
from noust.core.exceptions import (
    DeploymentError,
    NoustError,
    StartHeldBackError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner
from noust.core.secrets import secrets_dir
from noust.core.server_text import ServerText, say
from noust.core.store import AppType, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.docker_compose import (
    DockerComposeDeployer,
    compose_file_option,
    stack_deployer,
)
from noust.deployers.helpers import compose_privileges
from noust.deployers.helpers.compose_verdict import Heritage

if TYPE_CHECKING:
    from noust.core.store import App

__all__ = [
    "GATE_COMMAND",
    "Outcome",
    "check_before",
    "check_environment",
    "judge",
    "judge_app",
    "judge_from_unit",
    "require",
]

#: What the unit runs, after ``noust``, before ``docker compose up``.
GATE_COMMAND = ("app", "compose", "gate")


@dataclass(frozen=True)
class Outcome:
    """
    What the gate decided about one start.

    Attributes:
        allowed: The stack may start.
        lines: When it may not, each line that is new, as the guard says it.
        unrecorded: It may, because nothing recorded what the stack was accepted with: it starts as it
            always did, and what the operator sees says so.
        domain: The application.
        problem: When it may not because what Compose resolves to could not be judged, why.
    """

    allowed: bool
    lines: tuple[str, ...] = ()
    unrecorded: bool = False
    domain: str = ""
    problem: str = ""

    def text(self) -> ServerText:
        """
        Returns:
            The sentence of a stack that was held back.
        """
        update = f"noust update {self.domain}"
        if self.problem:
            return say(
                "compose_gate.cannot_tell",
                domain=self.domain,
                problem=self.problem,
                update_command=update,
            )
        return say(
            "compose_gate.refused",
            domain=self.domain,
            lines="\n".join(self.lines),
            update_command=update,
            command=f"noust app sandbox compose-exception {self.domain} --reason '...'",
        )

    def error(self, kind: type[NoustError] = DeploymentError) -> NoustError:
        """
        Make the error a stack that was held back is refused with.

        Args:
            kind: The class of the error, for the caller's own layer (a start is a ``StartHeldBackError``).

        Returns:
            The error, carrying the sentence.
        """
        return kind.from_text(self.text())


def _held(deployer: DockerComposeDeployer, problem: str) -> Outcome:
    """Say that a stack could not be judged, and so does not start."""
    return Outcome(False, domain=deployer.domain, problem=problem)


def judge(deployer: DockerComposeDeployer, *, note: bool = False) -> Outcome:
    """
    Decide whether a stack may start, from what Compose resolves for it now.

    Args:
        deployer: The deployer of the stack, with its directory, compose file, profiles and project
            set the way the unit starts it.
        note: Leave what was decided where ``noust health`` finds it (a refusal is noted, a start that
            passes takes a refusal's note away).

    Returns:
        The decision.
    """
    domain = deployer.domain
    accepted = compose_acceptance.get(domain, store=deployer.store)
    if accepted is None:
        return Outcome(True, unrecorded=True, domain=domain)
    try:
        deployer._discover_compose_file()
    except DeploymentError as exc:
        outcome = _held(deployer, exc.message)
    else:
        document = deployer._load_compose_document()
        if document is None:
            outcome = _held(deployer, "the compose file could not be read as YAML")
        else:
            judged = compose_privileges.judge(
                deployer, document, heritage=Heritage(keys=accepted.keys), final=True
            )
            lines = tuple(f"- {sentence}" for sentence in judged.verdict.stop)
            outcome = Outcome(not lines, lines, domain=domain)
    if note:
        _note(deployer.store, outcome)
    return outcome


def _note(store: NoustStore, outcome: Outcome) -> None:
    """
    Leave the decision where ``noust health`` finds it.

    Args:
        store: The store.
        outcome: What the gate decided.
    """
    existing = compose_acceptance.note_of(outcome.domain, store=store)
    if not outcome.allowed:
        said = "\n".join([outcome.text().text, *outcome.lines])
        compose_acceptance.note(outcome.domain, "refused", said, store=store)
    elif existing is not None and existing.state == "refused":
        compose_acceptance.clear_note(outcome.domain, store=store)


def judge_app(
    app: App, *, runner: CommandRunner | None = None, store: NoustStore | None = None
) -> Outcome:
    """
    Decide whether a deployed stack may start, reaching it the one way every reader of a stack does.

    Args:
        app: The stack's row.
        runner: What runs ``docker compose``; the process-wide runner by default.
        store: The store the record is read from; the process-wide one by default.

    Returns:
        The decision.
    """
    deployer = stack_deployer(app, runner=runner)
    if store is not None:
        deployer.store = store
    return judge(deployer, note=True)


def judge_from_unit(
    domain: str,
    *,
    directory: Path,
    compose_file: str | None,
    profiles: Sequence[str],
    project: str | None,
    runner: CommandRunner | None = None,
) -> Outcome:
    """
    Decide whether a stack may start, from what its unit says it starts.

    What ``noust app compose gate`` does when the unit runs it: the stack may not be in the store yet
    (a first deployment starts its unit before it registers the application), so nothing is read from
    a row.

    Args:
        domain: The application.
        directory: The unit's working directory: where the stack's tree is.
        compose_file: The unit's ``COMPOSE_FILE``, relative to the tree; None for the default names.
        profiles: The unit's ``COMPOSE_PROFILES``.
        project: What the unit passes with ``-p``; None when it passes none.
        runner: What runs ``docker compose``; the process-wide runner by default.

    Returns:
        The decision.

    Raises:
        ValidationError: The compose file named is not a relative path inside the tree.
    """
    deployer = DockerComposeDeployer(runner=runner)
    deployer.domain = domain
    deployer.app_name = domain_to_app_name(domain)
    deployer.app_path = directory
    deployer.compose_file = str(compose_file_option(compose_file)) if compose_file else None
    deployer.compose_profiles = [name for name in profiles if name]
    # What the unit passes with -p is the project, whatever the store says or lacks.
    deployer._stored_project = project or ""
    return judge(deployer, note=True)


def stack_named(unit: str, *, store: NoustStore | None = None) -> App | None:
    """
    Find the Compose application a unit stands for.

    Args:
        unit: The unit's name, with or without ``.service``: the application's name (its domain with
            dashes), or the directory it lives in when an older unit kept its prefix.
        store: The store; the process-wide one by default.

    Returns:
        The application's row, or None when no application of that type has the name (a first
        deployment has no row yet; its unit's own start asks the gate).
    """
    name = unit.removesuffix(".service")
    for app in (store or get_store()).list_apps():
        if app.app_type != AppType.DOCKER_COMPOSE.value:
            continue
        if name in (domain_to_app_name(app.domain), Path(app.app_path or "").name):
            return app
    return None


def require(deployer: DockerComposeDeployer) -> None:
    """
    Refuse to start something of a stack that would not pass the gate, for a caller that has its deployer.

    What starts a service of the stack without going through its unit (a restore that brings the
    database's service up alone) asks here first.

    Args:
        deployer: The deployer of the stack.

    Raises:
        StartHeldBackError: The stack would not start; nothing was started.
    """
    outcome = judge(deployer, note=True)
    if not outcome.allowed:
        raise outcome.error(StartHeldBackError)


def check_before(
    unit: str, *, store: NoustStore | None = None, runner: CommandRunner | None = None
) -> None:
    """
    Refuse to start or restart a Compose stack that would not pass the gate, before systemd is asked.

    Args:
        unit: The unit's name.
        store: The store; the process-wide one by default.
        runner: What runs ``docker compose``; the process-wide runner by default.

    Raises:
        StartHeldBackError: The stack would not start; it keeps running as it was, and the error says why.
    """
    app = stack_named(unit, store=store)
    if app is None:
        return
    outcome = judge_app(app, runner=runner, store=store)
    if not outcome.allowed:
        raise outcome.error(StartHeldBackError)


def check_environment(
    app: App,
    write: Callable[[Path], None],
    *,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
) -> None:
    """
    Refuse an environment that would make a stack ask for what it was not accepted with.

    The stack is resolved with the file ``write`` makes (``docker compose --env-file``), which the
    caller writes the way it will write the real one: what is judged is what will be there. The file
    is private, in memory where the system has such a place, and removed on every way out.

    Args:
        app: The stack's row.
        write: Writes the ``.env`` the application is about to have to the path it is given.
        runner: What runs ``docker compose``; the process-wide runner by default.
        fs: The filesystem seam the file is made through.

    Raises:
        ValidationError: What Compose resolves with these variables adds something the stack was not
            accepted with; nothing was written. A stack that cannot be resolved at all is not refused
            here: its start is, and says why.
    """
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        return
    if compose_acceptance.get(app.domain) is None:
        return
    seam = fs or get_fs()
    deployer = stack_deployer(app, runner=runner)
    base = sealing.plaintext_copy_dir(secrets_dir()) / "composeenv"
    sealing.ensure_private_dir(base, seam)
    work = base / secrets.token_hex(8)
    try:
        seam.make_dir(work, mode=0o700, parents=True)
        candidate = work / "env"
        write(candidate)
        deployer._env_file = candidate
        outcome = judge(deployer)
    finally:
        seam.remove_tree(work)
    if outcome.problem or outcome.allowed:
        return
    raise ValidationError.from_text(
        say(
            "compose_gate.env_refused",
            domain=app.domain,
            lines="\n".join(outcome.lines),
            command=f"noust app sandbox compose-exception {app.domain} --reason '...'",
        )
    )
