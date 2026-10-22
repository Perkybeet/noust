# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a deployment does about an application's variables, for every kind of application.

A variable has an audience (the build, the running application, or both), may be made of
``{{...}}`` references that a deploy resolves again, and is accompanied by what Noust says about
the deployment (``NOUST_URL``...). Those rules used to live in the deployer of the applications
that run a process, so a Docker Compose stack, a container and a monorepo silently went without:
a variable "only for the build" was written to no file and given to nobody, a rotated secret never
reached them, and none of them was told its own commit (checklist B-43, B-46 and B-48). This
module is the one implementation (rule 3). A deployer owns when each step happens and where the
result goes; what the steps decide is here:

- :class:`DeploymentVariables` resolves the references of a deployment, remembers what the
  variables given at creation are made of and for (even before the application has a row), and
  says what the install and the build are given beyond the ``.env``;
- :class:`BuildEnvironment` is that answer: the variables to add and the names to take away.

Where each kind of variable goes for each type of application (and what is refused) is decided in
:mod:`noust.core.variables.placement`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from noust.core.exceptions import NoustError
from noust.core.fs import DryRunFileSystem, FileSystem
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.core.variables import expressions
from noust.core.variables.predefined import Deployment
from noust.core.variables.resolver import Resolver, context_for
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.app_env import read_environment_of, refresh_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.env_variables import (
    AppEnvironment,
    EnvironmentPlan,
    record_given,
)

#: Failures while recording what the variables are made of: reported, never fatal. The ``.env``
#: on disk is the truth; the row only lets the next deploy resolve a reference again.
_RECORDING_ERRORS = (NoustError, sqlite3.Error)


@dataclass(frozen=True)
class BuildEnvironment:
    """
    What an install or a build is given beyond the ``.env``, and what it is not.

    Attributes:
        extra: The variables to add: the ones only the build is given and what Noust says about
            the deployment, less what changes on every deploy.
        hidden: The names to take away: the ones only the running application is given.
    """

    extra: Mapping[str, str] = field(default_factory=dict)
    hidden: tuple[str, ...] = ()

    def blanked(self) -> dict[str, str]:
        """
        Returns:
            The environment of a process that reads the ``.env`` itself (``docker compose``):
            what is added, and each hidden name set to empty, because a name that is merely
            absent would be filled from the file.
        """
        return {**self.extra, **dict.fromkeys(self.hidden, "")}


def build_environment_of(environment: AppEnvironment, deployment: Deployment) -> BuildEnvironment:
    """
    Decide what an install or a build is given for a set of variables.

    Args:
        environment: The application's variables with their audience.
        deployment: What is known about the deployment.

    Returns:
        The variables to add and the names to take away.
    """
    return BuildEnvironment(
        extra={**deployment.environment(build=True), **environment.build_only},
        hidden=tuple(environment.runtime_only_names),
    )


def describe_deployment(
    store: NoustStore,
    domain: str,
    *,
    row: App | None,
    ssl: bool,
    branch: str | None,
    commit: str | None,
    release: str | None,
    port: int | None,
    preview: bool = False,
) -> Deployment:
    """
    Describe a deployment for the references and the variables Noust sets.

    The one place the facts are put together, whichever deployer asks: what the application's
    row says wins over what the deployer was configured with once the application exists.

    Args:
        store: The store.
        domain: The application's domain.
        row: The application's row, when it has one.
        ssl: Whether the deployer was asked for TLS.
        branch: The branch being built.
        commit: The commit being built or running.
        release: The release id.
        port: The port the process listens on.
        preview: Whether the application is a preview of another.

    Returns:
        Where the application answers and what is being built.
    """
    from noust.core.preview_store import preview_identity

    pull_request, preview_of = (
        preview_identity(store, domain)
        if preview or (row is not None and row.preview_parent)
        else (None, None)
    )
    return Deployment(
        domain=domain,
        ssl=bool(row.ssl_enabled) if row is not None else ssl,
        branch=branch,
        commit=commit,
        release=release,
        port=port,
        pull_request=pull_request,
        preview_of=preview_of,
    )


class DeploymentVariables:
    """
    The variables of one deployment: resolved again, remembered and given to the build.

    A deployer builds one for the application it deploys, saying how to reach the pieces that
    differ between types (the row, the facts of the deployment, the files). Everything it does is
    skipped under ``--dry-run``.

    Args:
        store: The store.
        domain: The application's domain.
        logger: Where progress is reported.
        fs: The filesystem the variables' files are written through, looked up at each use.
        manager: The writer of environment files, looked up at each use.
        row: The application's row, or None while it has none.
        deployment: What is known about the deployment now (commit, release, port).
        audience: The audience of the variables given at creation (a clone, an import).
    """

    def __init__(
        self,
        *,
        store: NoustStore,
        domain: str,
        logger: Logger,
        fs: Callable[[], FileSystem],
        manager: Callable[[], EnvManager],
        row: Callable[[], App | None],
        deployment: Callable[[], Deployment],
        audience: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._domain = domain
        self._logger = logger
        self._fs = fs
        self._manager = manager
        self._row = row
        self._deployment = deployment
        self.audience: dict[str, str] = dict(audience or {})
        self._cache: BuildEnvironment | None = None
        #: What was given at creation for an application with no row yet: (every variable, the
        #: text of the ones made of references, the names the creation speaks for).
        self._pending: tuple[dict[str, str], dict[str, str], set[str]] | None = None

    @property
    def rehearsing(self) -> bool:
        """Whether this is a ``--dry-run``, where nothing is recorded or resolved."""
        return isinstance(self._fs(), DryRunFileSystem)

    # -- The references --------------------------------------------------------------------------

    def resolver(self) -> Resolver:
        """
        Returns:
            The resolver of this application's references for this deployment.
        """
        return Resolver(context_for(self._domain, self._deployment(), store=self._store))

    def expand(self, asked: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        """
        Resolve the ``{{...}}`` references in the variables a deployment was given.

        Args:
            asked: The variables as given, validated.

        Returns:
            The variables with their references resolved, and the text of each one that had any
            (what a later deploy resolves again).

        Raises:
            ValidationError: A reference that is not well formed.
            UnresolvedReferenceError: A reference that cannot be resolved.
            SecretProviderError: A secret manager could not give a secret.
        """
        values = dict(asked)
        written_as: dict[str, str] = {}
        resolver: Resolver | None = None
        for name, raw in asked.items():
            if not expressions.has_markup(raw):
                continue
            for reference in expressions.find_references(raw):
                expressions.check_reference(reference)
            resolver = resolver or self.resolver()
            written_as[name] = raw
            values[name] = resolver.resolve(raw)
        return values, written_as

    def refresh(self) -> EnvironmentPlan | None:
        """
        Resolve the application's references again, before anything is built.

        A shared variable that changed, a secret that was rotated, the commit and the release
        being built: they reach the ``.env`` (and the values only the build is given) here.
        Nothing is written when none changed, and an application without references costs one
        query.

        Returns:
            The plan (``changed`` names the variables whose value is new), or None when there
            was nothing to resolve.

        Raises:
            UnresolvedReferenceError: A reference no longer resolves; the deployment stops here,
                with what served before untouched.
            SecretProviderError: A secret manager could not give a secret.
        """
        app = self._row()
        if app is None or app.id is None or self.rehearsing:
            return None
        if not VariableStore(self._store).app_variables(app.id):
            return None
        plan = refresh_app_env(
            app,
            resolver=self.resolver(),
            manager=self._manager(),
            fs=self._fs(),
            logger=self._logger,
        )
        self._cache = None
        if plan.changed:
            self._logger.substep(f"Resolved {len(plan.changed)} variable(s) again")
        return plan

    # -- What the variables given at creation are made of -----------------------------------------

    def remember(
        self, everything: Mapping[str, str], written_as: Mapping[str, str], *, names: set[str]
    ) -> None:
        """
        Remember what the variables given at creation are made of and for.

        An application with no row yet (a Compose stack registers itself once it serves) keeps
        them until :meth:`flush`, and the build already sees its build-only variables.

        Args:
            everything: Every variable the application has now.
            written_as: The text of the ones made of references.
            names: The names the creation's set speaks for.
        """
        if self.rehearsing:
            return
        self._cache = None
        app = self._row()
        if app is None or app.id is None:
            self._pending = (dict(everything), dict(written_as), set(names))
            return
        self._record(app, everything, written_as, names)

    def flush(self) -> None:
        """Record what :meth:`remember` held back, once the application has a row."""
        if self._pending is None:
            return
        app = self._row()
        if app is None or app.id is None:
            return
        everything, written_as, names = self._pending
        self._pending = None
        self._record(app, everything, written_as, names)

    def _record(
        self,
        app: App,
        everything: Mapping[str, str],
        written_as: Mapping[str, str],
        names: set[str],
    ) -> None:
        """
        Store the rows for the variables a creation gave.

        Args:
            app: The application, with a row.
            everything: Every variable the application has now.
            written_as: The text of the ones made of references.
            names: The names the creation's set speaks for.
        """
        if not (
            written_as or self.audience or VariableStore(self._store).app_variables(app.id or 0)
        ):
            return
        self._cache = None
        try:
            record_given(
                app,
                values=everything,
                expressions=written_as,
                audience=self.audience,
                names=names,
            )
        except _RECORDING_ERRORS as exc:
            self._logger.warning(f"Could not record what the variables are made of: {exc}")

    # -- What the build is given -----------------------------------------------------------------

    def build_environment(self) -> BuildEnvironment:
        """
        Say what an install or a build is given beyond the ``.env``, and what it is not.

        Returns:
            The variables to add and the names to take away. Read once per deployment, from the
            store, or from what the creation gave while the application has no row.
        """
        if self._cache is None:
            row = self._row()
            if row is not None and row.id is not None:
                environment = read_environment_of(row, manager=self._manager())
            elif self._pending is not None:
                values, written_as, _names = self._pending
                environment = AppEnvironment(
                    values,
                    {n: k for n, k in self.audience.items() if n in values},
                    written_as,
                )
            else:
                # Nothing is known of this application yet: it is given nothing it
                # has not asked for.
                return BuildEnvironment()
            self._cache = build_environment_of(environment, self._deployment())
        return self._cache

    def invalidate(self) -> None:
        """Forget what was read: the variables changed under it."""
        self._cache = None
