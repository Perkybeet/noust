# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's variables are made of: references, audiences and the build.

An application's ``.env`` holds the values it runs with. Around it the store
keeps, per variable (:mod:`noust.core.variables.store`):

- the **expression** a value is made from, when it holds ``{{...}}`` references
  (shared variables of a scope, the deployment's own facts, a secret manager's
  secrets). The file holds the resolved value, so systemd, the backups and every
  tool that reads it keep working; the expression is what a deploy resolves
  again, so a changed shared variable or a rotated secret reaches the next release;
- its **audience**: ``both`` (the default), ``build`` (the install and the build
  see it, the running application never does: an npm token) or ``runtime`` (the
  running application sees it, the build does not).

This module is the one place that decides, for a complete set of variables
someone wants an application to have, which expression and audience each has
and what it resolves to (:func:`plan_environment`), and the one place that
turns that decision into a file and rows (:func:`commit_environment`). The
console, ``noust env``, a deploy and a preview all come through here.

Everything is decided before anything is written: a reference that cannot be
resolved leaves the environment as it was (it fails closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from noust.core.exceptions import ValidationError
from noust.core.store import App
from noust.core.variables import expressions
from noust.core.variables.model import BOTH, BUILD, RUNTIME, validate_audience
from noust.core.variables.predefined import RELEASE_ENV_NAME, RESERVED, Deployment, reserved_in
from noust.core.variables.resolver import Resolver, context_for
from noust.core.variables.store import AppVariable, VariableStore
from noust.validators.environment import EnvironmentValidationError, validate_environment


@dataclass(frozen=True)
class AppEnvironment:
    """
    An application's variables as the store and its file hold them.

    Attributes:
        values: Every variable's current value, build-only ones included.
        audience: The names whose audience is not ``both``, with it.
        expressions: The names whose value is made from references, with the text.
    """

    values: dict[str, str] = field(default_factory=dict)
    audience: dict[str, str] = field(default_factory=dict)
    expressions: dict[str, str] = field(default_factory=dict)

    def audience_of(self, name: str) -> str:
        """
        Args:
            name: A variable's name.

        Returns:
            ``both``, ``build`` or ``runtime``.
        """
        return self.audience.get(name, BOTH)

    @property
    def build_only(self) -> dict[str, str]:
        """The variables only the install and the build are given."""
        return {n: v for n, v in self.values.items() if self.audience_of(n) == BUILD}

    @property
    def runtime(self) -> dict[str, str]:
        """The variables the running application is given (what ``.env`` holds)."""
        return {n: v for n, v in self.values.items() if self.audience_of(n) != BUILD}

    @property
    def runtime_only_names(self) -> list[str]:
        """The names the build is not given."""
        return sorted(n for n in self.values if self.audience_of(n) == RUNTIME)


@dataclass(frozen=True)
class EnvironmentPlan:
    """
    What a set of variables becomes: decided, not yet written.

    Attributes:
        environment: The variables as they will be.
        fetched: Values fetched from a secret manager while planning, to keep
            out of logs.
        changed: The names whose resolved value differs from before.
    """

    environment: AppEnvironment
    fetched: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()


def deployment_for(
    app: App,
    *,
    branch: str | None = None,
    commit: str | None = None,
    release: str | None = None,
) -> Deployment:
    """
    Describe a deployment of an application for its variables.

    Args:
        app: The application's row.
        branch: The branch being built; the row's by default.
        commit: The commit being built or running.
        release: The release id.

    Returns:
        The deployment.
    """
    return Deployment(
        domain=app.domain,
        ssl=bool(app.ssl_enabled),
        branch=branch if branch is not None else app.branch,
        commit=commit,
        release=release,
        port=app.port,
    )


def resolver_for(app: App, deployment: Deployment | None = None) -> Resolver:
    """
    Build the resolver of an application's variables.

    Args:
        app: The application's row.
        deployment: What is known about the deployment; the row's facts by default.

    Returns:
        The resolver.
    """
    return Resolver(context_for(app.domain, deployment or deployment_for(app)))


def read_environment(app: App, file_values: Mapping[str, str]) -> AppEnvironment:
    """
    Join an application's file with what the store knows around it.

    Args:
        app: The application's row.
        file_values: What its ``.env`` holds.

    Returns:
        Every variable, with its audience and expression. An application the
        store has no id for (a directory found on disk) is its file alone.
    """
    values = dict(file_values)
    audience: dict[str, str] = {}
    exprs: dict[str, str] = {}
    if app.id is not None:
        for name, row in VariableStore().app_variables(app.id).items():
            if row.audience == BUILD:
                if row.build_value is None:
                    continue
                values[name] = row.build_value
                audience[name] = BUILD
            elif name not in values:
                # A row for a variable the file no longer holds: stale, not part of it.
                continue
            elif row.audience != BOTH:
                audience[name] = row.audience
            if row.expression is not None:
                exprs[name] = row.expression
    return AppEnvironment(values, audience, exprs)


def plan_environment(
    values: Mapping[str, object],
    *,
    previous: AppEnvironment,
    audience: Mapping[str, str] | None = None,
    resolver: Resolver,
) -> EnvironmentPlan:
    """
    Decide what a complete set of variables means for an application.

    A value with a reference becomes an expression and is resolved. A value
    equal to what a variable with an expression resolved to before keeps that
    expression (a client that read the resolved environment and wrote it back
    changes nothing). Anything else is a literal.

    Args:
        values: The complete set, names to the text typed.
        previous: What the application has now.
        audience: Names to ``both``, ``build`` or ``runtime``; a name left out
            keeps its audience.
        resolver: Resolves the references.

    Returns:
        The plan.

    Raises:
        EnvironmentValidationError: A name or a value a unit cannot hold, or a
            name Noust sets itself (``NOUST_URL``...) with a value of its own.
        ValidationError: A reference that is not well formed.
        UnresolvedReferenceError: A reference that cannot be resolved.
        SecretProviderError: A secret manager could not give a secret.
    """
    clean = validate_environment(values, multiline=True)
    taken = [n for n in reserved_in(clean) if clean[n] != previous.values.get(n)]
    if taken:
        raise EnvironmentValidationError(
            f"{', '.join(taken)} {'is' if len(taken) == 1 else 'are'} set by Noust",
            details="NOUST_URL, NOUST_DOMAIN, NOUST_BRANCH, NOUST_COMMIT and NOUST_RELEASE "
            "describe the deployment and are given to the application on their own. "
            "Refer to them in another value with {{app.URL}} and the like.",
        )
    wanted = dict(audience or {})
    unknown = sorted(set(wanted) - set(clean))
    if unknown:
        raise ValidationError(
            f"An audience was given for variables that are not in the environment: "
            f"{', '.join(unknown)}",
            field="audience",
        )
    resolved: dict[str, str] = {}
    exprs: dict[str, str] = {}
    chosen: dict[str, str] = {}
    for name, raw in clean.items():
        kind = validate_audience(wanted.get(name) or previous.audience_of(name))
        if kind != BOTH:
            chosen[name] = kind
        if expressions.has_markup(raw):
            for reference in expressions.find_references(raw):
                expressions.check_reference(reference)
            exprs[name] = raw
            resolved[name] = resolver.resolve(raw)
        elif name in previous.expressions and raw == previous.values.get(name):
            exprs[name] = previous.expressions[name]
            resolved[name] = raw
        else:
            resolved[name] = raw
    changed = tuple(
        sorted(
            n
            for n in set(resolved) | set(previous.values)
            if resolved.get(n) != previous.values.get(n)
        )
    )
    return EnvironmentPlan(
        AppEnvironment(resolved, chosen, exprs), tuple(resolver.fetched), changed
    )


def rows_for(environment: AppEnvironment) -> list[AppVariable]:
    """
    Args:
        environment: The variables as they will be.

    Returns:
        The rows the store keeps for them: only the variables with something
        beyond a plain value for both audiences.
    """
    rows = []
    for name in environment.values:
        kind = environment.audience_of(name)
        expression = environment.expressions.get(name)
        build_value = environment.values[name] if kind == BUILD else None
        if kind == BOTH and expression is None:
            continue
        rows.append(AppVariable(name, kind, expression, build_value))
    return rows


def refresh_plan(previous: AppEnvironment, *, resolver: Resolver) -> EnvironmentPlan:
    """
    Resolve every expression again, as a deploy does.

    Args:
        previous: What the application has now.
        resolver: The deployment's resolver.

    Returns:
        The plan: the same variables, with each expression's value as it is now.

    Raises:
        UnresolvedReferenceError: A reference that no longer resolves; the
            deployment stops before building anything.
        SecretProviderError: A secret manager could not give a secret.
    """
    values = dict(previous.values)
    for name, expression in previous.expressions.items():
        values[name] = resolver.resolve(expression)
    changed = tuple(sorted(n for n in values if values[n] != previous.values.get(n)))
    return EnvironmentPlan(
        AppEnvironment(values, dict(previous.audience), dict(previous.expressions)),
        tuple(resolver.fetched),
        changed,
    )


def secret_marks_for(expressions_by_name: Mapping[str, str]) -> dict[str, bool]:
    """
    Name the variables made of a secret manager's value: they are secrets.

    Whatever the name or the value look like, a value that was fetched from a
    secret manager is shown masked and scrubbed from logs. The operator's own
    mark, if there is one, still wins.

    Args:
        expressions_by_name: Variable name to the text its value is made from.

    Returns:
        ``{name: True}`` for each one with a ``{{secret:...}}`` in it.
    """
    return {
        name: True
        for name, text in expressions_by_name.items()
        if any(ref.kind == expressions.SECRET for ref in expressions.find_references(text))
    }


def record_given(
    app: App,
    *,
    values: Mapping[str, str],
    expressions: Mapping[str, str],
    audience: Mapping[str, str],
    names: set[str],
) -> None:
    """
    Remember what the variables a deployment was given are made of and for.

    Args:
        app: The application's row.
        values: Every variable the application has now.
        expressions: The text of the ones made of references.
        audience: Names to ``build`` or ``runtime``; the rest are for both.
        names: The names the deployment's set speaks for; the store's rows for
            any other name are kept as they are.
    """
    if app.id is None:
        return
    store = VariableStore()
    rows = store.app_variables(app.id)
    for name in names:
        kind = audience.get(name, BOTH)
        expression = expressions.get(name)
        if kind == BOTH and expression is None:
            rows.pop(name, None)
            continue
        rows[name] = AppVariable(
            name, kind, expression, values.get(name) if kind == BUILD else None
        )
    store.set_app_variables(app.id, [row for name, row in rows.items() if name in values])
    marks = secret_marks_for(expressions)
    if marks:
        from noust.core.store import get_store

        get_store().set_env_secret_marks(app.domain, {**marks, **app.env_secret_marks})


def predefined_for(app: App) -> dict[str, str]:
    """
    Say what Noust gives an application about its deployment, as it is now.

    Args:
        app: The application's row.

    Returns:
        ``NOUST_*`` to value: what the release that runs holds, or, for an
        application that has not been given a file yet (it was deployed before
        3.4 and not updated since), what its row says.
    """
    from noust.deployers.helpers.env_manager import EnvManager
    from noust.deployers.helpers.layout import code_path_for

    try:
        path = code_path_for(app) / RELEASE_ENV_NAME
        stored = (
            EnvManager().read_env_file(path) if path.is_file() and not path.is_symlink() else {}
        )
    except OSError:
        stored = {}
    known = {name: value for name, value in stored.items() if name in RESERVED}
    return known or deployment_for(app).environment()
