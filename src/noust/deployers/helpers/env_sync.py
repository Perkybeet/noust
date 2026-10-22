# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a real ``.env`` file into an application's variables, and showing them as one (checklist B-41).

``noust env set`` takes ``NAME=VALUE`` words, which is not what a developer has: a ``.env`` with
comments, ``export`` in front of a line, quoted values, a value of several lines. Coolify's
``app env sync`` reads that file and creates and updates what it holds without deleting the rest;
this is that, for every surface (the command line, the API, and the console that pastes a file):
:func:`plan_sync` says what a file would do to the variables an application has, and nothing is
written until a caller writes the plan.

The same module draws the other direction, :func:`render_view`: the stored ``.env`` as text, for a
viewer and a download, with the values a person must not see on a screen masked unless they
asked for them as someone who may.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.variables.predefined import RESERVED
from noust.deployers.helpers.env_manager import EnvManager
from noust.validators.environment import EnvironmentValidationError, validate_env_comment

__all__ = ["MAX_FILE_BYTES", "EnvSyncPlan", "parse_dotenv", "plan_sync", "render_view"]

#: The largest file accepted: a real ``.env`` is a few KiB.
MAX_FILE_BYTES = 256 * 1024

#: What a file may hold that the unit sets itself, and so is not the operator's to set.
_UNIT_OWNS = frozenset({"PORT", "NODE_ENV"})


@dataclass(frozen=True)
class EnvSyncPlan:
    """
    What a ``.env`` file would do to an application's variables.

    Attributes:
        values: The complete set of variables after the sync.
        comments: The comments the file puts above the variables it sets.
        created: Names the application did not have.
        updated: Names whose value changes.
        unchanged: Names the file sets to what they already are.
        removed: Names the application has and the file lacks, taken out only when asked for.
        skipped: Names in the file that Noust sets itself, with the reason.
        duplicates: Names the file sets more than once (the last one counts).
    """

    values: dict[str, str]
    comments: dict[str, str] = field(default_factory=dict)
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    duplicates: tuple[str, ...] = ()

    @property
    def changed(self) -> tuple[str, ...]:
        """The names whose value is new, changed or gone."""
        return tuple(sorted({*self.created, *self.updated, *self.removed}))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The names in each group (never a value), JSON-ready.
        """
        return {
            "created": list(self.created),
            "updated": list(self.updated),
            "unchanged": list(self.unchanged),
            "removed": list(self.removed),
            "skipped": dict(self.skipped),
            "duplicates": list(self.duplicates),
        }


def parse_dotenv(text: str) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    """
    Read the text of a ``.env`` file.

    Quotes, ``export``, comments and values of several lines are read the way the application's
    own ``.env`` is (:meth:`~noust.deployers.helpers.env_manager.EnvManager.parse_env_entries`).

    Args:
        text: The file's text.

    Returns:
        The variables, the comment above each that has one, and the names set more than once
        (the last assignment wins, as in a shell).

    Raises:
        ValidationError: The text is larger than a ``.env`` is, or holds a NUL byte.
    """
    if len(text.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
        raise ValidationError(
            f"The file is larger than {MAX_FILE_BYTES // 1024} KiB",
            details="A .env file is a few kilobytes: this is not one.",
            field="content",
        )
    if "\x00" in text:
        raise ValidationError(
            "The file holds a NUL byte", details="It is not a text file.", field="content"
        )
    values: dict[str, str] = {}
    comments: dict[str, str] = {}
    repeated: list[str] = []
    for entry in EnvManager.parse_env_entries(text):
        if entry.name in values and entry.name not in repeated:
            repeated.append(entry.name)
        values[entry.name] = entry.value
        if entry.comment:
            comments[entry.name] = entry.comment
        else:
            comments.pop(entry.name, None)
    return values, comments, tuple(repeated)


def plan_sync(
    current: Mapping[str, str],
    text: str,
    *,
    expressions: Mapping[str, str] | None = None,
    prune: bool = False,
) -> EnvSyncPlan:
    """
    Say what a ``.env`` file would do to the variables an application has.

    Every variable the file sets is created or updated; the others are kept, unless ``prune``.
    A value that is the text a variable was made from (a reference) counts as unchanged, as in the
    merge of the API: setting the same expression again is not a change. The names the unit
    owns (``PORT``, ``NODE_ENV``) and the ones Noust writes (``NOUST_URL``...) are skipped with the
    reason, because a developer's ``.env`` has a ``PORT`` and refusing the whole file for it
    would make the command useless.

    Args:
        current: The variables the application has, build-only ones included.
        text: The file's text.
        expressions: The text of the variables made of references.
        prune: Take out the variables the file does not hold.

    Returns:
        The plan.

    Raises:
        ValidationError: The text is not a ``.env`` (see :func:`parse_dotenv`).
    """
    parsed, comments, repeated = parse_dotenv(text)
    made_of = expressions or {}
    skipped: dict[str, str] = {}
    wanted: dict[str, str] = {}
    for name, value in parsed.items():
        if name in _UNIT_OWNS:
            skipped[name] = "the unit sets it: change the port by redeploying with --port"
        elif name in RESERVED:
            skipped[name] = "Noust writes it; refer to it with {{app.URL}} and the like"
        else:
            wanted[name] = value
    created = tuple(sorted(name for name in wanted if name not in current))
    updated = tuple(
        sorted(
            name
            for name, value in wanted.items()
            if name in current and current[name] != value and made_of.get(name) != value
        )
    )
    unchanged = tuple(sorted(name for name in wanted if name in current and name not in updated))
    removed = tuple(sorted(name for name in current if name not in wanted)) if prune else ()
    values = {name: value for name, value in current.items() if name not in removed}
    values.update({name: value for name, value in wanted.items() if name not in unchanged})
    # Only a comment a variable can carry: the rest of a file's prose is not a variable's.
    kept: dict[str, str] = {}
    for name, comment in comments.items():
        if name not in wanted:
            continue
        try:
            kept[name] = validate_env_comment(comment, name=name)
        except EnvironmentValidationError:
            continue
    return EnvSyncPlan(
        values=values,
        comments=kept,
        created=created,
        updated=updated,
        unchanged=unchanged,
        removed=removed,
        skipped=skipped,
        duplicates=repeated,
    )


def render_view(
    values: Mapping[str, str],
    comments: Mapping[str, str] | None = None,
    *,
    build_only: tuple[str, ...] = (),
) -> str:
    """
    Write an application's ``.env`` as the text a viewer shows and a download saves.

    Args:
        values: The variables as they are to appear (already masked when they must be).
        comments: The comment kept above each variable.
        build_only: The names only the build is given: they are in no file, so they are named in
            a comment at the end and never given a value.

    Returns:
        The file's text, sorted like the file itself.
    """
    text = EnvManager().render(values, comments)
    if values == {} and text == "\n":
        text = ""
    if build_only:
        text += (
            "\n# Given only to the install and the build, so not in the file: "
            + ", ".join(sorted(build_only))
            + "\n"
        )
    return text
