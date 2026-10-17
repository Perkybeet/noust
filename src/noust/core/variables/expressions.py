# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``{{...}}`` references a variable's value can hold, and how they are read.

Four kinds, and only these four (anything else between double braces is the
operator's own text, left alone, since templates such as Handlebars' or Jinja's
are legitimate values):

- ``{{team.NAME}}``, ``{{project.NAME}}``, ``{{environment.NAME}}`` and
  ``{{server.NAME}}``: a shared variable of that scope, as seen from the
  application the value belongs to.
- ``{{app.URL}}`` (also ``DOMAIN``, ``BRANCH``, ``COMMIT``, ``RELEASE``,
  ``PORT`` and ``HOST``): what Noust knows of the deployment itself, the same
  values the application is given as ``NOUST_*``.
- ``{{secret:PROVIDER/PATH}}``: a value kept in a secret manager Noust was
  told about (:mod:`noust.core.variables.providers`), fetched at every deploy.
- ``{{peer:DOMAIN/URL}}`` (also ``DOMAIN/DOMAIN``): where another application answers, and,
  for a preview of a pull request, where *its* preview of the same pull request does. A
  monorepo's web application points at ``{{peer:api.example.com/URL}}``: in production that
  is ``https://api.example.com`` and in the preview of pull request 7 it is the API's
  preview of pull request 7, when the API has previews on the same repository.

A backslash before the braces (``\\{{project.X}}``) keeps them as text.

This module only reads the text. Resolving a reference is the resolver's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from noust.core.exceptions import ValidationError
from noust.core.variables.model import REFERENCE_SCOPES
from noust.validators.domain import is_valid_domain
from noust.validators.environment import ENV_NAME_PATTERN

#: What ``{{app.X}}`` can name, in the order the console lists them.
APP_FIELDS: tuple[str, ...] = (
    "URL",
    "DOMAIN",
    "BRANCH",
    "COMMIT",
    "RELEASE",
    "PORT",
    "HOST",
    "PR_NUMBER",
    "PREVIEW_OF",
)

#: The scope word of a reference to the deployment itself.
APP = "app"

#: The word of a reference to a secret manager.
SECRET = "secret"  # noqa: S105 - the word of a reference kind, not a credential

#: The word of a reference to another application.
PEER = "peer"

#: What ``{{peer:DOMAIN/X}}`` can name.
PEER_FIELDS: tuple[str, ...] = ("URL", "DOMAIN")

#: A secret manager's name, as an operator types it.
PROVIDER_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")

#: Longest path inside a provider; Vault paths and Doppler names are short.
MAX_PATH = 256

_BRACES = re.compile(
    r"(?P<escape>\\)?\{\{\s*(?P<kind>[a-z]+)(?P<sep>[.:])(?P<key>[^\s{}]+)\s*\}\}",
)


@dataclass(frozen=True)
class Reference:
    """
    One ``{{...}}`` in a value.

    Attributes:
        kind: A shared scope (``project``...), ``app`` or ``secret``.
        key: The variable's name, the app field, or ``PROVIDER/PATH``.
        text: The reference as written, braces included.
    """

    kind: str
    key: str
    text: str

    @property
    def provider(self) -> str:
        """For a secret reference, the provider's name."""
        return self.key.partition("/")[0]

    @property
    def path(self) -> str:
        """For a secret reference, the path inside the provider."""
        return self.key.partition("/")[2]

    @property
    def peer_domain(self) -> str:
        """For a peer reference, the other application's domain."""
        return self.key.partition("/")[0]

    @property
    def peer_field(self) -> str:
        """For a peer reference, what is asked of it: ``URL`` or ``DOMAIN``."""
        return self.key.partition("/")[2]


def _is_reference(kind: str, sep: str) -> bool:
    """
    Args:
        kind: The word before the separator.
        sep: ``.`` or ``:``.

    Returns:
        Whether that pair introduces one of the three kinds (an escaped one
        is still one: it is the shape the backslash protects).
    """
    if kind in (SECRET, PEER):
        return sep == ":"
    return sep == "." and (kind == APP or kind in REFERENCE_SCOPES)


def find_references(value: str) -> list[Reference]:
    """
    List the references a value holds, in order, repeats included.

    Args:
        value: A variable's value.

    Returns:
        Its references; none for a literal. A reference whose key is not valid
        for its kind is still listed (the resolver refuses it by name), so a
        typo is an error and not a silent literal.
    """
    found = []
    for match in _BRACES.finditer(value or ""):
        if match.group("escape") or not _is_reference(match.group("kind"), match.group("sep")):
            continue
        found.append(Reference(match.group("kind"), match.group("key"), match.group(0)))
    return found


def has_references(value: str) -> bool:
    """
    Args:
        value: A variable's value.

    Returns:
        Whether it holds at least one reference.
    """
    return bool(find_references(value))


def has_markup(value: str) -> bool:
    """
    Args:
        value: A variable's value.

    Returns:
        Whether it needs :func:`substitute` to become the value it stands for:
        it holds a reference, or an escape (``\\{{...}}``) to unescape.
    """
    return any(
        _is_reference(match.group("kind"), match.group("sep"))
        for match in _BRACES.finditer(value or "")
    )


def substitute(value: str, resolve: Callable[[Reference], str]) -> str:
    """
    Replace every reference in a value, and unescape the ones kept as text.

    Args:
        value: A variable's value.
        resolve: Gives the text one reference stands for.

    Returns:
        The value with each reference replaced. ``\\{{...}}`` loses its
        backslash and is otherwise left as it was.
    """

    def replace(match: re.Match[str]) -> str:
        if not _is_reference(match.group("kind"), match.group("sep")):
            return match.group(0)
        if match.group("escape"):
            return match.group(0)[1:]
        return resolve(Reference(match.group("kind"), match.group("key"), match.group(0)))

    return _BRACES.sub(replace, value or "")


def check_reference(reference: Reference) -> None:
    """
    Check that a reference is well formed for its kind.

    Args:
        reference: As found in a value.

    Raises:
        ValidationError: A shared reference whose name is not a variable name,
            an ``app`` field that does not exist, or a secret reference that is
            not ``PROVIDER/PATH``. The message names the reference.
    """
    if reference.kind == APP:
        if reference.key not in APP_FIELDS:
            raise ValidationError(
                f"{reference.text} names nothing Noust knows about the application",
                details=f"Use one of: {', '.join(f'{{{{app.{name}}}}}' for name in APP_FIELDS)}.",
            )
        return
    if reference.kind == PEER:
        domain, slash, field = reference.key.partition("/")
        valid, _reason = is_valid_domain(domain)
        if not valid or "." not in domain or not slash or field not in PEER_FIELDS:
            raise ValidationError(
                f"{reference.text} is not a reference to another application",
                details="Write {{peer:DOMAIN/URL}} or {{peer:DOMAIN/DOMAIN}}, with the domain of "
                "an application deployed here.",
            )
        return
    if reference.kind == SECRET:
        provider, slash, path = reference.key.partition("/")
        if not PROVIDER_NAME.fullmatch(provider) or not slash or not path or len(path) > MAX_PATH:
            raise ValidationError(
                f"{reference.text} is not a secret reference",
                details="Write {{secret:PROVIDER/PATH}}, with the name of a secret manager "
                "('noust secrets list') and the path of the secret inside it.",
            )
        return
    if not ENV_NAME_PATTERN.match(reference.key):
        raise ValidationError(
            f"{reference.text} does not name a variable",
            details="A variable's name is letters, digits and underscores, not starting with "
            f"a digit, as in {{{{{reference.kind}.DATABASE_URL}}}}.",
        )
