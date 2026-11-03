# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ledger of what Noust installed on the server (``installed_software``, schema v17).

Nothing recorded "Noust installed this" before. Every installer skipped what was already on the path,
so ``installed_now`` existed only in a job's result, and the audit trail, which can be purged, was
the one other trace. The ledger is a row per component of the registry
(:mod:`noust.managers.server.components`) with what the install brought, the repository source and
key files Noust added for it, when, as whom and through which installer. It is what lets a removal
take back out the repository and the key along with the package, and what tells software Noust put
there from software that was there already.

It starts empty on a server that exists. What was installed before is recognised by the marks Noust
leaves (a ``noust-*.list`` file, a binary in ``/opt/noust``), never by inventing rows for it.

Every value is judged here before it is written (identifiers, package names through
:func:`~noust.validators.package.validate_package`, normal absolute paths, one-line actors): the
rows are read back by a removal that deletes the files they name, so a row is input like any other.
"""

from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso
from noust.validators.package import validate_package

__all__ = ["InstalledSoftware", "SoftwareLedger"]

#: What a component's id and the name of an installer are: lower-case words, as the registry writes them.
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,39}")


@dataclass(frozen=True)
class InstalledSoftware:
    """
    One component Noust installed.

    Attributes:
        component: Its id in the registry (``nginx``, ``cloudflared``).
        packages: The packages its install brought.
        repository_files: The repository source and key files Noust added for it, absolute paths.
        installed_at: When, ISO 8601 UTC.
        actor: As whom (an audit actor label), when it is known.
        via: The installer that did it (``package``, ``cloudflared``, ``database_engine``...).
    """

    component: str
    packages: tuple[str, ...]
    repository_files: tuple[str, ...]
    installed_at: str
    actor: str | None
    via: str


def _identifier(value: object, what: str) -> str:
    """
    Judge an id.

    Args:
        value: What was given.
        what: Its name for the message (``component``, ``via``).

    Returns:
        The id.

    Raises:
        ValidationError: It is not lower-case words, digits and ``_``, starting with a letter.
    """
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValidationError(
            f"{value!r} is not a valid {what}",
            details=f"A {what} is lower-case letters, digits and _ only, starting with a letter, "
            "at most 40 characters.",
        )
    return value


def _path(value: object) -> str:
    """
    Judge a repository file.

    Args:
        value: What was given.

    Returns:
        The path.

    Raises:
        ValidationError: It is not a normal absolute path: a relative one, one with ``..`` or
            ``.`` in it, a doubled slash, a trailing one, a newline or a NUL.
    """
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or any(char in value for char in "\x00\n\r")
        or posixpath.normpath(value) != value
    ):
        raise ValidationError(
            f"{value!r} is not a repository file",
            details="A repository file is a normal absolute path, with no '..', no '.' and no "
            "line break.",
        )
    return value


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    """
    Args:
        items: Values that may repeat.

    Returns:
        Each once, in the order first seen.
    """
    return tuple(dict.fromkeys(items))


class SoftwareLedger:
    """
    Read and write the ledger.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def record(
        self,
        component: str,
        *,
        packages: Sequence[str] = (),
        repository_files: Sequence[str] = (),
        via: str,
        actor: str | None = None,
    ) -> InstalledSoftware:
        """
        Write down that Noust installed a component, or what a later install added to it.

        The date, the actor and the way are those of the first install: a second record adds to
        the packages and the repository files, so a removal knows all of what Noust put there.

        Args:
            component: The component's id in the registry.
            packages: The packages the install brought.
            repository_files: The repository source and key files Noust added.
            via: The installer that did it.
            actor: As whom, an audit actor label.

        Returns:
            What the ledger holds for the component now.

        Raises:
            ValidationError: A value is not one the ledger keeps (see the module's rules).
        """
        name = _identifier(component, "component")
        way = _identifier(via, "via")
        brought = tuple(validate_package(package) for package in packages)
        files = tuple(_path(path) for path in repository_files)
        if actor is not None and (not isinstance(actor, str) or len(actor.splitlines()) > 1):
            raise ValidationError(
                "An actor is one line of text",
                details="The audit trail's label of whoever acted, such as cli:root.",
            )
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM installed_software WHERE component = ?", (name,))
            existing = cursor.fetchone()
            if existing is None:
                kept = InstalledSoftware(
                    name,
                    _unique(brought),
                    _unique(files),
                    utc_now_iso("seconds"),
                    actor or None,
                    way,
                )
                cursor.execute(
                    """
                    INSERT INTO installed_software
                        (component, packages, repository_files, installed_at, actor, via)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kept.component,
                        json.dumps(list(kept.packages)),
                        json.dumps(list(kept.repository_files)),
                        kept.installed_at,
                        kept.actor,
                        kept.via,
                    ),
                )
                return kept
            before = _installed(existing)
            kept = replace(
                before,
                packages=_unique((*before.packages, *brought)),
                repository_files=_unique((*before.repository_files, *files)),
            )
            cursor.execute(
                "UPDATE installed_software SET packages = ?, repository_files = ? "
                "WHERE component = ?",
                (
                    json.dumps(list(kept.packages)),
                    json.dumps(list(kept.repository_files)),
                    name,
                ),
            )
            return kept

    def get(self, component: str) -> InstalledSoftware | None:
        """
        Args:
            component: A component's id.

        Returns:
            What Noust installed for it, or None when the ledger has nothing.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM installed_software WHERE component = ?", (component,))
            .fetchone()
        )
        return None if row is None else _installed(row)

    def all(self) -> dict[str, InstalledSoftware]:
        """
        Returns:
            Every component the ledger holds, by id.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM installed_software ORDER BY component")
            .fetchall()
        )
        return {row["component"]: _installed(row) for row in rows}

    def forget(self, component: str) -> bool:
        """
        Drop a component that was removed.

        Args:
            component: A component's id.

        Returns:
            True when the ledger held it.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM installed_software WHERE component = ?", (component,))
            return cursor.rowcount > 0


def _installed(row: sqlite3.Row) -> InstalledSoftware:
    """
    Args:
        row: An ``installed_software`` row.

    Returns:
        The record it holds.
    """
    return InstalledSoftware(
        component=row["component"],
        packages=tuple(json.loads(row["packages"])),
        repository_files=tuple(json.loads(row["repository_files"])),
        installed_at=row["installed_at"],
        actor=row["actor"],
        via=row["via"],
    )
