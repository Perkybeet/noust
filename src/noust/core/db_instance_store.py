# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which database instances Noust runs itself, in containers.

The host's PostgreSQL, MySQL, Redis and MongoDB stay the engines of the server.
An **instance** is another one beside them: another major version, or an engine
the host does not provide, run from an official image Noust pinned by digest
(decision D4 of the 3.4 design). A row of ``db_instances`` (schema v14,
:mod:`noust.core.schema_v14`) says which instance Noust made, from which image,
on which loopback port and with how much memory. It is the authority on what is
Noust's: a container that merely wears the label is not an instance until a row
names it.

The row is written *before* the first thing is created, with ``state``
``creating``, and says ``upgrading`` or ``removing`` before the first thing is
changed, so an interrupted action is finished or undone from what the row
records instead of from a guess. Every write goes through the store's
transaction, so ``--dry-run`` rehearses it and rolls it back like any other
change of the store.

Every column has its own setter and every setter validates before it writes
(through :mod:`noust.validators.db_instance` and
:mod:`noust.validators.container`), so a value that could not be an argument of
``docker`` or ``systemctl`` never reaches the row. **No secret is in it**: the
superuser's password is in the instance's own 0600 env file, which the
container reads. What a row does not hold, the unit, the volume, the container
and the directory, is derived from its name in
:mod:`noust.managers.database.instance_layout`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import DatabaseExistsError, ValidationError
from noust.validators import container as image_rules
from noust.validators import db_instance as rules

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: What a row goes through: being made, running, being upgraded, being removed.
CREATING = "creating"
READY = "ready"
UPGRADING = "upgrading"
REMOVING = "removing"
STATES: tuple[str, ...] = (CREATING, READY, UPGRADING, REMOVING)

#: Whether an instance serves TLS: not at all, to a client that asks for it, or
#: only (a plaintext connection is refused).
TLS_OFF = "off"
TLS_OPTIONAL = "optional"
TLS_REQUIRED = "required"
TLS_MODES: tuple[str, ...] = (TLS_OFF, TLS_OPTIONAL, TLS_REQUIRED)

#: A flavour's name, as the catalog spells it.
_FLAVOUR = re.compile(r"[a-z][a-z0-9]{1,23}")

#: The version of an image's tag: numbers and dots, as the catalog offers them.
_VERSION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}")

#: Memory no instance row can exceed: a sanity bound, not what a server can give
#: (the manager checks that against the machine).
_MEMORY_CEILING_MB = 4 * 1024 * 1024


@dataclass(frozen=True)
class InstanceRecord:
    """
    One instance Noust runs.

    Attributes:
        id: Row id.
        name: The operator's name for it (``pg16``); also the part of its key
            after the ``@``.
        flavour: What it runs: a key of the catalog (``postgresql``, ``mysql``,
            ``mariadb``, ``redis``, ``valkey``, ``mongodb``).
        version: The tag Noust offered, a major such as ``16`` or ``8.4``.
        image: The image's repository (``postgres``, ``valkey/valkey``).
        digest: The digest of the image the container runs.
        previous_digest: What ran before the last minor upgrade, or None.
        host_port: The port on 127.0.0.1 it is published on.
        memory_limit_mb: Its memory limit, in megabytes.
        state: ``creating``, ``ready``, ``upgrading`` or ``removing``.
        created_at: When it was made, ISO 8601 in UTC.
        created_by: Who made it.
        updated_at: When the row last changed.
        upgraded_at: When its image last changed, or None.
        tls_mode: ``off``, ``optional`` or ``required``: whether the engine
            serves TLS with a certificate of Noust's authority.
    """

    id: int
    name: str
    flavour: str
    version: str
    image: str
    digest: str
    previous_digest: str | None
    host_port: int
    memory_limit_mb: int
    state: str
    created_at: str
    created_by: str | None
    updated_at: str
    upgraded_at: str | None = None
    tls_mode: str = TLS_OFF

    @property
    def reference(self) -> str:
        """The image reference a container is started from: tag and digest."""
        return f"{self.image}:{self.version}@{self.digest}"

    @property
    def ready(self) -> bool:
        """Whether the instance was made completely and is not being changed."""
        return self.state == READY

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the row for the API and the CLI.

        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "flavour": self.flavour,
            "version": self.version,
            "image": self.image,
            "digest": self.digest,
            "previous_digest": self.previous_digest,
            "host_port": self.host_port,
            "memory_limit_mb": self.memory_limit_mb,
            "state": self.state,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "upgraded_at": self.upgraded_at,
            "tls_mode": self.tls_mode,
        }


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_flavour(flavour: object) -> str:
    """
    Accept the name of a flavour.

    Args:
        flavour: The candidate.

    Returns:
        The flavour.

    Raises:
        ValidationError: It is not lower-case letters and digits.
    """
    if not isinstance(flavour, str) or _FLAVOUR.fullmatch(flavour) is None:
        raise ValidationError(
            f"{flavour!r} is not a database flavour",
            details="A flavour is a name such as postgresql, mysql, mariadb, redis, valkey "
            "or mongodb.",
            field="engine",
        )
    return flavour


def check_repository(image: object) -> str:
    """
    Accept the repository an instance's image is pulled from.

    The tag and the digest are columns of their own, so a reference that
    carries them would be two answers to one question.

    Args:
        image: The candidate: ``postgres``, ``valkey/valkey``.

    Returns:
        The repository.

    Raises:
        ValidationError: It is not an image repository, or it names a tag or
            a digest.
    """
    if not isinstance(image, str):
        raise ValidationError(
            f"{image!r} is not an image repository",
            details="Give the repository alone, such as postgres or valkey/valkey.",
            field="image",
        )
    image_rules.check_image_reference(image)
    if "@" in image or ":" in image.rsplit("/", 1)[-1]:
        raise ValidationError(
            f"{image!r} names a tag or a digest",
            details="Give the repository alone, such as postgres or valkey/valkey: the tag "
            "and the digest are kept apart.",
            field="image",
        )
    return image


def check_version(version: object) -> str:
    """
    Accept the version of an instance's image tag.

    Args:
        version: The candidate.

    Returns:
        The version.

    Raises:
        ValidationError: It is not numbers and dots.
    """
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValidationError(
            f"{version!r} is not a database version",
            details="A version is numbers and dots, such as 17 or 8.4.",
            field="version",
        )
    return version


def _build(row: sqlite3.Row) -> InstanceRecord:
    """
    Build an instance from its row.

    Args:
        row: A ``db_instances`` row.

    Returns:
        The instance.
    """
    return InstanceRecord(
        id=row["id"],
        name=row["name"],
        flavour=row["flavour"],
        version=row["version"],
        image=row["image"],
        digest=row["digest"],
        previous_digest=row["previous_digest"],
        host_port=row["host_port"],
        memory_limit_mb=row["memory_limit_mb"],
        state=row["state"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
        upgraded_at=row["upgraded_at"],
        tls_mode=row["tls_mode"] or TLS_OFF,
    )


class DbInstanceStore:
    """The instances Noust runs, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def all(self) -> list[InstanceRecord]:
        """
        List every instance.

        Returns:
            The rows, by name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_instances ORDER BY name")
            return [_build(row) for row in cursor.fetchall()]

    def get(self, name: str) -> InstanceRecord | None:
        """
        Find an instance by name.

        Args:
            name: Its name.

        Returns:
            The row, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_instances WHERE name = ?", (name,))
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def on_port(self, port: int) -> InstanceRecord | None:
        """
        Find the instance published on a port.

        Args:
            port: The port on 127.0.0.1.

        Returns:
            The row, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_instances WHERE host_port = ?", (port,))
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def create(
        self,
        *,
        name: str,
        flavour: str,
        version: str,
        image: str,
        digest: str,
        host_port: int,
        memory_limit_mb: int,
        created_by: str | None,
    ) -> InstanceRecord:
        """
        Record that an instance is being made, before anything is created.

        Args:
            name: Its name.
            flavour: What it runs.
            version: The tag Noust offered.
            image: The repository.
            digest: The digest it is pinned to.
            host_port: The port on 127.0.0.1.
            memory_limit_mb: Its memory limit.
            created_by: Who is making it.

        Returns:
            The row, ``creating``.

        Raises:
            ValidationError: A value is not valid (``field`` names it);
                nothing is written.
            DatabaseExistsError: The name or the port is taken by another.
        """
        rules.check_instance_name(name)
        check_flavour(flavour)
        check_version(version)
        check_repository(image)
        image_rules.check_digest(digest)
        rules.check_host_port(host_port)
        rules.check_memory(memory_limit_mb, minimum=1, maximum=_MEMORY_CEILING_MB)
        now = _now()
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO db_instances (name, flavour, version, image, digest, host_port, "
                    "memory_limit_mb, state, created_at, created_by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        flavour,
                        version,
                        image,
                        digest,
                        host_port,
                        memory_limit_mb,
                        CREATING,
                        now,
                        created_by,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseExistsError(
                f"An instance called {name}, or one on port {host_port}, exists already",
                details="See them with: noust db instance list",
            ) from exc
        created = self.get(name)
        if created is None:  # a rehearsal rolls the insert back
            return InstanceRecord(
                id=0,
                name=name,
                flavour=flavour,
                version=version,
                image=image,
                digest=digest,
                previous_digest=None,
                host_port=host_port,
                memory_limit_mb=memory_limit_mb,
                state=CREATING,
                created_at=now,
                created_by=created_by,
                updated_at=now,
            )
        return created

    def _set(self, name: str, assignments: dict[str, Any]) -> bool:
        """
        Write columns of one instance's row.

        Args:
            name: The instance's name.
            assignments: Column names (code, never input) to already validated values.

        Returns:
            True when the instance exists and the columns were written.
        """
        columns = ", ".join(f"{column} = ?" for column in assignments)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"UPDATE db_instances SET {columns}, updated_at = ? WHERE name = ?",  # noqa: S608
                (*assignments.values(), _now(), name),
            )
            return cursor.rowcount > 0

    def set_state(self, name: str, state: str) -> bool:
        """
        Say how far creating, upgrading or removing got.

        Args:
            name: The instance's name.
            state: One of :data:`STATES`.

        Returns:
            True when the instance exists and the state was written.

        Raises:
            ValidationError: Not a state.
        """
        if state not in STATES:
            raise ValidationError(
                f"{state!r} is not a state of a database instance",
                details=f"The states are: {', '.join(STATES)}.",
                field="state",
            )
        return self._set(name, {"state": state})

    def set_tls_mode(self, name: str, mode: str) -> bool:
        """
        Say whether an instance serves TLS.

        Args:
            name: The instance's name.
            mode: One of :data:`TLS_MODES`.

        Returns:
            True when the instance exists and the mode was written.

        Raises:
            ValidationError: Not a mode.
        """
        if mode not in TLS_MODES:
            raise ValidationError(
                f"{mode!r} is not a TLS mode",
                details=f"The modes are: {', '.join(TLS_MODES)}.",
                field="mode",
            )
        return self._set(name, {"tls_mode": mode})

    def set_memory_limit(self, name: str, megabytes: int) -> bool:
        """
        Store an instance's memory limit.

        Args:
            name: The instance's name.
            megabytes: The limit.

        Returns:
            True when the instance exists and the limit was written.

        Raises:
            ValidationError: Not a positive amount.
        """
        return self._set(
            name,
            {
                "memory_limit_mb": rules.check_memory(
                    megabytes, minimum=1, maximum=_MEMORY_CEILING_MB
                )
            },
        )

    def set_digest(self, name: str, digest: str) -> bool:
        """
        Store the image an instance now runs, keeping what ran before it.

        Args:
            name: The instance's name.
            digest: The new image's digest.

        Returns:
            True when the instance exists and the digest was written.

        Raises:
            ValidationError: Not a digest.
        """
        image_rules.check_digest(digest)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_instances SET previous_digest = digest, digest = ?, "
                "upgraded_at = ?, updated_at = ? WHERE name = ?",
                (digest, _now(), _now(), name),
            )
            return cursor.rowcount > 0

    def delete(self, name: str) -> None:
        """
        Forget an instance.

        Args:
            name: The instance's name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM db_instances WHERE name = ?", (name,))
