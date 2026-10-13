# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a shared variable is, and the rules between its parts.

Pure data and validation, with no store behind it, so the CLI, the API and the
store agree on one definition. A *shared variable* is defined once for a scope
(the whole server, a team, a project, an environment, or the previews of one
application) and used by reference from the variables of an application:
``DATABASE_URL={{project.DATABASE_URL}}``. It is never injected by itself, so a
team's database password does not reach every application the team owns just
because they sit in the same project.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.core.exceptions import ValidationError
from noust.validators.environment import EnvironmentValidationError, validate_env_value

#: Whose a shared variable is. ``preview`` is the set the previews of one
#: application are given (it is never a target of a ``{{...}}`` reference).
SERVER = "server"
TEAM = "team"
PROJECT = "project"
ENVIRONMENT = "environment"
PREVIEW = "preview"

#: The scopes a reference can name, broadest first. A variable is looked up in
#: exactly the scope its reference names; there is no fall-through from one to
#: the next, so what a value comes from is always readable in the value itself.
REFERENCE_SCOPES: tuple[str, ...] = (SERVER, TEAM, PROJECT, ENVIRONMENT)

#: Every scope the store holds variables for.
SCOPES: tuple[str, ...] = (*REFERENCE_SCOPES, PREVIEW)

#: Which part of a deployment is given a variable.
BOTH = "both"
BUILD = "build"
RUNTIME = "runtime"
AUDIENCES: tuple[str, ...] = (BOTH, BUILD, RUNTIME)

#: How many variables one scope may hold. A scope is a handful of settings;
#: more is a sign it is being used as a file store.
MAX_VARIABLES = 500

#: The longest value kept, in bytes. A certificate or a private key fits.
MAX_VALUE_BYTES = 32 * 1024


@dataclass(frozen=True)
class SharedVariable:
    """
    One variable of a scope.

    Attributes:
        scope: One of :data:`SCOPES`.
        owner: The id of the team, project, environment or application the
            scope belongs to; 0 for the server.
        name: The variable's name.
        value: Its value, which may itself hold references.
        secret: The operator's mark, or None to let the one classifier decide.
        audience: ``both``, ``build`` or ``runtime``.
        created_at: When it was first set (UTC ISO 8601).
        created_by: Who first set it.
        updated_at: When it last changed.
        updated_by: Who last changed it.
    """

    scope: str
    owner: int
    name: str
    value: str
    secret: bool | None = None
    audience: str = BOTH
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""
    updated_by: str | None = None


def validate_scope(scope: str) -> str:
    """
    Check a scope.

    Args:
        scope: As typed.

    Returns:
        The scope.

    Raises:
        ValidationError: When it is not one of :data:`SCOPES`.
    """
    if scope not in SCOPES:
        raise ValidationError(
            f"Unknown variable scope: {scope!r}",
            details=f"Use one of: {', '.join(SCOPES)}.",
            field="scope",
        )
    return scope


def validate_audience(audience: str) -> str:
    """
    Check which part of a deployment a variable is for.

    Args:
        audience: As typed.

    Returns:
        The audience.

    Raises:
        ValidationError: When it is not ``both``, ``build`` or ``runtime``.
    """
    if audience not in AUDIENCES:
        raise ValidationError(
            f"Unknown audience: {audience!r}",
            details="Use 'both', 'build' (only the install and the build see it) or "
            "'runtime' (only the running application does).",
            field="audience",
        )
    return audience


def validate_shared_value(value: object, *, name: str) -> str:
    """
    Check a value as the environment of a unit would need it.

    The same rules as an application's own variables, because a shared
    variable ends up in one: no control character, a sane size.

    Args:
        value: As given.
        name: The variable's name, for the message.

    Returns:
        The value as text.

    Raises:
        EnvironmentValidationError: A control character, or too large.
    """
    text = validate_env_value(value, name=name)
    if len(text.encode("utf-8")) > MAX_VALUE_BYTES:
        raise EnvironmentValidationError(
            f"The value of {name} is larger than {MAX_VALUE_BYTES // 1024} KiB",
            details="Keep large files in a persistent path of the application, not in a variable.",
        )
    return text
