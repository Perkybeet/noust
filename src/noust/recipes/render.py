# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rendering a recipe's values: variables, notes and template files.

A value is a Jinja expression over a small, fixed context, in a sandbox with
undefined names refused:

- ``{{ secret(48) }}``: a new random secret of that many letters and digits,
  different at every call (a salt per variable).
- ``{{ domain }}``, ``{{ url }}`` (``https://`` or ``http://`` plus the
  domain), ``{{ https }}`` (a boolean), ``{{ app_name }}``, ``{{ app_path }}``
  and ``{{ port }}``.
- ``{{ database.url }}``, ``.host``, ``.port``, ``.name``, ``.user``,
  ``.password`` and ``.engine``: the credentials provisioned for it.

Recipes are package data, but the sandbox still applies: a template that
reached for an attribute of a Python object would be a template bug, and it
should fail rather than work.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined, UndefinedError
from jinja2 import TemplateError as JinjaTemplateError
from jinja2.sandbox import SandboxedEnvironment

from noust.core.exceptions import ValidationError

#: Longest secret a recipe may ask for.
MAX_SECRET_LENGTH = 128

_ALPHABET = string.ascii_letters + string.digits


def secret(length: int = 48) -> str:
    """
    Generate a secret of letters and digits.

    Letters and digits only: the value goes into ``.env`` files, URLs and
    PHP-FPM pool files, and none of them has to escape anything then.

    Args:
        length: How many characters, 16 to :data:`MAX_SECRET_LENGTH`.

    Returns:
        The secret.

    Raises:
        ValidationError: When the length is out of range.
    """
    if not isinstance(length, int) or not 16 <= length <= MAX_SECRET_LENGTH:
        raise ValidationError(
            f"A recipe asked for a secret of length {length!r}",
            details=f"Secrets are 16 to {MAX_SECRET_LENGTH} characters long.",
        )
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


@lru_cache(maxsize=1)
def template_environment() -> SandboxedEnvironment:
    """
    Build the environment every recipe value renders in.

    Returns:
        A sandboxed environment that refuses undefined names.
    """
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        # Values end up in .env files and configuration, never in markup;
        # autoescaping would corrupt a password with an & in it.
        autoescape=False,
        keep_trailing_newline=True,
    )


def render_value(template: str, context: Mapping[str, Any], *, what: str) -> str:
    """
    Render one value.

    Args:
        template: The recipe's template.
        context: The names it may use (see the module).
        what: What is rendered, for the message.

    Returns:
        The value.

    Raises:
        ValidationError: When it uses a name the context does not have, or
            does not render.
    """
    try:
        return template_environment().from_string(template).render(**context, secret=secret)
    except UndefinedError as exc:
        raise ValidationError(
            f"{what} uses something this deployment does not have: {exc.message}",
            details="A recipe can use secret(n), domain, url, https, app_name, app_path, "
            "port and, when it asks for a database, database.*.",
        ) from exc
    except JinjaTemplateError as exc:
        raise ValidationError(f"{what} did not render", details=str(exc)) from exc
