# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Edit the Compose of a stack with a net: judge it, see what changes, apply it behind the health gate, go back.

:mod:`noust.deployers.compose_document` already reads, checks and replaces the document of a stack deployed
from one. The Docker zone adds what makes an edit something to do on a running server.

- **A document is judged by the model Compose resolves it to**, not by its text. The text goes to
  ``docker compose config`` on its standard input, the model that comes back is what the guard of
  :mod:`~noust.deployers.helpers.compose_guard` reads, so an ``include``, an ``extends`` or a ``volumes_from``
  cannot hide a privileged service. When Compose does not read it, Compose's own words are what the person
  sees, with the line.
- **What changes is said before it changes**, from Compose's own rehearsal (``up --dry-run``) of the document
  as it would be saved: which services are recreated, which are not touched, whether a volume would go.
- **Every saved text is a version** (:mod:`noust.core.docker_stack_versions`): who saved it, when and why, so
  that there is always a document to go back to, and a failed update puts the one before back.
- **Applying is the application's update**: its health gate, and the way back of the images it keeps. When
  the stack does not come up the document before is the one that runs again, and the error says whether it
  answers.

Two kinds of stack have a document to edit here: one deployed from a document, which Noust keeps
(``document``), and one brought to Noust where it already ran that is not a repository, whose file is in its
own folder (``file``). A stack from a repository has its document in git, a recipe's is the recipe's, a
container application's is generated from its settings, and one Noust did not make is brought to it first; each
says so, with the command, and nothing is changed.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from noust.core import audit
from noust.core.appdir.layout import code_path_for
from noust.core.compose_yaml import load_compose
from noust.core.docker.compose_documents import rehearse_document, resolve_document
from noust.core.docker.errors import DockerBlockedError, DockerNotFoundError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import ComposeProject, OwnerKind
from noust.core.docker.versions import parse_reference, with_default_tag
from noust.core.docker_stack_versions import StackVersion, StackVersionStore
from noust.core.exceptions import NoustError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, AppType, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers import compose_document
from noust.deployers.helpers import compose_variables, sandbox
from noust.deployers.helpers.app_env import read_app_env
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    inspect_compose,
    merge_findings,
)
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.site_history import unified_diff
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.installed import get_installed

__all__ = [
    "CheckLine",
    "ComposeView",
    "EditCheck",
    "EditResult",
    "Effect",
    "Effects",
    "ImageLine",
    "Problem",
    "ServiceLine",
    "StackEditor",
    "Staged",
    "VersionView",
]

#: What a line of Compose's rehearsal says: ``Container web-1  Recreated  0.0s``.
_EFFECT = re.compile(r"(Container|Volume|Network)\s+(\S+)\s+([A-Za-z]+)")
_DRY_RUN_PREFIX = re.compile(r"^.*?DRY-RUN MODE -\s*")
#: A line number in what Compose or YAML says: ``yaml: line 10:``, ``line 10, column 3``.
_LINE = re.compile(r"line\s+(\d+)")

#: Compose's words for what a rehearsal would do, as one action each. What only says it is running or
#: waiting is not an effect.
_ACTIONS = {
    "recreate": "recreate",
    "recreated": "recreate",
    "recreating": "recreate",
    "create": "create",
    "created": "create",
    "creating": "create",
    "remove": "remove",
    "removed": "remove",
    "removing": "remove",
    "start": "start",
    "started": "start",
    "starting": "start",
    "stop": "stop",
    "stopped": "stop",
    "stopping": "stop",
}


@dataclass(frozen=True)
class _Target:
    """
    Where a stack's document is and how it is changed.

    Attributes:
        project: The Compose project, as Docker lists it.
        app: The application that owns the stack.
        kind: ``document`` (Noust keeps it) or ``file`` (it is a file in the stack's own folder).
        path: Where the document is written.
        cwd: Where Compose runs: the running code of the application, which is where a relative path of the
            document means what it says.
        relative: The file's name relative to the application's folder (``file``), or the document's name.
    """

    project: str
    app: App
    kind: str
    path: Path
    cwd: Path
    relative: str


@dataclass(frozen=True)
class ComposeView:
    """
    A stack's document, or why it is not edited here.

    Attributes:
        stack: The Compose project.
        domain: The application it belongs to; None for a stack nobody of Noust made.
        editable: Whether it is edited here.
        kind: ``document`` or ``file`` when it is.
        why_code: When it is not: ``repository``, ``recipe``, ``generated``, ``bring_first``, ``no_file``
            or ``not_compose``.
        why: The same in a sentence that says where it is changed.
        text: The document, when it is edited here.
        path: Where it is.
        version: The number of its newest version in the history; None when none was kept.
    """

    stack: str
    domain: str | None
    editable: bool
    kind: str | None = None
    why_code: str = ""
    why: str = ""
    text: str | None = None
    path: str | None = None
    version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "stack": self.stack,
            "domain": self.domain,
            "editable": self.editable,
            "kind": self.kind,
            "why_code": self.why_code,
            "why": self.why,
            "text": self.text,
            "path": self.path,
            "version": self.version,
        }


@dataclass(frozen=True)
class Problem:
    """
    Something that stops a document from being applied.

    Attributes:
        code: ``compose`` (Compose does not read it), ``privileges`` (it asks for root on this server),
            ``variables`` (it requires a value the stack lacks) or ``image_missing``.
        message: What is wrong, in a sentence.
        output: Compose's own words, verbatim, when the problem is its.
        line: The line of the document it is about, when it says.
        service: The service it is about, when there is one.
    """

    code: str
    message: str
    output: str = ""
    line: int | None = None
    service: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "code": self.code,
            "message": self.message,
            "output": self.output,
            "line": self.line,
            "service": self.service,
        }


@dataclass(frozen=True)
class CheckLine:
    """
    One thing that was checked.

    Attributes:
        name: ``compose``, ``privileges``, ``images`` or ``variables``.
        ok: Whether it passed.
        detail: What was found, in a few words.
    """

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ServiceLine:
    """
    A service of the document.

    Attributes:
        name: Its name.
        image: Its image; None when it is built.
    """

    name: str
    image: str | None


@dataclass(frozen=True)
class ImageLine:
    """
    What is known of an image the change brings in.

    Attributes:
        service: The service that runs it.
        image: The image.
        state: ``present`` (this machine has it), ``found`` (its registry has it), ``missing`` (its
            registry does not), ``unknown`` (it could not be asked) or ``built`` (the service builds it).
        detail: What the registry said, when it could not tell.
    """

    service: str
    image: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class Effect:
    """
    One thing Compose's rehearsal says starting the stack from the document would do.

    Attributes:
        kind: ``container``, ``volume`` or ``network``.
        name: As Compose names it.
        service: The service, for a container.
        action: ``recreate``, ``create``, ``remove``, ``start`` or ``stop``.
    """

    kind: str
    name: str
    service: str
    action: str


@dataclass(frozen=True)
class Effects:
    """
    What the change would do, from Compose's own rehearsal.

    Attributes:
        changes: What would be recreated, created, removed, started or stopped; what only keeps running is
            not here.
        output: Compose's words, verbatim.
        removes_volumes: Whether a volume would go, which is data.
    """

    changes: list[Effect]
    output: str
    removes_volumes: bool


@dataclass
class EditCheck:
    """
    A document, judged and not written.

    Attributes:
        stack: The Compose project.
        domain: The application.
        kind: ``document`` or ``file``.
        changed: Whether it differs from what the stack has.
        valid: Whether nothing stops it from being applied.
        problems: What stops it.
        warnings: What is worth knowing and does not stop it.
        checks: What was checked, in order.
        services: The services of the document.
        images: What is known of the images the change brings in.
        diff: What changes, as a unified diff.
        added: Lines added.
        removed: Lines removed.
        effects: What starting the stack from it would do; None when it could not be rehearsed.
    """

    stack: str
    domain: str
    kind: str
    changed: bool = False
    valid: bool = True
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[CheckLine] = field(default_factory=list)
    services: list[ServiceLine] = field(default_factory=list)
    images: list[ImageLine] = field(default_factory=list)
    diff: str = ""
    added: int = 0
    removed: int = 0
    effects: Effects | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "stack": self.stack,
            "domain": self.domain,
            "kind": self.kind,
            "changed": self.changed,
            "valid": self.valid,
            "problems": [item.to_dict() for item in self.problems],
            "warnings": list(self.warnings),
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "services": [{"name": s.name, "image": s.image} for s in self.services],
            "images": [
                {"service": i.service, "image": i.image, "state": i.state, "detail": i.detail}
                for i in self.images
            ],
            "diff": self.diff,
            "added": self.added,
            "removed": self.removed,
            "effects": None
            if self.effects is None
            else {
                "changes": [
                    {"kind": e.kind, "name": e.name, "service": e.service, "action": e.action}
                    for e in self.effects.changes
                ],
                "output": self.effects.output,
                "removes_volumes": self.effects.removes_volumes,
            },
        }


@dataclass(frozen=True)
class Staged:
    """
    A document that passed and was kept as a version, ready to be applied.

    Attributes:
        domain: The application.
        version: Its number in the stack's history.
        check: What it was judged to be.
    """

    domain: str
    version: int
    check: EditCheck


@dataclass
class EditResult:
    """
    What applying a version did.

    Attributes:
        domain: The application.
        version: The version that was applied.
        applied: Whether the stack runs it now.
        deployment_id: The deployment the update wrote.
        warnings: What the update said worth knowing.
    """

    domain: str
    version: int
    applied: bool = False
    deployment_id: int | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "domain": self.domain,
            "version": self.version,
            "applied": self.applied,
            "deployment_id": self.deployment_id,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class VersionView:
    """
    One version of the document, with what restoring it would change.

    Attributes:
        version: The number.
        source: How it came to be.
        actor: Who saved it.
        reason: Why.
        created_at: When.
        text: The document.
        diff: What restoring it would change in what runs now, as a unified diff.
        diff_against: What it is compared with: ``current``.
    """

    version: int
    source: str
    actor: str | None
    reason: str
    created_at: str
    text: str
    diff: str
    diff_against: str = "current"


def _normal(text: str) -> str:
    """
    Args:
        text: A document as typed.

    Returns:
        It with the single trailing newline a file has.
    """
    return text if text.endswith("\n") else text + "\n"


def _first_line(text: str) -> str:
    """
    Args:
        text: What a tool said.

    Returns:
        Its first line with something in it.
    """
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


class StackEditor:
    """
    Edit the Compose of the stacks Docker lists.

    Args:
        gate: The gate, which finds the stack among the projects Docker's containers name, says whose it is
            and says what images this machine has.
        store: The store; the process-wide one when None.
        runner: Runs docker; the process-wide one when None.
        fs: Every file change goes through it; the process-wide one when None.
    """

    def __init__(
        self,
        gate: DockerGate,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        self._gate = gate
        self._store = store
        self._runner = runner
        self._fs = fs

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs or get_fs()

    @property
    def history(self) -> StackVersionStore:
        """The versions of stacks' documents."""
        return StackVersionStore(self.store)

    # -- whose document it is ------------------------------------------------------------------

    def _target(self, stack: str) -> _Target:
        """
        Find the document of a stack, or say whose it is.

        Args:
            stack: The Compose project, as the person typed it.

        Returns:
            Where the document is and how it is changed.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it.
            DockerBlockedError: It has no document to edit here; ``reason`` says whose it is.
        """
        project, _members = self._gate.project(stack)
        owner = project.owner
        if owner.kind is OwnerKind.NOBODY:
            raise DockerBlockedError(
                f"The stack {project.name} is not Noust's",
                "Noust does not know how it was created, so it does not edit its compose file blind. "
                "Bring it to Noust first, which changes nothing of what runs: "
                f"noust docker stack bring {project.name}.",
                reason="bring_first",
                via=f"noust docker stack bring {project.name}",
            )
        if owner.kind is not OwnerKind.APP:
            what = (
                f"the database instance {owner.subject}"
                if owner.kind is OwnerKind.DATABASE
                else "Noust"
            )
            raise DockerOwnedError(
                f"The stack {project.name} belongs to {what}",
                "Its compose file is Noust's own and is not edited here.",
                owner_kind=owner.kind.value,
                subject=owner.subject,
            )
        app = self.store.get_app(owner.subject)
        if app is None:
            raise DockerNotFoundError(
                f"No stack {stack!r} on this server",
                details="List the stacks to see their names.",
            )
        return self._kind_of(project, app)

    def _kind_of(self, project: ComposeProject, app: App) -> _Target:
        """
        Decide whether an application's compose file is a document to edit, and where.

        Args:
            project: The stack.
            app: Its application.

        Returns:
            Where the document is.

        Raises:
            DockerBlockedError: It is not edited here.
        """
        if app.app_type == AppType.CONTAINER.value:
            raise DockerBlockedError(
                f"The compose file of {app.domain} is generated by Noust from its settings",
                "It is rewritten by every deployment. Change what it runs with: "
                f"noust app container set {app.domain}.",
                reason="generated",
                via=f"noust app container set {app.domain}",
            )
        if app.app_type != AppType.DOCKER_COMPOSE.value:
            raise DockerBlockedError(
                f"{app.domain} is not a Compose stack",
                "Only a Docker Compose stack has a compose document.",
                reason="not_compose",
            )
        cwd = code_path_for(app)
        why = compose_document.why_not_editable(app, self.store)
        if why is None:
            directory = recipe_source_dir(domain_to_app_name(app.domain), self.store)
            return _Target(
                project.name, app, "document", directory / COMPOSE_FILE, cwd, COMPOSE_FILE
            )
        if get_installed(app.domain, store=self.store) is not None:
            raise DockerBlockedError(
                f"{app.domain} was installed from a recipe: its document is the recipe's",
                why,
                reason="recipe",
                via=f"noust recipe update {app.domain}",
            )
        if app.source:
            raise DockerBlockedError(
                f"{app.domain} comes from a repository: its compose file is the repository's",
                why,
                reason="repository",
            )
        return self._file_of(project, app, cwd)

    def _file_of(self, project: ComposeProject, app: App, cwd: Path) -> _Target:
        """
        Find the compose file of a stack brought to Noust where it already ran.

        Args:
            project: The stack.
            app: Its application.
            cwd: Where the application's code is.

        Returns:
            The file, in the stack's own folder.

        Raises:
            DockerBlockedError: The stack has no file there, or it is a link that is not written through.
        """
        from noust.deployers.docker_compose import stack_deployer

        deployer = stack_deployer(app, runner=self.runner)
        try:
            deployer._discover_compose_file()
        except NoustError as exc:
            raise DockerBlockedError(
                f"The compose file of {app.domain} is not there: {exc.message}",
                "Put it back where the stack runs from, or bring the stack to Noust again.",
                reason="no_file",
            ) from exc
        path = deployer.compose_path
        if path is None or path.is_symlink() or not path.is_file():
            raise DockerBlockedError(
                f"The compose file of {app.domain} is not a plain file",
                "A link is never written through. Edit the file it points to where it is.",
                reason="no_file",
            )
        try:
            relative = str(path.resolve().relative_to(Path(app.app_path).resolve()))
        except ValueError as exc:
            raise DockerBlockedError(
                f"The compose file of {app.domain} is outside its folder",
                "Noust edits a file inside the folder the stack runs from.",
                reason="no_file",
            ) from exc
        return _Target(project.name, app, "file", path, cwd, relative)

    def _current(self, target: _Target) -> str:
        """
        Args:
            target: Where the document is.

        Returns:
            What it says now.

        Raises:
            DockerBlockedError: It cannot be read.
        """
        try:
            return target.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DockerBlockedError(
                f"The compose document of {target.app.domain} cannot be read: {exc}",
                "Check the file and its permissions.",
                reason="no_file",
            ) from exc

    def application_of(self, stack: str) -> str:
        """
        Say which application owns a stack whose document is edited here, without reading it.

        Args:
            stack: The Compose project.

        Returns:
            The application's domain.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it.
            DockerBlockedError: It has no document to edit here.
        """
        return self._target(stack).app.domain

    # -- reading ---------------------------------------------------------------------------

    def view(self, stack: str, *, actor: str = "system") -> ComposeView:
        """
        Read a stack's document, or say why it is not edited here.

        Reading is a reveal (a document can hold a value typed into it) and is recorded.

        Args:
            stack: The Compose project.
            actor: Who asked, for the audit trail.

        Returns:
            The document and where it is, or why there is none to edit.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it.
        """
        try:
            target = self._target(stack)
        except DockerBlockedError as blocked:
            project, _ = self._gate.project(stack)
            domain = project.owner.subject if project.owner.kind is OwnerKind.APP else None
            return ComposeView(
                stack=stack,
                domain=domain,
                editable=False,
                why_code=blocked.reason,
                why=f"{blocked.message}. {blocked.details}".strip(),
            )
        text = self._current(target)
        audit.record(
            "apps.compose.document",
            target=f"app:{target.app.domain}",
            details={"action": "read", "by": actor},
        )
        latest = self.history.latest(target.app.domain)
        return ComposeView(
            stack=stack,
            domain=target.app.domain,
            editable=True,
            kind=target.kind,
            text=text,
            path=str(target.path),
            version=latest.version if latest else None,
        )

    # -- judging ---------------------------------------------------------------------------

    def check(self, stack: str, text: str) -> EditCheck:
        """
        Judge a document for a stack without writing anything.

        Args:
            stack: The Compose project.
            text: The document.

        Returns:
            What it is, what stops it, what it changes and what that does.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it.
            DockerBlockedError: It has no document to edit here.
        """
        return self._judge(self._target(stack), text)

    def _judge(self, target: _Target, text: str) -> EditCheck:
        """
        Judge a document: does Compose read it, what does the model it resolves to ask of the host, are its
        images there, what does the stack lack, and what would starting it from it do.

        Args:
            target: Where the stack's document is.
            text: The candidate.

        Returns:
            The judgement.
        """
        current = self._current(target)
        found = EditCheck(target.project, target.app.domain, target.kind)
        found.changed = _normal(text) != _normal(current)
        found.diff = unified_diff(
            _normal(current), _normal(text), older_label="current", newer_label="new"
        )
        found.added = sum(
            1
            for line in found.diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        found.removed = sum(
            1
            for line in found.diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        model, output = self._resolve(target, text)
        if model is None:
            line = _line_of(output, text)
            found.problems.append(
                Problem(
                    "compose",
                    "Docker Compose does not read this document",
                    output=output,
                    line=line,
                )
            )
            found.checks.append(CheckLine("compose", False, _first_line(output)))
            found.valid = False
            return found
        found.checks.append(CheckLine("compose", True, "docker compose config"))
        document = load_compose(text)
        found.services = [
            ServiceLine(str(name), spec.get("image") if isinstance(spec, Mapping) else None)
            for name, spec in (model.get("services") or {}).items()
        ]
        self._privileges(target, document, model, current, found)
        self._images(target, model, current, found)
        self._variables(target, document, found)
        found.valid = not found.problems
        if found.valid and found.changed:
            found.effects = self._rehearse(target, text, found)
        return found

    def _resolve(self, target: _Target, text: str) -> tuple[dict[str, Any] | None, str]:
        """
        Ask Compose for the document as it resolves it.

        The document goes on the standard input and is written nowhere. The model holds the stack's
        secrets: it is parsed and dropped, never logged, and nothing of it is in what this returns when
        Compose fails; what is returned then is Compose's own words.

        Args:
            target: Where the stack's document is.
            text: The candidate.

        Returns:
            The model, and an empty string; or None and what Compose said.
        """
        return resolve_document(self.runner, target.project, text, target.cwd)

    def _privileges(
        self,
        target: _Target,
        document: Any,
        model: dict[str, Any],
        current: str,
        found: EditCheck,
    ) -> None:
        """
        Apply the guard to the document and to the model Compose resolves it to.

        What the stack already runs with is not new, and an exception recorded for the stack lets it
        through with the reason said, as the deployer treats them: the deployer judges it again when the
        update runs, and this is what lets a person know before.

        Args:
            target: Where the stack's document is.
            document: The candidate, parsed.
            model: What Compose resolves it to.
            current: What the stack has now.
            found: The judgement, to which this adds.
        """
        where = target.cwd
        findings: ComposeFindings = merge_findings(
            inspect_compose(document, where), inspect_compose(model, where)
        )
        refused = list(findings.refused)
        found.warnings.extend(findings.warned)
        if not refused:
            found.checks.append(CheckLine("privileges", True, ""))
            return
        exception = sandbox.get_compose_exception(target.app.domain, store=self.store)
        already: set[str] = set()
        if exception is None:
            already = set(self._refused_in(target, current))
        added = [sentence for sentence in refused if sentence not in already]
        for sentence in refused:
            if exception is not None:
                found.warnings.append(
                    f"{sentence}; allowed for {target.app.domain} by {exception.allowed_by} "
                    f"({exception.reason})"
                )
            elif sentence in already:
                found.warnings.append(f"{sentence}: the stack runs with this already")
        if exception is not None or not added:
            found.checks.append(CheckLine("privileges", True, "allowed"))
            return
        for sentence in added:
            key = findings.keys.get(sentence)
            service = (
                key[0].removeprefix("service ") if key and key[0].startswith("service ") else None
            )
            found.problems.append(Problem("privileges", sentence, service=service))
        found.checks.append(CheckLine("privileges", False, f"{len(added)} found"))

    def _refused_in(self, target: _Target, text: str) -> list[str]:
        """
        Args:
            target: Where the stack's document is.
            text: A document the stack has run from.

        Returns:
            What the guard refuses in it and in what Compose resolves it to; empty when it cannot be read.
        """
        try:
            document = load_compose(text)
        except yaml.YAMLError:
            return []
        model, _output = self._resolve(target, text)
        findings = inspect_compose(document, target.cwd)
        if model is not None:
            findings = merge_findings(findings, inspect_compose(model, target.cwd))
        return list(findings.refused)

    def _images(
        self, target: _Target, model: dict[str, Any], current: str, found: EditCheck
    ) -> None:
        """
        Say what is known of each image the change brings in: the ones that are new or changed, and no
        other. An image this machine has needs no registry, and a service that builds its image has none to
        look for.

        Args:
            target: Where the stack's document is.
            model: What Compose resolves the candidate to.
            current: What the stack has now.
            found: The judgement, to which this adds.
        """
        try:
            before = load_compose(current)
        except yaml.YAMLError:
            before = {}
        old = {
            str(name): spec.get("image")
            for name, spec in ((before or {}).get("services") or {}).items()
            if isinstance(spec, Mapping)
        }
        local = {tag for image in self._gate.images().items for tag in image.tags}
        versions = ImageVersions(self._gate, store=self.store, runner=self.runner)
        missing = False
        for name, spec in (model.get("services") or {}).items():
            if not isinstance(spec, Mapping) or not spec.get("image"):
                continue
            image = str(spec["image"])
            if old.get(str(name)) == image:
                continue
            if spec.get("build"):
                found.images.append(ImageLine(str(name), image, "built"))
                continue
            parsed = parse_reference(image)
            if parsed is None or with_default_tag(image) in local:
                found.images.append(ImageLine(str(name), image, "present"))
                continue
            looked = versions.look(parsed)
            if looked.ok:
                found.images.append(ImageLine(str(name), image, "found"))
            elif looked.why == "not_found":
                missing = True
                found.images.append(ImageLine(str(name), image, "missing", looked.detail))
                found.problems.append(
                    Problem(
                        "image_missing",
                        f"The registry has no {image}, which service {name} would run",
                        output=looked.detail,
                        service=str(name),
                    )
                )
            else:
                found.images.append(ImageLine(str(name), image, "unknown", looked.detail))
                found.warnings.append(
                    f"{image} could not be looked for in its registry, so it is not known "
                    f"whether it exists: {looked.detail}"
                )
        found.checks.append(CheckLine("images", not missing, ""))

    def _variables(self, target: _Target, document: Any, found: EditCheck) -> None:
        """
        Judge the document's variables against the stack's own values, the way an update will.

        Args:
            target: Where the stack's document is.
            document: The candidate, parsed.
            found: The judgement, to which this adds.
        """
        report = compose_variables.judge(
            document, read_app_env(target.app), file=target.relative or COMPOSE_FILE
        )
        names = [variable.name for variable in report.missing]
        if names:
            found.problems.append(
                Problem(
                    "variables",
                    f"The document requires {', '.join(names)}, and {target.app.domain} has no value "
                    f"for {'it' if len(names) == 1 else 'them'}: noust env set {target.app.domain} "
                    "NAME=VALUE",
                )
            )
        found.checks.append(CheckLine("variables", not names, ", ".join(names)))

    def _rehearse(self, target: _Target, text: str, found: EditCheck) -> Effects | None:
        """
        Ask Compose what starting the stack from the candidate would do, and change nothing.

        Args:
            target: Where the stack's document is.
            text: The candidate.
            found: The judgement; a rehearsal that fails is a warning in Compose's words.

        Returns:
            What would be recreated, created, removed, started or stopped; None when Compose could not
            rehearse it.
        """
        succeeded, output = rehearse_document(self.runner, target.project, text, target.cwd)
        if not succeeded:
            found.warnings.append(
                f"Compose could not rehearse starting the stack from this document: {_first_line(output)}"
            )
            return None
        services = self._service_names(target)
        changes: list[Effect] = []
        for raw in output.splitlines():
            line = _DRY_RUN_PREFIX.sub("", raw).strip()
            match = _EFFECT.match(line)
            if match is None:
                continue
            kind, name, word = match.group(1).lower(), match.group(2), match.group(3).lower()
            action = _ACTIONS.get(word)
            if action is None:
                continue
            service = services.get(name, "") if kind == "container" else ""
            changes.append(Effect(kind, name, service, action))
        return Effects(
            changes=changes,
            output=output,
            removes_volumes=any(e.kind == "volume" and e.action == "remove" for e in changes),
        )

    def _service_names(self, target: _Target) -> dict[str, str]:
        """
        Args:
            target: Where the stack's document is.

        Returns:
            The service each of the stack's containers is, by the container's name.
        """
        return {
            c.name: c.service
            for c in self._gate.containers(project=target.project).items
            if c.service
        }

    # -- keeping a version -----------------------------------------------------------------

    def stage(
        self, stack: str, text: str, *, reason: str, actor: str, source: str = "edit"
    ) -> Staged:
        """
        Judge a document and, if it passes, keep it as a version, ready to be applied.

        What the stack has is kept first when the history does not have it, so there is always a document
        to go back to. Nothing is written where the stack runs from.

        Args:
            stack: The Compose project.
            text: The document.
            reason: Why, in a line.
            actor: Who saved it.
            source: How it came to be: ``edit`` or ``restore``.

        Returns:
            The version, and what the document was judged to be.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it.
            DockerBlockedError: It has no document to edit here (``reason`` says whose it is), it does not
                pass (``invalid``) or it is what the stack has (``unchanged``).
        """
        target = self._target(stack)
        found = self._judge(target, text)
        if not found.changed:
            raise DockerBlockedError(
                f"This is the document {target.app.domain} already runs from",
                "Change something first.",
                reason="unchanged",
            )
        if not found.valid:
            raise DockerBlockedError(
                f"The document cannot be applied to {target.app.domain}",
                "\n".join(
                    f"- {problem.message}" + (f"\n{problem.output}" if problem.output else "")
                    for problem in found.problems
                ),
                reason="invalid",
            )
        domain = target.app.domain
        self.history.add(
            domain,
            _normal(self._current(target)),
            source="found",
            actor=None,
            reason="The document as Noust found it",
        )
        kept = self.history.add(domain, _normal(text), source=source, actor=actor, reason=reason)
        if kept is None:  # the newest version already says it: the same text staged twice
            kept = self.history.latest(domain)
        if kept is None:  # pragma: no cover - the add above made one
            raise DockerBlockedError(
                "The version could not be kept", "Try again.", reason="invalid"
            )
        audit.record(
            "docker.compose",
            target=f"app:{domain}",
            details={
                "action": "stage",
                "by": actor,
                "source": source,
                "version": kept.version,
                "reason": kept.reason,
                "services": [service.name for service in found.services],
            },
        )
        return Staged(domain, kept.version, found)

    def stage_restore(self, stack: str, version: int, *, reason: str, actor: str) -> Staged:
        """
        Keep an earlier version as the next one, ready to be applied.

        Args:
            stack: The Compose project.
            version: The number to go back to.
            reason: Why, in a line.
            actor: Who asked.

        Returns:
            The new version.

        Raises:
            DockerNotFoundError: There is no such stack or version.
            DockerBlockedError: It is judged like any edit: what no longer passes is refused.
        """
        target = self._target(stack)
        old = self.history.get(target.app.domain, version)
        if old is None:
            raise DockerNotFoundError(
                f"No version {version} of the document of {stack}",
                details="List its versions to see which are kept.",
            )
        return self.stage(stack, old.text, reason=reason, actor=actor, source="restore")

    # -- the history -----------------------------------------------------------------------

    def versions(self, stack: str) -> list[StackVersion]:
        """
        Args:
            stack: The Compose project.

        Returns:
            Its versions, newest first, without their text.

        Raises:
            DockerNotFoundError: There is no such stack, or the principal may not see it.
            DockerBlockedError: It has no document to edit here.
        """
        return self.history.versions(self._target(stack).app.domain)

    def version(self, stack: str, number: int) -> VersionView:
        """
        Read one version, with what restoring it would change in what runs now.

        Args:
            stack: The Compose project.
            number: The version.

        Returns:
            The version, its text and the difference.

        Raises:
            DockerNotFoundError: There is no such stack or version.
            DockerBlockedError: It has no document to edit here.
        """
        target = self._target(stack)
        old = self.history.get(target.app.domain, number)
        if old is None:
            raise DockerNotFoundError(
                f"No version {number} of the document of {stack}",
                details="List its versions to see which are kept.",
            )
        audit.record(
            "apps.compose.document",
            target=f"app:{target.app.domain}",
            details={"action": "read_version", "version": number},
        )
        return VersionView(
            version=old.version,
            source=old.source,
            actor=old.actor,
            reason=old.reason,
            created_at=old.created_at,
            text=old.text,
            diff=unified_diff(
                _normal(self._current(target)),
                old.text,
                older_label="current",
                newer_label=f"version {number}",
            ),
        )

    # -- applying --------------------------------------------------------------------------

    def apply(
        self,
        stack: str,
        version: int,
        *,
        actor: str,
        trigger: str = "cli",
        job_id: str | None = None,
        on_phase: Callable[[int, int, str], None] | None = None,
        on_step: Callable[[str], None] | None = None,
        logger: Logger | None = None,
        verbose: bool = False,
    ) -> EditResult:
        """
        Make a version the document the stack runs from, and run the application's update behind its
        health gate.

        It is judged again: what was checked when it was kept is of its moment. When the stack does not
        come up the document before is back where the next update reads it and the stack runs it again
        (the error says whether it answers), and the history says so.

        Args:
            stack: The Compose project.
            version: The version to apply.
            actor: Who asked.
            trigger: Who asked, for the deployment history.
            job_id: The background job driving this, when there is one.
            on_phase: Told as each phase of the update begins.
            on_step: Told as each step of the rebuild begins.
            logger: Where progress goes.
            verbose: Verbosity of the update.

        Returns:
            What was done.

        Raises:
            DockerNotFoundError: There is no such stack or version.
            DockerBlockedError: It no longer passes (``invalid``), or is what runs (``unchanged``).
            NoustError: The update failed; the document before was put back.
        """
        from noust.deployers.lifecycle import health_gate_for, update_app
        from noust.recipes.updates import put_stack_back

        target = self._target(stack)
        domain = target.app.domain
        wanted = self.history.get(domain, version)
        if wanted is None:
            raise DockerNotFoundError(
                f"No version {version} of the document of {stack}",
                details="List its versions to see which are kept.",
            )
        found = self._judge(target, wanted.text)
        if not found.changed:
            raise DockerBlockedError(
                f"{domain} already runs from version {version}", "", reason="unchanged"
            )
        if not found.valid:
            raise DockerBlockedError(
                f"Version {version} no longer passes",
                "\n".join(f"- {problem.message}" for problem in found.problems),
                reason="invalid",
            )
        log = logger or Logger(verbose=verbose)
        previous = self._current(target)
        self._write(target, wanted.text)
        audit.record(
            "docker.compose",
            target=f"app:{domain}",
            details={"action": "apply", "by": actor, "version": version, "kind": target.kind},
        )
        try:
            outcome = update_app(
                domain,
                trigger=trigger,
                job_id=job_id,
                on_phase=on_phase,
                on_step=on_step,
                logger=log,
                verbose=verbose,
            )
        except NoustError as failure:
            if target.kind == "file":
                # What was written is not what a stack that never got as far as its update may keep.
                self._write(target, previous)
            put_stack_back(
                domain,
                domain_to_app_name(domain),
                {target.relative: previous},
                failure,
                store=self.store,
                log=log,
                gate=health_gate_for,
                write_source=target.kind == "document",
            )
            self.history.add(
                domain,
                _normal(previous),
                source="revert",
                actor=None,
                reason=f"The update with version {version} failed: {failure.message}",
            )
            audit.record(
                "docker.compose",
                target=f"app:{domain}",
                outcome="failure",
                details={
                    "action": "revert",
                    "by": actor,
                    "version": version,
                    "reason": failure.message,
                },
            )
            raise
        return EditResult(
            domain=domain,
            version=version,
            applied=True,
            deployment_id=outcome.deployment_id,
            warnings=(str(outcome.warnings),) if outcome.warnings else (),
        )

    def _write(self, target: _Target, text: str) -> None:
        """
        Write a document where the stack reads it, with the mode and the owner the file had.

        Args:
            target: Where the stack's document is.
            text: The document.
        """
        mode, owner = 0o644, None
        if target.path.exists():
            info = os.stat(target.path)
            mode, owner = stat.S_IMODE(info.st_mode), (info.st_uid, info.st_gid)
        self.fs.write_text(target.path, _normal(text), mode=mode, owner=owner)


def _line_of(output: str, text: str) -> int | None:
    """
    Args:
        output: What Compose or YAML said.
        text: The document it was said of.

    Returns:
        The line of the document it names, when it names one the document has.
    """
    match = _LINE.search(output)
    if match is None:
        return None
    line = int(match.group(1))
    return line if 1 <= line <= max(len(text.splitlines()), 1) else None
