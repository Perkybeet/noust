# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags on databases and on systemd services (M-07), as applications carry theirs.

A tag says nothing to Noust beyond which resources share it, so one vocabulary serves all three
kinds: :mod:`noust.core.app_tags` owns the rules of what a tag is and this module reuses them.
What differs is what a tag is attached to. An application is a row of ``apps``; a database is
named by its engine and its own name (``postgresql/shop``) and a service by its unit
(``worker.service``), and neither has a row of its own that every place that lists them reads.
So they share the one table, ``resource_tags``, keyed by kind and name, and the code that
removes a database or a unit (``DatabaseService.drop`` and ``forget``,
``ServiceManager.delete_service``) calls :meth:`ResourceTags.forget`, so a name that is created
again later does not come back with the labels of the last one.

Who may see which resource is not decided here: a database is reached through the scoped store
and the route's resource rule, a service is the instance's. This module answers about the names
it is handed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from noust.core.app_tags import validate_tag, validate_tags
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: The two things besides an application that carry tags.
KIND_DATABASE = "database"
KIND_SERVICE = "service"
KINDS = (KIND_DATABASE, KIND_SERVICE)

#: What a resource may be called here: a unit name (``worker.service``) or an engine key and a
#: database (``postgresql@pg16/shop``). Judged with ``fullmatch``.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9@._:+-]{1,200}(?:/[A-Za-z0-9@._:+-]{1,200})?")


def database_key(engine: str, name: str) -> str:
    """
    Name a database the way its tags are keyed.

    Args:
        engine: The engine as the listing names it (``postgresql``, ``postgresql@pg16``).
        name: The database.

    Returns:
        ``engine/name``.
    """
    return f"{engine}/{name}"


def _kind(kind: str) -> str:
    """
    Check what kind of resource a tag is attached to.

    Args:
        kind: ``database`` or ``service``.

    Returns:
        The kind.

    Raises:
        ValidationError: The kind is unknown.
    """
    if kind not in KINDS:
        raise ValidationError(
            f"Tags are kept for {' and '.join(KINDS)}, not {kind!r}", field="kind"
        )
    return kind


def _check(kind: str, name: str) -> str:
    """
    Check what a tag is going to be attached to.

    Args:
        kind: ``database`` or ``service``.
        name: The resource's key.

    Returns:
        The key.

    Raises:
        ValidationError: The kind is unknown or the name is not one.
    """
    _kind(kind)
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise ValidationError(f"Not a {kind} name: {name!r}", field="name")
    return name


class ResourceTags:
    """
    The reader and the one writer of ``resource_tags``.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store this reads and writes."""
        return self._store or get_store()

    def of(self, kind: str, name: str) -> list[str]:
        """
        Args:
            kind: ``database`` or ``service``.
            name: The resource's key.

        Returns:
            Its tags, sorted; none for a resource that has none.

        Raises:
            ValidationError: The kind or the name is not valid.
        """
        _check(kind, name)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT tag FROM resource_tags WHERE kind = ? AND name = ? ORDER BY tag",
                (kind, name),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def set(self, kind: str, name: str, tags: Iterable[str]) -> list[str]:
        """
        Make a resource carry exactly these tags.

        Args:
            kind: ``database`` or ``service``.
            name: The resource's key. The caller has already found that it exists: a tag
                on a name nothing answers to would only wait for one.
            tags: The tags.

        Returns:
            The tags it carries now, sorted.

        Raises:
            ValidationError: A tag is not valid, there are too many, or the name is not one.
        """
        _check(kind, name)
        checked = validate_tags(tags)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM resource_tags WHERE kind = ? AND name = ?", (kind, name))
            for tag in checked:
                cursor.execute(
                    "INSERT INTO resource_tags (kind, name, tag, created_at) VALUES (?, ?, ?, ?)",
                    (kind, name, tag, stamp),
                )
        return sorted(checked)

    def add(self, kind: str, name: str, tags: Iterable[str]) -> list[str]:
        """
        Give a resource more tags, keeping the ones it has.

        Args:
            kind: ``database`` or ``service``.
            name: The resource's key.
            tags: The tags to add.

        Returns:
            The tags it carries now.

        Raises:
            ValidationError: As :meth:`set`.
        """
        return self.set(kind, name, [*self.of(kind, name), *validate_tags(tags)])

    def remove(self, kind: str, name: str, tags: Iterable[str]) -> list[str]:
        """
        Take tags off a resource.

        Args:
            kind: ``database`` or ``service``.
            name: The resource's key.
            tags: The tags to remove; one it does not carry is ignored.

        Returns:
            The tags it carries now.

        Raises:
            ValidationError: As :meth:`set`.
        """
        gone = set(validate_tags(tags))
        return self.set(kind, name, [tag for tag in self.of(kind, name) if tag not in gone])

    def by_resource(self, kind: str) -> dict[str, list[str]]:
        """
        Args:
            kind: ``database`` or ``service``.

        Returns:
            The key of every tagged resource of that kind with its tags. The caller keeps to
            the resources it may see.

        Raises:
            ValidationError: The kind is not one.
        """
        _kind(kind)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name, tag FROM resource_tags WHERE kind = ? ORDER BY name, tag", (kind,)
            )
            found: dict[str, list[str]] = {}
            for name, tag in cursor.fetchall():
                found.setdefault(str(name), []).append(str(tag))
        return found

    def with_tag(self, kind: str, tag: str) -> list[str]:
        """
        Args:
            kind: ``database`` or ``service``.
            tag: The tag.

        Returns:
            The keys of the resources of that kind that carry it, sorted.

        Raises:
            ValidationError: The kind or the tag is not valid.
        """
        _kind(kind)
        wanted = validate_tag(tag)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name FROM resource_tags WHERE kind = ? AND tag = ? ORDER BY name",
                (kind, wanted),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def forget(self, kind: str, name: str) -> int:
        """
        Drop what a resource carried, because the resource is gone.

        Args:
            kind: ``database`` or ``service``.
            name: The resource's key.

        Returns:
            How many tags were removed.

        Raises:
            ValidationError: The kind or the name is not valid.
        """
        _check(kind, name)
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM resource_tags WHERE kind = ? AND name = ?", (kind, name))
            return int(cursor.rowcount)
