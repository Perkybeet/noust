# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which recipe an application was installed from, and at which version.

One row per application in ``app_recipes`` (schema v14), written when a recipe
install succeeds and again when a recipe update does. It is the only memory
the catalogue has of what is deployed: Coolify copies a template at creation
and forgets it ("Coolify does not merge future template changes into the saved
Service"), which is why it can never say a service is out of date. Here the
row is what :mod:`noust.recipes.updates` compares with the catalogue.

The table is keyed by the application and goes with it (``ON DELETE CASCADE``),
and it is not a column of ``apps``: a redeploy writes back the whole row it
read when it started, and a record in a table of its own cannot be put back to
what it was.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.recipes.model import KINDS, NAME_PATTERN, Recipe

#: What a legacy recipe, which carries no version, is recorded as.
UNVERSIONED = "unversioned"


@dataclass(frozen=True)
class InstalledRecipe:
    """
    The recipe an application came from.

    Attributes:
        domain: The application's domain.
        recipe: The recipe's name.
        kind: ``native`` or ``compose``.
        version: The version of the application the recipe pinned.
        revision: The recipe's own revision of that version.
        installed_at: When the recipe was installed (ISO 8601).
        updated_at: When its version last changed (ISO 8601).
    """

    domain: str
    recipe: str
    kind: str
    version: str
    revision: int
    installed_at: str
    updated_at: str


def _checked(recipe: Recipe) -> tuple[str, str, str, int]:
    """
    Check what is about to be recorded.

    Args:
        recipe: The recipe.

    Returns:
        Its name, kind, version and revision.

    Raises:
        ValidationError: Any of them is not something the table may hold.
    """
    version = recipe.version or UNVERSIONED
    if not NAME_PATTERN.match(recipe.name):
        raise ValidationError(f"{recipe.name!r} is not a recipe name", field="recipe")
    if recipe.kind not in KINDS:
        raise ValidationError(f"{recipe.kind!r} is not a kind of recipe", field="kind")
    if len(version) > 40 or not version.strip():
        raise ValidationError(f"{version!r} is not a version", field="version")
    if isinstance(recipe.revision, bool) or recipe.revision < 1:
        raise ValidationError(f"{recipe.revision!r} is not a revision", field="revision")
    return recipe.name, recipe.kind, version, recipe.revision


def record_install(domain: str, recipe: Recipe, *, store: NoustStore | None = None) -> bool:
    """
    Record that an application was installed from a recipe.

    Safe to call again: a second call for the same application replaces the
    first, which is what a redeploy of a failed first attempt is.

    Args:
        domain: The application's domain.
        recipe: The recipe, at the version that was deployed.
        store: The store. Defaults to the process-wide one.

    Returns:
        True when the application exists and the record was written.

    Raises:
        ValidationError: The recipe's name, kind, version or revision is not
            something the table may hold; nothing is written.
    """
    name, kind, version, revision = _checked(recipe)
    now = datetime.now().isoformat()
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return False
        cursor.execute(
            "INSERT INTO app_recipes (app_id, recipe, kind, version, revision, installed_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "recipe = excluded.recipe, kind = excluded.kind, version = excluded.version, "
            "revision = excluded.revision, installed_at = excluded.installed_at, "
            "updated_at = excluded.updated_at",
            (row[0], name, kind, version, revision, now, now),
        )
        return True


def record_update(domain: str, recipe: Recipe, *, store: NoustStore | None = None) -> bool:
    """
    Record that an installed application now runs another version of its recipe.

    Args:
        domain: The application's domain.
        recipe: The recipe, at the version that was deployed.
        store: The store. Defaults to the process-wide one.

    Returns:
        True when the application has a record and it was changed.

    Raises:
        ValidationError: As :func:`record_install`.
    """
    name, kind, version, revision = _checked(recipe)
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "UPDATE app_recipes SET recipe = ?, kind = ?, version = ?, revision = ?, "
            "updated_at = ? WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (name, kind, version, revision, datetime.now().isoformat(), domain),
        )
        return cursor.rowcount > 0


_SELECT = (
    "SELECT a.domain, r.recipe, r.kind, r.version, r.revision, r.installed_at, r.updated_at "
    "FROM app_recipes r JOIN apps a ON a.id = r.app_id"
)


def _row(row: sqlite3.Row) -> InstalledRecipe:
    """
    Build the record from a row of :data:`_SELECT`.

    Args:
        row: The row.

    Returns:
        The record.
    """
    return InstalledRecipe(
        domain=str(row[0]),
        recipe=str(row[1]),
        kind=str(row[2]),
        version=str(row[3]),
        revision=int(row[4]),
        installed_at=str(row[5]),
        updated_at=str(row[6]),
    )


def get_installed(domain: str, *, store: NoustStore | None = None) -> InstalledRecipe | None:
    """
    Say which recipe an application was installed from.

    Args:
        domain: The application's domain.
        store: The store. Defaults to the process-wide one.

    Returns:
        The record, or None when the application was not installed from a recipe.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} WHERE a.domain = ?", (domain,))
        row = cursor.fetchone()
    return _row(row) if row is not None else None


def list_installed(*, store: NoustStore | None = None) -> list[InstalledRecipe]:
    """
    List every application installed from a recipe.

    Args:
        store: The store. Defaults to the process-wide one.

    Returns:
        The records, by domain.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} ORDER BY a.domain")
        return [_row(row) for row in cursor.fetchall()]
