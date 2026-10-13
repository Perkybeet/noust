# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning a value with ``{{...}}`` references into the value the application runs with.

One resolver per deployment, built for one application: it knows which team,
project and environment the application is in, so ``{{project.NAME}}`` means
that application's project's variable and nothing else's, and what the
deployment is (:class:`~noust.core.variables.predefined.Deployment`), so
``{{app.URL}}`` is where this application answers. Every failure is an error
that names the reference and says how to fix it; nothing resolves to an empty
string, because an application started with a value nobody noticed is missing
is worse than a deployment that does not start.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from noust.core.store import NoustStore, get_store
from noust.core.variables import expressions
from noust.core.variables.expressions import APP, SECRET, Reference
from noust.core.variables.model import ENVIRONMENT, PROJECT, SERVER, TEAM
from noust.core.variables.predefined import Deployment
from noust.core.variables.providers import ProviderRegistry
from noust.core.variables.store import VariableStore
from noust.validators.environment import EnvironmentValidationError

#: How deep references may nest (a team variable that names an environment one...).
MAX_DEPTH = 6


class UnresolvedReferenceError(EnvironmentValidationError):
    """A reference names something that does not exist, or that this application may not use."""


@dataclass(frozen=True)
class Context:
    """
    Whose variables are being resolved.

    Attributes:
        domain: The application's domain.
        team_id: Its team, or None when it has none yet.
        project_id: Its project, or None.
        environment_id: Its environment, or None.
        deployment: What is known about the deployment.
    """

    domain: str
    team_id: int | None = None
    project_id: int | None = None
    environment_id: int | None = None
    deployment: Deployment = field(default_factory=lambda: Deployment(domain=""))


def context_for(
    domain: str, deployment: Deployment | None = None, *, store: NoustStore | None = None
) -> Context:
    """
    Build the context of an application from where the store says it lives.

    Args:
        domain: The application's domain.
        deployment: What is known about the deployment; just the domain by default.
        store: The store; the process-wide one when None.

    Returns:
        The context. An application the store does not know is in no scope.
    """
    target = store or get_store()
    deployment = deployment or Deployment(domain=domain)
    with target._transaction() as cursor:
        cursor.execute(
            "SELECT a.project_id, a.environment_id, p.team_id FROM apps a "
            "LEFT JOIN projects p ON p.id = a.project_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return Context(domain=domain, deployment=deployment)
    return Context(
        domain=domain,
        team_id=None if row[2] is None else int(row[2]),
        project_id=None if row[0] is None else int(row[0]),
        environment_id=None if row[1] is None else int(row[1]),
        deployment=deployment,
    )


class Resolver:
    """
    Resolves the references in the values of one application.

    Args:
        context: Whose variables.
        variables: Where shared variables are; the store's by default.
        providers: Where secrets are fetched; the store's by default.
    """

    def __init__(
        self,
        context: Context,
        *,
        variables: VariableStore | None = None,
        providers: ProviderRegistry | None = None,
    ) -> None:
        self.context = context
        self._variables = variables or VariableStore()
        self._providers = providers or ProviderRegistry()
        self._shared: dict[tuple[str, str], str | None] = {}
        self._secrets: dict[str, str] = {}
        #: Every value fetched from a secret manager, so the caller can keep it out of logs.
        self.fetched: list[str] = []

    def resolve(self, value: str) -> str:
        """
        Args:
            value: A variable's value, possibly with references.

        Returns:
            The value with every reference replaced.

        Raises:
            UnresolvedReferenceError: A reference that cannot be resolved, a
                cycle, or nesting deeper than :data:`MAX_DEPTH`.
            SecretProviderError: A secret manager could not give a value.
        """
        return self._resolve(value, ())

    def resolve_all(self, values: Mapping[str, str]) -> dict[str, str]:
        """
        Args:
            values: Variable name to value.

        Returns:
            The same names with their values resolved.

        Raises:
            UnresolvedReferenceError: As :meth:`resolve`, for the first that fails.
        """
        return {name: self.resolve(value) for name, value in values.items()}

    def check(self, value: str) -> None:
        """
        Check that the references in a value are well formed, without fetching anything.

        Args:
            value: A variable's value.

        Raises:
            ValidationError: A reference that is not one of the three kinds' syntax.
        """
        for reference in expressions.find_references(value):
            expressions.check_reference(reference)

    # Internals ---------------------------------------------------------------

    def _resolve(self, value: str, stack: tuple[str, ...]) -> str:
        if len(stack) > MAX_DEPTH:
            raise UnresolvedReferenceError(
                f"References nest more than {MAX_DEPTH} deep: {' -> '.join(stack)}",
                details="A shared variable that refers to another that refers to another... "
                "is hard to read. Flatten them.",
            )
        return expressions.substitute(value, lambda reference: self._reference(reference, stack))

    def _reference(self, reference: Reference, stack: tuple[str, ...]) -> str:
        expressions.check_reference(reference)
        if reference.kind == APP:
            return self._app_field(reference)
        if reference.kind == SECRET:
            return self._secret(reference)
        return self._shared_value(reference, stack)

    def _app_field(self, reference: Reference) -> str:
        found = self.context.deployment.fields().get(reference.key)
        if found is None:
            raise UnresolvedReferenceError(
                f"{reference.text} has no value for {self.context.domain} yet",
                details=f"{reference.key} is known once the application has "
                + {
                    "COMMIT": "been built from a commit.",
                    "RELEASE": "a release (applications on the release layout).",
                    "BRANCH": "a branch it follows.",
                    "PORT": "a port (static sites have none).",
                }.get(reference.key, "been deployed."),
            )
        return found

    def _secret(self, reference: Reference) -> str:
        cached = self._secrets.get(reference.key)
        if cached is not None:
            return cached
        value = self._providers.fetch(
            reference.provider, reference.path, team_id=self.context.team_id
        )
        self._secrets[reference.key] = value
        if value:
            self.fetched.append(value)
        return value

    def _owner(self, scope: str) -> int | None:
        return {
            SERVER: 0,
            TEAM: self.context.team_id,
            PROJECT: self.context.project_id,
            ENVIRONMENT: self.context.environment_id,
        }[scope]

    def _shared_value(self, reference: Reference, stack: tuple[str, ...]) -> str:
        scope, name = reference.kind, reference.key
        owner = self._owner(scope)
        if owner is None:
            raise UnresolvedReferenceError(
                f"{reference.text}: {self.context.domain} is in no {scope}",
                details=f"Put the application in a {scope} first (noust app move "
                f"{self.context.domain}) or drop the reference.",
            )
        link = f"{scope}.{name}"
        if link in stack:
            raise UnresolvedReferenceError(
                f"{reference.text} refers to itself: {' -> '.join((*stack, link))}",
                details="Break the cycle: one of these variables has to hold a plain value.",
            )
        key = (scope, name)
        if key not in self._shared:
            found = self._variables.get(scope, owner, name)
            self._shared[key] = found.value if found is not None else None
        raw = self._shared[key]
        if raw is None:
            raise UnresolvedReferenceError(
                f"{reference.text}: the {scope} has no variable {name}",
                details=f"Define it: noust variables set {scope} {name} VALUE"
                + (" --team TEAM" if scope == TEAM else "")
                + (" --team TEAM --project PROJECT" if scope == PROJECT else "")
                + (
                    " --team TEAM --project PROJECT --environment ENV"
                    if scope == ENVIRONMENT
                    else ""
                )
                + ".",
            )
        return self._resolve(raw, (*stack, link))
