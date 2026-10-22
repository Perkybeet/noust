# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Compose document of a stack Noust keeps: read it, check a change, make it, apply it.

A stack deployed from a document (pasted into the console, typed into ``noust create``'s
API, or converted from a Coolify template) has no repository to edit: the document lives in a
directory beside the store (:func:`~noust.recipes.deploy.recipe_source_dir`) and is the stack's
source, fetched again by every update. Until now it could only be written when the stack was
created, so changing an image's tag, a port or a variable meant deleting the stack or leaving
Noust. Here it is edited like the file in a repository would be, with the same guard in front:

- :func:`check_document` is what the editor shows before saving: the services, the variables the
  document reads and the ones the stack has no value for, and what refuses it
  (:func:`~noust.deployers.compose_paste.analyze_document` is the same analysis a creation runs).
- :func:`replace_document` writes it. Nothing is rebuilt by writing: the next update fetches the
  document again, the console's pending changes say the stack is not running it yet
  (:mod:`noust.deployers.pending_changes`), and with ``apply`` the update runs now, behind the
  health gate, with the previous document put back where it came from when that fails
  (:func:`~noust.recipes.updates.put_stack_back`, the one implementation of "a stack that did not
  come up").

A stack that comes from a repository, was adopted where it already runs, or was installed from a
recipe has no such document: the first is edited in git, the second where it lives, and the
third is the recipe's, updated with ``noust recipe update``. Each is refused with that.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from noust.core import audit
from noust.core.compose_yaml import load_compose
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.compose_paste import PastedCompose, analyze_document
from noust.deployers.helpers import compose_variables
from noust.deployers.helpers.app_env import read_app_env
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.installed import get_installed
from noust.validators.domain import validate_domain


@dataclass(frozen=True)
class StackDocument:
    """
    A stack's Compose document.

    Attributes:
        domain: The stack.
        text: The document.
        path: Where Noust keeps it.
    """

    domain: str
    text: str
    path: str


@dataclass
class DocumentCheck:
    """
    What a document would do to a stack, judged and not written.

    Attributes:
        analysis: The services, variables, and what refuses or warns about it.
        missing: The variables it requires (``${NAME:?message}``) that the stack has no value for.
        changed: Whether it differs from the document the stack has.
    """

    analysis: PastedCompose
    missing: list[str] = field(default_factory=list)
    changed: bool = True

    @property
    def deployable(self) -> bool:
        """Whether nothing refuses it and nothing it requires is missing."""
        return self.analysis.deployable and not self.missing


@dataclass
class DocumentChange:
    """
    What :func:`replace_document` did.

    Attributes:
        domain: The stack.
        changed: Whether the document was different from the one the stack had.
        applied: Whether the stack was updated to run it.
        services: The services of the document now kept.
        warnings: What is worth knowing about it, and what the update says.
        deployment_id: The deployment the update wrote, when it ran.
    """

    domain: str
    changed: bool
    applied: bool = False
    services: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    deployment_id: int | None = None


def why_not_editable(app: App, store: NoustStore) -> str | None:
    """
    Say why a stack has no document Noust keeps.

    Args:
        app: The application.
        store: The store.

    Returns:
        The reason, or None when it has one.
    """
    if app.app_type != "docker-compose":
        return "only a Docker Compose stack has a compose document"
    directory = _directory(app, store)
    if not app.source or os.path.realpath(app.source) != os.path.realpath(directory):
        return (
            "its compose file comes from "
            f"{app.source or 'the directory it already runs from'}, not from a document Noust "
            "keeps: change the file there (a repository: in git) and update, or choose another "
            f"file of the checkout with 'noust app compose set {app.domain} --file'"
        )
    if get_installed(app.domain, store=store) is not None:
        return (
            "its stack is a recipe's: it is updated to the catalogue's version with "
            f"'noust recipe update {app.domain}'"
        )
    if not (directory / COMPOSE_FILE).is_file():
        return f"{directory / COMPOSE_FILE} is not there"
    return None


def _directory(app: App, store: NoustStore) -> Path:
    """
    Name where the document of a stack is kept.

    Args:
        app: The application.
        store: The store, beside which it lives.

    Returns:
        The directory.
    """
    return recipe_source_dir(domain_to_app_name(app.domain), store)


def _stack(domain: str, store: NoustStore) -> App:
    """
    Read the row of a stack that has a document.

    Args:
        domain: The stack's domain.
        store: The store.

    Returns:
        The row.

    Raises:
        NoustError: Nothing is deployed there.
        ValidationError: It has no document Noust keeps; the reason says which way it is edited.
    """
    app = store.get_app(validate_domain(domain))
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    reason = why_not_editable(app, store)
    if reason is not None:
        raise ValidationError(
            f"{app.domain} has no compose document to edit: {reason}", field="domain"
        )
    return app


def document_text(app: App, store: NoustStore | None = None) -> str | None:
    """
    Read the document a stack has, for the pending changes.

    Args:
        app: The application.
        store: The store; the process-wide one when None.

    Returns:
        Its text, or None when the stack has no document Noust keeps.
    """
    store = store or get_store()
    if why_not_editable(app, store) is not None:
        return None
    try:
        return (_directory(app, store) / COMPOSE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_document(
    domain: str, *, actor: str = "system", store: NoustStore | None = None
) -> StackDocument:
    """
    Read the document a stack runs from.

    A document can hold a value typed into it, so reading it for a person is audited.

    Args:
        domain: The stack's domain.
        actor: Who asked, for the audit trail.
        store: The store; the process-wide one when None.

    Returns:
        The document and where it is.

    Raises:
        NoustError: Nothing is deployed there.
        ValidationError: It has no document Noust keeps, or the file cannot be read.
    """
    store = store or get_store()
    app = _stack(domain, store)
    path = _directory(app, store) / COMPOSE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"The compose document of {app.domain} cannot be read: {exc}", field="domain"
        ) from exc
    audit.record(
        "apps.compose.document",
        target=f"app:{app.domain}",
        details={"action": "read", "by": actor},
    )
    return StackDocument(domain=app.domain, text=text, path=str(path))


def check_document(domain: str, text: str, *, store: NoustStore | None = None) -> DocumentCheck:
    """
    Judge a document for a stack without writing anything.

    Args:
        domain: The stack's domain.
        text: The document.
        store: The store; the process-wide one when None.

    Returns:
        What it is, what refuses it, and what the stack lacks to run it.

    Raises:
        NoustError: Nothing is deployed there.
        ValidationError: The stack has no document Noust keeps, or the text is not a Compose
            document at all (too large, not YAML, no services).
    """
    store = store or get_store()
    app = _stack(domain, store)
    analysis = analyze_document(text)
    # The variables are judged against the stack's own values, the way an update will judge
    # them: a document that asks for DB_PASSWORD when the stack has none stops there, minutes
    # into a build, so it is said now.
    report = compose_variables.judge(load_compose(text), read_app_env(app), file=COMPOSE_FILE)
    current = (_directory(app, store) / COMPOSE_FILE).read_text(encoding="utf-8")
    return DocumentCheck(
        analysis=analysis,
        missing=[variable.name for variable in report.missing],
        changed=_normal(text) != current,
    )


def replace_document(
    domain: str,
    text: str,
    *,
    apply: bool = False,
    actor: str = "cli",
    trigger: str = "cli",
    job_id: str | None = None,
    on_phase: Callable[[int, int, str], None] | None = None,
    on_step: Callable[[str], None] | None = None,
    logger: Logger | None = None,
    verbose: bool = False,
    store: NoustStore | None = None,
    fs: FileSystem | None = None,
) -> DocumentChange:
    """
    Make a document the one a stack runs from, and optionally run it now.

    The same guard a creation runs refuses what it refuses (root on the host, the Docker socket,
    files a document does not have), and so does a variable it requires that the stack has no
    value for. Nothing is written until it passes. With ``apply`` the stack is updated behind the
    health gate; when that fails, the previous document is back where the next update reads it
    and the stack runs it again (the error says whether it answers).

    Args:
        domain: The stack's domain.
        text: The new document.
        apply: Update the stack now instead of at its next update.
        actor: Who asked, for the audit trail.
        trigger: Who asked, for the deployment history.
        job_id: The background job driving this, when there is one.
        on_phase: Called as each phase of the update begins.
        on_step: Called as each step of the rebuild begins.
        logger: Where progress goes.
        verbose: Verbosity of the update.
        store: The store; the process-wide one when None.
        fs: The filesystem the document is written through; the process-wide one when None.

    Returns:
        What was done. Writing a document the stack already has changes nothing and updates
        nothing.

    Raises:
        NoustError: Nothing is deployed there, or the update failed.
        ValidationError: The stack has no document Noust keeps, or the document is refused; the
            details name each reason.
        DeploymentError: The document requires a variable the stack has no value for.
    """
    store = store or get_store()
    fs = fs or get_fs()
    log = logger or Logger(verbose=verbose)
    check = check_document(domain, text, store=store)
    if check.analysis.refused:
        raise ValidationError(
            "The Compose document cannot be deployed as it is",
            details="\n".join(f"- {problem}" for problem in check.analysis.refused),
            field="document",
        )
    if check.missing:
        raise DeploymentError(
            f"The document requires {', '.join(check.missing)}, and {domain} has no value for "
            + ("it" if len(check.missing) == 1 else "them"),
            details=f"Give a value first: noust env set {domain} NAME=VALUE.",
        )
    app = _stack(domain, store)
    path = _directory(app, store) / COMPOSE_FILE
    previous = path.read_text(encoding="utf-8")
    result = DocumentChange(
        domain=app.domain,
        changed=check.changed,
        services=tuple(service.name for service in check.analysis.services),
        warnings=tuple(check.analysis.warned),
    )
    if not check.changed:
        return result
    fs.write_text(path, _normal(text))
    audit.record(
        "apps.compose.document",
        target=f"app:{app.domain}",
        details={
            "action": "replace",
            "by": actor,
            "services": list(result.services),
            "bytes": len(text.encode("utf-8")),
            "applied": apply,
        },
    )
    if not apply:
        return result

    from noust.deployers.lifecycle import health_gate_for, update_app
    from noust.recipes.updates import put_stack_back

    try:
        outcome = update_app(
            app.domain,
            trigger=trigger,
            job_id=job_id,
            on_phase=on_phase,
            on_step=on_step,
            logger=log,
            verbose=verbose,
        )
    except NoustError as failure:
        put_stack_back(
            app.domain,
            domain_to_app_name(app.domain),
            {COMPOSE_FILE: previous},
            failure,
            store=store,
            log=log,
            gate=health_gate_for,
        )
        raise
    result.applied = True
    result.deployment_id = outcome.deployment_id
    if outcome.warnings:
        result.warnings = (*result.warnings, str(outcome.warnings))
    return result


def _normal(text: str) -> str:
    """
    Give a document the single trailing newline the file has.

    Args:
        text: The document as typed.

    Returns:
        The text a file holds.
    """
    return text if text.endswith("\n") else text + "\n"
