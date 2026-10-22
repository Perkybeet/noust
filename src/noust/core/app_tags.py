# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags: free labels on an application, to find it and to deploy a group of them.

``prod``, ``customer-a``, ``nightly``: a tag says nothing to Noust beyond which
applications share it. They are lower case, like the labels of a fleet's nodes
(:mod:`noust.fleet.labels`), so ``Prod`` and ``prod`` are never two groups, and
at most :data:`MAX_TAGS` to an application, since a handful is what grouping
needs and more is a sign they are being used as notes.

One table of ``schema_v14.ENVIRONMENTS_SQL``, ``app_tags``, owned by this module
and removed with the application. The tags are not columns of ``apps`` so a
redeploy's full-row write cannot put back what an operator changed meanwhile.
Who may see which applications is the scoped store's: this module answers about
the applications it is handed or the ones the ambient scope sees, never about
the whole server on behalf of a team.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: A tag: lower case letters and digits, with ``.``, ``_`` and ``-`` inside.
TAG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?")

#: Tags one application may carry.
MAX_TAGS = 16


def validate_tag(tag: str) -> str:
    """
    Check a tag.

    Args:
        tag: As typed; surrounding spaces and upper case are tolerated.

    Returns:
        The tag, lower-cased.

    Raises:
        ValidationError: Not 1 to 32 letters, digits, ``.``, ``_`` or ``-``,
            starting and ending with a letter or digit.
    """
    text = (tag or "").strip().lower() if isinstance(tag, str) else ""
    if not TAG_PATTERN.fullmatch(text):
        raise ValidationError(
            f"Not a tag: {tag!r}",
            details="A tag is 1 to 32 letters, digits, '.', '_' or '-', starting and ending "
            "with a letter or digit, such as prod or customer-a.",
            field="tags",
        )
    return text


def validate_tags(tags: Iterable[str]) -> list[str]:
    """
    Check a set of tags.

    Args:
        tags: As typed.

    Returns:
        The tags, lower-cased, without repeats, in the order given.

    Raises:
        ValidationError: A tag is not one, or there are more than :data:`MAX_TAGS`.
    """
    if isinstance(tags, str):
        raise ValidationError("Tags must be a list", field="tags")
    checked: list[str] = []
    for tag in tags:
        text = validate_tag(tag)
        if text not in checked:
            checked.append(text)
    if len(checked) > MAX_TAGS:
        raise ValidationError(
            f"An application carries at most {MAX_TAGS} tags, not {len(checked)}", field="tags"
        )
    return checked


class AppTags:
    """
    The reader and the one writer of ``app_tags``.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store this reads and writes."""
        return self._store or get_store()

    def of(self, domain: str) -> list[str]:
        """
        Args:
            domain: The application's domain.

        Returns:
            Its tags, sorted; none for an application that has none or does not exist.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT t.tag FROM app_tags t JOIN apps a ON a.id = t.app_id "
                "WHERE a.domain = ? ORDER BY t.tag",
                (domain,),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def set(self, domain: str, tags: Iterable[str]) -> list[str]:
        """
        Make an application carry exactly these tags.

        Args:
            domain: The application's domain.
            tags: The tags.

        Returns:
            The tags it carries now, sorted.

        Raises:
            ValidationError: A tag is not valid, there are too many, or there is
                no such application.
        """
        checked = validate_tags(tags)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
            row = cursor.fetchone()
            if row is None:
                raise ValidationError(f"No application named {domain}", field="domain")
            app_id = int(row[0])
            cursor.execute("DELETE FROM app_tags WHERE app_id = ?", (app_id,))
            for tag in checked:
                cursor.execute(
                    "INSERT INTO app_tags (app_id, tag, created_at) VALUES (?, ?, ?)",
                    (app_id, tag, stamp),
                )
        return sorted(checked)

    def add(self, domain: str, tags: Iterable[str]) -> list[str]:
        """
        Give an application more tags, keeping the ones it has.

        Args:
            domain: The application's domain.
            tags: The tags to add.

        Returns:
            The tags it carries now.

        Raises:
            ValidationError: As :meth:`set`.
        """
        return self.set(domain, [*self.of(domain), *validate_tags(tags)])

    def remove(self, domain: str, tags: Iterable[str]) -> list[str]:
        """
        Take tags off an application.

        Args:
            domain: The application's domain.
            tags: The tags to remove; one it does not carry is ignored.

        Returns:
            The tags it carries now.

        Raises:
            ValidationError: As :meth:`set`.
        """
        gone = set(validate_tags(tags))
        return self.set(domain, [tag for tag in self.of(domain) if tag not in gone])

    def by_application(self) -> dict[str, list[str]]:
        """
        Returns:
            Every tagged application's domain with its tags. The caller keeps
            to the applications its scope sees.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT a.domain, t.tag FROM app_tags t JOIN apps a ON a.id = t.app_id "
                "ORDER BY a.domain, t.tag"
            )
            found: dict[str, list[str]] = {}
            for domain, tag in cursor.fetchall():
                found.setdefault(str(domain), []).append(str(tag))
        return found

    def copy(self, source: str, target: str) -> list[str]:
        """
        Give an application the tags of another (a clone).

        Args:
            source: The application copied from.
            target: The application copied to; its own tags are replaced.

        Returns:
            The tags the target carries now.
        """
        return self.set(target, self.of(source))
