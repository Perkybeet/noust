# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a preview is given to run with: variables, and a database of its own.

A preview runs code nobody reviewed yet, on the same server as the application
it previews. Until 3.4 it was given a copy of every variable that application
had, production secrets included, and talked to its databases. Two things
change it.

**Variables.** A preview is given the variables its application's settings name
(``inherit_env``: ``allowlist``, the default for previews turned on from 3.4, or
``all`` less the excluded ones, as before) and then the preview set: the
variables an operator defined for previews alone (scope ``preview`` of
:mod:`noust.core.variables`), which win. A variable that is made of references
is copied as the references, not as what they resolved to for the application,
so ``NEXTAUTH_URL={{app.URL}}`` is the preview's own address.

**Data.** ``data_mode`` says whether a preview shares its application's
databases (``shared``, the 2.2 behaviour: a pull request can write into
production's), or gets an empty database of its own (``empty``). A preview that has a database
of its own is given no variable that names the application's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from noust.core.preview_store import (
    DATA_SHARED,
    INHERIT_ALL,
    PreviewOptions,
    PreviewStore,
)
from noust.core.store import App, PreviewSettings
from noust.core.variables.model import BOTH, PREVIEW
from noust.core.variables.predefined import RESERVED
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.app_env import read_environment_of
from noust.managers.database.isolation import DatabaseWish, connection_variables, own_database

#: Variables the unit sets itself: a preview runs on its own port, and a stack's
#: Compose project is named after its own directory, never after the original's.
_MANAGED = frozenset({"PORT", "NODE_ENV", "COMPOSE_PROJECT_NAME"}) | RESERVED


@dataclass(frozen=True)
class PreviewEnvironment:
    """
    The variables a preview is given.

    Attributes:
        values: Name to value; a value made of references is the references.
        audience: Names whose audience is not ``both``.
        marks: The secret marks it carries (the application's, the preview set's).
        given: Every name the policy gave it.
    """

    values: dict[str, str] = field(default_factory=dict)
    audience: dict[str, str] = field(default_factory=dict)
    marks: dict[str, bool] = field(default_factory=dict)
    given: tuple[str, ...] = ()


def preview_set(parent: App) -> dict[str, tuple[str, bool | None, str]]:
    """
    Read the variables defined for the previews of an application.

    Args:
        parent: The application previewed.

    Returns:
        Name to its value, its secret mark and its audience.
    """
    if parent.id is None:
        return {}
    return {
        v.name: (v.value, v.secret, v.audience) for v in VariableStore().list(PREVIEW, parent.id)
    }


def compose(
    parent: App, settings: PreviewSettings, options: PreviewOptions | None = None
) -> PreviewEnvironment:
    """
    Decide which variables a preview of an application is given.

    Args:
        parent: The application previewed.
        settings: Its preview settings.
        options: How its previews are made; read from the store when None.

    Returns:
        The variables, the audiences, the secret marks and the names given.
    """
    chosen = options or PreviewStore().options(parent.domain)
    environment = read_environment_of(parent)
    if chosen.inherit_env == INHERIT_ALL:
        names = [
            name
            for name in environment.values
            if name not in _MANAGED and name not in settings.exclude_env
        ]
    else:
        names = [n for n in chosen.allow_env if n in environment.values and n not in _MANAGED]
    values = {name: environment.expressions.get(name, environment.values[name]) for name in names}
    audience = {
        name: environment.audience_of(name)
        for name in names
        if environment.audience_of(name) != BOTH
    }
    marks = {name: mark for name, mark in parent.env_secret_marks.items() if name in names}
    if chosen.data_mode != DATA_SHARED:
        # A preview with a database of its own is given nothing that reaches the
        # application's: the connection variables are made for it instead.
        for name in connection_variables(environment.values):
            if name in values:
                del values[name]
                audience.pop(name, None)
                marks.pop(name, None)
    for name, (value, secret, kind) in preview_set(parent).items():
        values[name] = value
        if kind != BOTH:
            audience[name] = kind
        else:
            audience.pop(name, None)
        if secret is not None:
            marks[name] = secret
        else:
            marks.pop(name, None)
    return PreviewEnvironment(values, audience, marks, tuple(values))


def database_wish(parent: App, sequence: int) -> DatabaseWish | None:
    """
    Decide the database a preview gets of its own.

    Args:
        parent: The application previewed.
        sequence: A number that tells this preview's database from another's.

    Returns:
        What to make; None when the application connects to no database it
        has recorded.
    """
    return own_database(parent, read_environment_of(parent).values, f"pr{sequence}")
