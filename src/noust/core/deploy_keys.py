# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store knows about deploy keys: which exist and which repository each reaches.

A deploy key is an SSH key pair a code host trusts for one repository (or, for
a machine user's key, a few). The private half is a secret file kept by
:mod:`noust.managers.source_keys`; this module holds the rest: the public key
and its fingerprint (safe to list), where the key came from, when it last
worked, the new public half while a rotation waits for the code host to accept
it, and the repositories it is assigned to.

A key is found by repository, never by application. ``git clone``, a fetch in
the cache, a webhook and a preview all know the repository's address and none
of them knows which application asked, so the assignment is keyed by
:func:`~noust.core.repositories.repository_identity`.

Every write goes through the store's transaction, so ``--dry-run`` rehearses
it and rolls it back like any other change of the store.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: Longest key name; it is also a directory name under the secrets.
MAX_NAME_LENGTH = 63

#: What a key's name may be: it names a directory, so no dot first and no slash.
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")

#: Where a key came from.
GENERATED = "generated"
IMPORTED = "imported"
ORIGINS = (GENERATED, IMPORTED)


def validate_name(name: str) -> str:
    """
    Check a deploy key's name.

    Args:
        name: As given.

    Returns:
        The name, stripped and lower case.

    Raises:
        ValidationError: When it is not a simple name.
    """
    text = (name or "").strip().lower()
    if not _NAME.fullmatch(text):
        raise ValidationError(
            f"{name!r} is not a deploy key name",
            details="Use lowercase letters, digits, '.', '_' and '-', starting with a letter "
            f"or digit, at most {MAX_NAME_LENGTH} characters.",
            field="name",
        )
    return text


@dataclass(frozen=True)
class PendingKey:
    """
    The new key a rotation waits to put in place.

    Attributes:
        key_type: ``ssh-ed25519``.
        fingerprint: ``SHA256:...``.
        public_key: The OpenSSH public key line.
        since: When the rotation started.
    """

    key_type: str
    fingerprint: str
    public_key: str
    since: str


@dataclass(frozen=True)
class DeployKey:
    """
    One deploy key, as it is recorded. Never the private half.

    Attributes:
        id: Row id.
        name: Its name; also the directory its secrets are in.
        origin: ``generated`` here, or ``imported``.
        key_type: ``ssh-ed25519``, ``ssh-rsa`` or ``ecdsa-sha2-nistp...``.
        fingerprint: ``SHA256:...``, as ``ssh-keygen -l`` prints it.
        public_key: The OpenSSH public key line to give the code host.
        created_at: When it was made or imported.
        created_by: Who did it.
        rotated_at: When a rotation last finished.
        last_used_at: When git last reached the repository with it.
        pending: The new key while a rotation waits; None otherwise.
        repositories: The repositories it is assigned to, sorted.
    """

    id: int
    name: str
    origin: str
    key_type: str
    fingerprint: str
    public_key: str
    created_at: str
    created_by: str | None = None
    rotated_at: str | None = None
    last_used_at: str | None = None
    pending: PendingKey | None = None
    repositories: tuple[str, ...] = field(default_factory=tuple)


def _now() -> str:
    """
    Returns:
        The time, in the store's own spelling: ISO 8601 without an offset.
    """
    return datetime.now().isoformat(timespec="seconds")


class DeployKeyStore:
    """
    Read and write the deploy key tables.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    @staticmethod
    def _build(row: sqlite3.Row, repositories: list[str]) -> DeployKey:
        """
        Build a key from its row.

        Args:
            row: A ``deploy_keys`` row.
            repositories: The repositories assigned to it.

        Returns:
            The key.
        """
        pending = None
        if row["pending_public_key"]:
            pending = PendingKey(
                key_type=row["pending_key_type"] or "",
                fingerprint=row["pending_fingerprint"] or "",
                public_key=row["pending_public_key"],
                since=row["pending_since"] or "",
            )
        return DeployKey(
            id=row["id"],
            name=row["name"],
            origin=row["origin"],
            key_type=row["key_type"],
            fingerprint=row["fingerprint"],
            public_key=row["public_key"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            rotated_at=row["rotated_at"],
            last_used_at=row["last_used_at"],
            pending=pending,
            repositories=tuple(sorted(repositories)),
        )

    def keys(self) -> list[DeployKey]:
        """
        List every deploy key.

        Returns:
            The keys, by name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM deploy_keys ORDER BY name")
            rows = cursor.fetchall()
            cursor.execute("SELECT repository, key_id FROM deploy_key_repositories")
            assigned: dict[int, list[str]] = {}
            for repository, key_id in cursor.fetchall():
                assigned.setdefault(key_id, []).append(repository)
        return [self._build(row, assigned.get(row["id"], [])) for row in rows]

    def get(self, name: str) -> DeployKey | None:
        """
        Find a key by name.

        Args:
            name: Its name.

        Returns:
            The key, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM deploy_keys WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "SELECT repository FROM deploy_key_repositories WHERE key_id = ?", (row["id"],)
            )
            repositories = [found[0] for found in cursor.fetchall()]
        return self._build(row, repositories)

    def for_repository(self, repository: str) -> DeployKey | None:
        """
        Find the key assigned to a repository.

        Args:
            repository: :func:`~noust.core.repositories.repository_identity`.

        Returns:
            The key, or None when the repository has none.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT k.* FROM deploy_keys k JOIN deploy_key_repositories r ON r.key_id = k.id "
                "WHERE r.repository = ?",
                (repository,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "SELECT repository FROM deploy_key_repositories WHERE key_id = ?", (row["id"],)
            )
            repositories = [found[0] for found in cursor.fetchall()]
        return self._build(row, repositories)

    def any_assigned(self) -> bool:
        """
        Tell whether any repository has a deploy key.

        Cheap, so every git command can ask before it works out a repository.

        Returns:
            True when at least one assignment exists.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT 1 FROM deploy_key_repositories LIMIT 1")
            return cursor.fetchone() is not None

    def create(
        self,
        *,
        name: str,
        origin: str,
        key_type: str,
        fingerprint: str,
        public_key: str,
        created_by: str | None,
    ) -> DeployKey:
        """
        Record a new key.

        Args:
            name: Its validated name.
            origin: ``generated`` or ``imported``.
            key_type: The key's algorithm name.
            fingerprint: ``SHA256:...``.
            public_key: The public key line.
            created_by: Who made it.

        Returns:
            The key.

        Raises:
            ValidationError: When the name is taken or the origin unknown.
        """
        if origin not in ORIGINS:
            raise ValidationError(f"Unknown deploy key origin: {origin!r}", field="origin")
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO deploy_keys (name, origin, key_type, fingerprint, public_key, "
                    "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, origin, key_type, fingerprint, public_key, _now(), created_by),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(
                f"A deploy key named {name} already exists",
                details="Choose another name with --name, or assign the one that exists.",
                field="name",
            ) from exc
        created = self.get(name)
        if created is None:  # a rehearsal rolls the insert back
            return DeployKey(
                id=0,
                name=name,
                origin=origin,
                key_type=key_type,
                fingerprint=fingerprint,
                public_key=public_key,
                created_at=_now(),
                created_by=created_by,
            )
        return created

    def assign(self, name: str, repository: str) -> None:
        """
        Make a key the one that reaches a repository.

        Args:
            name: The key.
            repository: The repository's identity.

        Raises:
            ValidationError: When the key does not exist or the repository
                already has another key.
        """
        key = self.get(name)
        if key is None:
            raise ValidationError(f"There is no deploy key named {name}", field="name")
        held = self.for_repository(repository)
        if held is not None and held.name != name:
            raise ValidationError(
                f"{repository} already uses the deploy key {held.name}",
                details="Remove that key from the application first, or use it: a repository "
                "has one deploy key.",
                field="name",
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO deploy_key_repositories (repository, key_id, assigned_at) "
                "VALUES (?, ?, ?)",
                (repository, key.id, _now()),
            )

    def unassign(self, repository: str) -> str | None:
        """
        Take a repository's key away from it.

        Args:
            repository: The repository's identity.

        Returns:
            The name of the key that reached it, or None when there was none.
        """
        held = self.for_repository(repository)
        if held is None:
            return None
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM deploy_key_repositories WHERE repository = ?", (repository,)
            )
        return held.name

    def delete(self, name: str) -> None:
        """
        Forget a key and its assignments.

        Args:
            name: The key.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM deploy_keys WHERE name = ?", (name,))

    def set_pending(self, name: str, *, key_type: str, fingerprint: str, public_key: str) -> None:
        """
        Record the key a rotation waits to put in place.

        Args:
            name: The key being rotated.
            key_type: The new key's algorithm name.
            fingerprint: The new key's fingerprint.
            public_key: The new public key line.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE deploy_keys SET pending_key_type = ?, pending_fingerprint = ?, "
                "pending_public_key = ?, pending_since = ? WHERE name = ?",
                (key_type, fingerprint, public_key, _now(), name),
            )

    def clear_pending(self, name: str) -> None:
        """
        Forget a rotation that was cancelled.

        Args:
            name: The key.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE deploy_keys SET pending_key_type = NULL, pending_fingerprint = NULL, "
                "pending_public_key = NULL, pending_since = NULL WHERE name = ?",
                (name,),
            )

    def promote_pending(self, name: str) -> None:
        """
        Make the waiting key the key: the code host accepted it.

        Args:
            name: The key.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE deploy_keys SET key_type = pending_key_type, "
                "fingerprint = pending_fingerprint, public_key = pending_public_key, "
                "rotated_at = ?, pending_key_type = NULL, pending_fingerprint = NULL, "
                "pending_public_key = NULL, pending_since = NULL "
                "WHERE name = ? AND pending_public_key IS NOT NULL",
                (_now(), name),
            )

    def touch_used(self, name: str) -> None:
        """
        Record that git reached a repository with the key.

        Args:
            name: The key.
        """
        with self.store._transaction() as cursor:
            cursor.execute("UPDATE deploy_keys SET last_used_at = ? WHERE name = ?", (_now(), name))
