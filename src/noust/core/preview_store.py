# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What 3.4 adds to previews without touching the 2.2 tables.

Two small tables of ``schema_v14.ENVIRONMENTS_SQL``, keyed by domain like the
tables they sit beside (the managers that own previews remove their rows with
the preview or the application, and a row that outlives one by a crash is
harmless and visible):

- ``preview_options``, one row per application that allows previews: which of
  its variables a preview is given (``inherit_env``: ``all`` as 2.2 did, minus
  ``exclude_env``, or ``allowlist``, only ``allow_env``) and whether a preview
  shares its application's databases (``data_mode``: ``shared`` or ``empty``).
  An application whose previews were turned on before 3.4 has no
  row and reads as the 2.2 behaviour; one turned on from 3.4 gets the safe one.
- ``preview_origins``, one row per preview: what it was made from (a pull
  request, or a branch or a tag an operator named), which variables the policy
  gave it (so a later change of the policy takes back exactly those) and which
  were made for it (its own database's connection settings).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: Every variable of the application, less ``exclude_env`` (the 2.2 behaviour).
INHERIT_ALL = "all"
#: Only the variables ``allow_env`` names.
INHERIT_ALLOWLIST = "allowlist"
INHERIT_MODES: tuple[str, ...] = (INHERIT_ALL, INHERIT_ALLOWLIST)

#: A preview uses its application's databases (the 2.2 behaviour).
DATA_SHARED = "shared"
#: A preview gets a database of its own, empty.
DATA_EMPTY = "empty"
DATA_MODES: tuple[str, ...] = (DATA_SHARED, DATA_EMPTY)

#: A preview made from a pull request, a branch or a tag.
KIND_PR = "pr"
KIND_BRANCH = "branch"
KIND_TAG = "tag"
KINDS: tuple[str, ...] = (KIND_PR, KIND_BRANCH, KIND_TAG)

#: Previews made by hand from a branch or a tag are numbered from here, above
#: any pull request number, so one number is never two previews.
MANUAL_BASE = 1_000_000


@dataclass(frozen=True)
class PreviewOptions:
    """
    How the previews of one application are made.

    Attributes:
        app_domain: The application previewed.
        inherit_env: ``all`` or ``allowlist``.
        allow_env: The variables an ``allowlist`` preview copies.
        data_mode: ``shared`` or ``empty``.
    """

    app_domain: str
    inherit_env: str = INHERIT_ALL
    allow_env: tuple[str, ...] = ()
    data_mode: str = DATA_SHARED


@dataclass(frozen=True)
class PreviewOrigin:
    """
    What one preview was made from, and what it was given.

    Attributes:
        domain: The preview's domain.
        kind: ``pr``, ``branch`` or ``tag``.
        ref: The branch or the tag; None for a pull request.
        created_by: Who made it by hand; None for one a webhook made.
        given_env: The variables the policy gave it.
        owned_env: The variables made for it alone (its database's).
        databases: ``engine/name`` of the databases made for it.
    """

    domain: str
    kind: str = KIND_PR
    ref: str | None = None
    created_by: str | None = None
    given_env: tuple[str, ...] = ()
    owned_env: tuple[str, ...] = ()
    databases: tuple[str, ...] = field(default_factory=tuple)


def validate_inherit(mode: str) -> str:
    """
    Args:
        mode: As typed.

    Returns:
        The mode.

    Raises:
        ValidationError: Neither ``all`` nor ``allowlist``.
    """
    if mode not in INHERIT_MODES:
        raise ValidationError(
            f"Unknown way to give previews variables: {mode!r}",
            details="Use 'allowlist' (only the variables you name, the safe default) or "
            "'all' (every variable of the application, less the ones excluded).",
            field="inherit_env",
        )
    return mode


def validate_data_mode(mode: str) -> str:
    """
    Args:
        mode: As typed.

    Returns:
        The mode.

    Raises:
        ValidationError: Not one of :data:`DATA_MODES`.
    """
    if mode not in DATA_MODES:
        raise ValidationError(
            f"Unknown way to give previews data: {mode!r}",
            details="Use 'empty' (a database of its own, empty) or 'shared' (the application's "
            "own databases).",
            field="data_mode",
        )
    return mode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _names(raw: object) -> tuple[str, ...]:
    try:
        value = json.loads(str(raw)) if raw else []
    except ValueError:
        return ()
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


class PreviewStore:
    """
    The reader and the one writer of ``preview_options`` and ``preview_origins``.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store this reads and writes."""
        return self._store or get_store()

    # Options ---------------------------------------------------------------

    def options(self, app_domain: str) -> PreviewOptions:
        """
        Args:
            app_domain: The application previewed.

        Returns:
            How its previews are made; the 2.2 behaviour when it has no row.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT inherit_env, allow_env, data_mode FROM preview_options "
                "WHERE app_domain = ?",
                (app_domain,),
            )
            row = cursor.fetchone()
        if row is None:
            return PreviewOptions(app_domain)
        return PreviewOptions(app_domain, str(row[0]), _names(row[1]), str(row[2]))

    def has_options(self, app_domain: str) -> bool:
        """
        Args:
            app_domain: The application previewed.

        Returns:
            Whether it was given options (previews turned on from 3.4).
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT 1 FROM preview_options WHERE app_domain = ?", (app_domain,))
            return cursor.fetchone() is not None

    def save_options(self, options: PreviewOptions) -> PreviewOptions:
        """
        Args:
            options: The options, already checked.

        Returns:
            The options as stored.

        Raises:
            ValidationError: A mode that does not exist.
        """
        validate_inherit(options.inherit_env)
        validate_data_mode(options.data_mode)
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO preview_options (app_domain, inherit_env, allow_env, data_mode, "
                "updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(app_domain) DO UPDATE SET "
                "inherit_env = excluded.inherit_env, allow_env = excluded.allow_env, "
                "data_mode = excluded.data_mode, updated_at = excluded.updated_at",
                (
                    options.app_domain,
                    options.inherit_env,
                    json.dumps(list(options.allow_env)),
                    options.data_mode,
                    _now(),
                ),
            )
        return self.options(options.app_domain)

    def forget_options(self, app_domain: str) -> None:
        """
        Args:
            app_domain: The application whose previews are turned off.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM preview_options WHERE app_domain = ?", (app_domain,))

    # Origins ---------------------------------------------------------------

    def origin(self, domain: str) -> PreviewOrigin:
        """
        Args:
            domain: The preview's domain.

        Returns:
            What it was made from; a pull request when nothing says otherwise
            (every preview before 3.4 was).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT kind, ref, created_by, given_env, owned_env, databases "
                "FROM preview_origins WHERE domain = ?",
                (domain,),
            )
            row = cursor.fetchone()
        if row is None:
            return PreviewOrigin(domain)
        return PreviewOrigin(
            domain,
            str(row[0]),
            None if row[1] is None else str(row[1]),
            None if row[2] is None else str(row[2]),
            _names(row[3]),
            _names(row[4]),
            _names(row[5]),
        )

    def save_origin(self, origin: PreviewOrigin) -> None:
        """
        Record, or change, what a preview was made from and given.

        Args:
            origin: The origin.

        Raises:
            ValidationError: A kind that does not exist.
        """
        if origin.kind not in KINDS:
            raise ValidationError(f"Unknown kind of preview: {origin.kind!r}", field="kind")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO preview_origins (domain, kind, ref, created_by, created_at, "
                "given_env, owned_env, databases) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET kind = excluded.kind, ref = excluded.ref, "
                "given_env = excluded.given_env, owned_env = excluded.owned_env, "
                "databases = excluded.databases",
                (
                    origin.domain,
                    origin.kind,
                    origin.ref,
                    origin.created_by,
                    _now(),
                    json.dumps(list(origin.given_env)),
                    json.dumps(list(origin.owned_env)),
                    json.dumps(list(origin.databases)),
                ),
            )

    def forget_origin(self, domain: str) -> None:
        """
        Args:
            domain: The preview that was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM preview_origins WHERE domain = ?", (domain,))

    def next_manual_number(self, parent_domain: str) -> int:
        """
        Args:
            parent_domain: The application previewed.

        Returns:
            The number the next preview made by hand gets: above every number
            a pull request has, and above every one taken so far.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT MAX(number) FROM previews WHERE parent_domain = ? AND number >= ?",
                (parent_domain, MANUAL_BASE),
            )
            top = cursor.fetchone()[0]
        return (int(top) + 1) if top is not None else MANUAL_BASE + 1
