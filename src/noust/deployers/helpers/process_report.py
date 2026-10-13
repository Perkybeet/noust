# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's declared process types do, as the console and ``noust app processes`` say it.

:mod:`noust.deployers.helpers.processes` reads a tree; this module reads the tree an application
runs now and adds what a deployment would do with each process type: whether ``web`` is the
command of the unit, whether ``release`` runs before a new version serves, and why one that
looks as if it should is not in effect (the build block's ``start`` wins; a ``Procfile`` is only
honoured by an application deployed with one). It is read-only: nothing here changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from noust.core import app_build_store
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store
from noust.deployers.helpers.build_config import describe_build, project_dir
from noust.deployers.helpers.hooks import resolve_hooks
from noust.deployers.helpers.layout import code_path_for
from noust.deployers.helpers.processes import RELEASE, WEB, ProcessSet, resolve_processes
from noust.deployers.registry import DeployerRegistry, _import_deployers


@dataclass(frozen=True)
class AppProcesses:
    """
    The process types of one application, and what each does.

    Attributes:
        domain: The application.
        found: What its code declares.
        web_in_effect: Whether the unit runs the ``web`` process.
        release_in_effect: Whether the ``release`` process runs before activation.
        procfile_honoured: Whether the application reads its ``Procfile``.
        notes: Why a declared process is not in effect, and what to do.
    """

    domain: str
    found: ProcessSet
    web_in_effect: bool = False
    release_in_effect: bool = False
    procfile_honoured: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            Every process type, with whether it is in effect, and the notes.
        """
        in_effect = {WEB: self.web_in_effect, RELEASE: self.release_in_effect}
        return {
            "domain": self.domain,
            "source": self.found.source,
            "problem": self.found.problem,
            "procfile_honoured": self.procfile_honoured,
            "processes": [
                {**process.to_dict(), "in_effect": in_effect.get(process.name, False)}
                for process in self.found
            ],
            "notes": list(self.notes),
        }


def describe_processes(domain: str, *, store: NoustStore | None = None) -> AppProcesses:
    """
    Say which process types an application declares and which of them a deployment uses.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        The report, read from the code that runs now.

    Raises:
        NoustError: The application is unknown.
    """
    target = store or get_store()
    build = describe_build(domain, store=target)
    app = target.get_app(domain)
    if app is None:  # describe_build raised already; this is for the type checker
        raise NoustError(f"Application not found: {domain}")
    tree = code_path_for(app)
    try:
        project = project_dir(tree, build.build.base_directory)
        found = resolve_processes(project)
    except ValidationError as exc:
        return AppProcesses(
            domain, ProcessSet(problem=f"{exc.message}. {exc.details}".strip()), notes=[]
        )

    applied = app_build_store.get_applied(domain, store=target)
    honoured = bool(applied and applied.get("procfile"))
    notes: list[str] = []
    allowed = found.source == "noust.yaml" or (found.source == "Procfile" and honoured)
    if found.source == "Procfile" and not honoured and found.declared:
        notes.append(
            "This application was deployed before its Procfile was read, so it keeps starting "
            "the way it was deployed. Declare the same under processes: in noust.yaml to have "
            "it used, or create the application again."
        )

    _import_deployers()
    deployer = DeployerRegistry.get(app.app_type)
    supports_start = "start" in getattr(deployer, "BUILD_KEYS", frozenset())

    web = found.named(WEB)
    web_in_effect = False
    if web is not None and allowed:
        if not supports_start:
            notes.append(f"A {app.app_type} application runs no process: web is not used.")
        elif build.build.start is not None or build.build.publish_directory:
            notes.append(
                "web is not used: the build block's start (or publish directory) wins, "
                f"from {'the operator' if build.build.source == 'operator' else 'noust.yaml'}."
            )
        else:
            web_in_effect = True

    release = found.named(RELEASE)
    release_in_effect = False
    if release is not None and allowed:
        hooks = resolve_hooks(app, tree, target)
        if hooks.source == "operator" or hooks.declared:
            notes.append("release is not used: deploy hooks are declared, and they win.")
        else:
            release_in_effect = True

    others = [process.name for process in found if not process.started_by_noust]
    if others:
        notes.append(
            f"{', '.join(others)} {'is' if len(others) == 1 else 'are'} declared here; a "
            "deployment starts only web and runs release."
        )
    return AppProcesses(
        domain,
        found,
        web_in_effect=web_in_effect,
        release_in_effect=release_in_effect,
        procfile_honoured=honoured,
        notes=notes,
    )
