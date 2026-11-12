# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bring a stack that already runs to Noust, from where the Docker zone shows it.

The work is :mod:`noust.deployers.compose_adopt` (``noust app adopt``), which registers a stack as it runs
and touches nothing of what runs: no container is recreated, restarted or changed, and its compose file is
not rewritten. What the zone adds is where the stack comes from. Docker lists it, so its directory and its
compose file are what its containers say (``com.docker.compose.project.working_dir`` and ``config_files``),
and the only thing a person types is the name the application will have in Noust's list (rule 4: what was
typed never reaches a command or a path).

What is said before anything is registered is what makes it safe to say yes: that the folder is on this
server, what the rehearsed ``up`` would change (nothing, or Compose's own words if it would), how many
containers, jobs, volumes and networks the stack has, and what would not come back after a restart. What
cannot be adopted is said first and in plain words: a folder that is not on this server (Portainer keeps
its stacks inside its volume), a stack started from several files (adopting takes one), a stack that is
already Noust's, and a name an application already has.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.docker.errors import DockerBlockedError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import ComposeProject, OwnerKind
from noust.core.exceptions import DomainError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import DeploymentTrigger, NoustStore, get_store
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionPlan
from noust.validators.domain import validate_domain

__all__ = ["Bringing", "RestartWarning", "StackBringer", "proposed_name"]

#: What a stack's name is read as when it is proposed as an application's: Compose's own alphabet, with the
#: underscore (which a domain does not have) turned into a dash.
_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True)
class RestartWarning:
    """
    A container of the stack that a restart of the machine would leave down.

    Attributes:
        container: Its name.
        policy: Its restart policy.
        why: ``policy`` (it runs and its policy does not bring it back) or ``stopped``.
    """

    container: str
    policy: str
    why: str


@dataclass
class Bringing:
    """
    What bringing a stack to Noust would register, or did.

    Attributes:
        stack: The Compose project.
        name: What the application will be called in Noust's list.
        folder: The directory the stack runs from, where it stays.
        compose_file: Its compose file, relative to the folder.
        counts: How many ``containers`` it has, how many of them are ``jobs`` that already ended, and how
            many ``volumes`` and ``networks`` it has.
        recreates: What starting the stack the way Noust would changes, in Compose's own words; empty when
            it changes nothing, which is what an adoption proves.
        warnings: What adopting found worth saying, one sentence each.
        restart_loss: What a restart of the machine would not bring back.
        plan: What :func:`noust.deployers.compose_adopt.plan_adoption` found.
        adopted: Whether it was registered; false for a preview.
        deployment_id: The adoption's history row, when one was recorded.
    """

    stack: str
    name: str
    folder: str
    compose_file: str
    counts: dict[str, int]
    recreates: list[str]
    warnings: list[str]
    restart_loss: list[RestartWarning]
    plan: AdoptionPlan
    adopted: bool = False
    deployment_id: int | None = None
    stays: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            What the preview or the adoption says, JSON-ready.
        """
        return {
            "stack": self.stack,
            "name": self.name,
            "folder": self.folder,
            "compose_file": self.compose_file,
            "project": self.plan.project,
            "containers": list(self.plan.containers),
            "running": self.plan.running,
            "headless": self.plan.headless,
            "port": self.plan.port,
            "site": self.plan.site,
            "source": self.plan.source,
            "unit": self.plan.app_name,
            "counts": dict(self.counts),
            "rehearsal": self.plan.dry_run,
            "recreates": list(self.recreates),
            "warnings": list(self.warnings),
            "restart_loss": [
                {"container": item.container, "policy": item.policy, "why": item.why}
                for item in self.restart_loss
            ],
            "adopted": self.adopted,
            "deployment_id": self.deployment_id,
        }


def proposed_name(project: str) -> str | None:
    """
    Propose what a stack is called in the list of applications.

    Args:
        project: The Compose project's name.

    Returns:
        The project's name as a domain accepts it (underscores become dashes); None when it cannot be
        one, and the person has to say.
    """
    lowered = project.lower()
    if not _PROJECT.fullmatch(lowered):
        return None
    candidate = lowered.replace("_", "-").strip("-")
    if not candidate or len(candidate) > 63:
        return None
    try:
        return validate_domain(candidate)
    except DomainError:
        return None


class StackBringer:
    """
    Bring the stacks Docker lists to Noust.

    Args:
        gate: The gate, which finds the stack among the projects Docker's containers name and says whose
            it is.
        store: The store; the process-wide one when None.
        runner: Runs docker and git; the process-wide one when None.
    """

    def __init__(
        self,
        gate: DockerGate,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._gate = gate
        self._store = store
        self._runner = runner

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    def preview(
        self, stack: str, *, name: str | None = None, accept_recreate: bool = False
    ) -> Bringing:
        """
        Say what bringing a stack to Noust would register, without registering anything.

        Args:
            stack: The Compose project, as the person typed it.
            name: What to call the application; the project's name when None.
            accept_recreate: Go on although the rehearsed ``up`` would change something.

        Returns:
            What would be registered and what is not changed.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: An engine or Noust owns it.
            DockerBlockedError: It cannot be brought: already Noust's, no folder on this server, several
                files, a name that is taken or that has to be said.
            AdoptionRefusedError: Compose said starting it as Noust would changes what runs.
            DeploymentError: Adopting is refused for another reason; the error says which.
        """
        project, path, relative, domain = self._prepare(stack, name)
        plan = compose_adopt.plan_adoption(
            domain,
            path,
            compose_file=relative,
            accept_recreate=accept_recreate,
            runner=self.runner,
            store=self.store,
        )
        return self._described(project, plan)

    def bring(
        self,
        stack: str,
        *,
        name: str | None = None,
        accept_recreate: bool = False,
        trigger: str = DeploymentTrigger.PANEL.value,
        job_id: str | None = None,
    ) -> Bringing:
        """
        Register a stack as it runs: nothing is recreated, restarted or changed.

        Args:
            stack: The Compose project, as the person typed it.
            name: What to call the application; the project's name when None.
            accept_recreate: Go on although the rehearsed ``up`` would change something.
            trigger: Who asked, for the history row.
            job_id: The background job, when there is one.

        Returns:
            What was registered.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: An engine or Noust owns it.
            DockerBlockedError: It cannot be brought.
            AdoptionRefusedError: Compose said starting it as Noust would changes what runs.
            DeploymentError: Adopting is refused for another reason.
        """
        project, path, relative, domain = self._prepare(stack, name)
        plan = compose_adopt.plan_adoption(
            domain,
            path,
            compose_file=relative,
            accept_recreate=accept_recreate,
            runner=self.runner,
            store=self.store,
        )
        done = compose_adopt.adopt(
            plan, trigger=trigger, job_id=job_id, runner=self.runner, store=self.store
        )
        described = self._described(project, plan)
        described.adopted = done.adopted
        described.deployment_id = done.deployment_id
        return described

    # -- what is found ---------------------------------------------------------------------

    def _prepare(
        self, stack: str, name: str | None
    ) -> tuple[ComposeProject, Path, str | None, str]:
        """
        Find what adopting needs among what Docker listed, and refuse what cannot be adopted.

        Args:
            stack: The Compose project, as the person typed it.
            name: What to call the application.

        Returns:
            The project, the directory it runs from, its compose file relative to it (None when Compose
            recorded none) and the name of the application.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: An engine or Noust owns it.
            DockerBlockedError: It cannot be brought.
            DomainError: The name is not one.
        """
        project, _members = self._gate.project(stack)
        owner = project.owner
        if owner.kind is OwnerKind.APP:
            raise DockerBlockedError(
                f"The stack {project.name} is already Noust's: it is the application {owner.subject}",
                f"Look for it in the applications: noust status {owner.subject}.",
                reason="already_noust",
            )
        if owner.kind is not OwnerKind.NOBODY:
            what = (
                f"the database instance {owner.subject}"
                if owner.kind is OwnerKind.DATABASE
                else "Noust"
            )
            raise DockerOwnedError(
                f"The stack {project.name} belongs to {what}",
                "It is already managed there and is not brought as an application.",
                owner_kind=owner.kind.value,
                subject=owner.subject,
            )
        folder = project.working_dir
        if not folder or not os.path.isabs(folder) or not Path(folder).is_dir():
            raise DockerBlockedError(
                f"The folder of {project.name} is not on this server: {folder or 'Docker did not say it'}",
                "Noust registers a stack where it already runs, and this one runs from a folder this "
                "server does not have (a tool that keeps its stacks in its own volume, or Noust in a "
                "container that does not see the host's folders). Bring it up from a folder on this "
                "server, then bring it here.",
                reason="no_folder",
            )
        path = Path(folder)
        relative = self._compose_file(project, path)
        domain = validate_domain(name) if name else proposed_name(project.name)
        if domain is None:
            raise DockerBlockedError(
                f"{project.name} cannot be called by that name in the list of applications",
                "Choose the name it will have: letters, digits and dashes.",
                reason="name_needed",
            )
        if self.store.get_app(domain) is not None:
            raise DockerBlockedError(
                f"An application is already called {domain}",
                "Choose another name for this stack.",
                reason="name_taken",
            )
        return project, path, relative, domain

    @staticmethod
    def _compose_file(project: ComposeProject, path: Path) -> str | None:
        """
        Args:
            project: The stack.
            path: The directory it runs from.

        Returns:
            Its compose file relative to the directory, None when Compose recorded none.

        Raises:
            DockerBlockedError: It was started from several files, or from one outside its folder.
        """
        files = [item for item in project.config_files if item]
        if not files:
            return None
        if len(files) > 1:
            raise DockerBlockedError(
                f"{project.name} was started from several compose files: {', '.join(files)}",
                "Noust adopts one compose file. Put what the others add into one file, bring the "
                "stack up from it, then bring it here.",
                reason="several_files",
            )
        found = Path(files[0]) if os.path.isabs(files[0]) else path / files[0]
        resolved, base = Path(os.path.realpath(found)), Path(os.path.realpath(path))
        if not resolved.is_relative_to(base):
            raise DockerBlockedError(
                f"The compose file of {project.name} is outside its folder: {files[0]}",
                "Noust registers a stack with its file inside the folder it runs from. Move the file "
                "into the folder, bring the stack up from it, then bring it here.",
                reason="file_outside",
            )
        return str(resolved.relative_to(base))

    def _described(self, project: ComposeProject, plan: AdoptionPlan) -> Bringing:
        """
        Put together what a plan says with what Docker knows of the stack.

        Args:
            project: The stack.
            plan: What adoption found.

        Returns:
            What bringing it registers.
        """
        volumes = [v for v in self._gate.volumes().items if v.project == project.name]
        networks = [n for n in self._gate.networks().items if n.project == project.name]
        lost = [item for item in self._gate.restart_loss() if item.project == project.name]
        return Bringing(
            stack=project.name,
            name=plan.domain,
            folder=str(plan.app_path),
            compose_file=plan.compose_file,
            counts={
                "containers": len(project.containers),
                "jobs": max(len(project.containers) - project.workers, 0),
                "volumes": len(volumes),
                "networks": len(networks),
            },
            recreates=list(plan.changes),
            warnings=list(plan.warnings),
            restart_loss=[RestartWarning(item.name, item.policy, item.why) for item in lost],
            plan=plan,
        )
