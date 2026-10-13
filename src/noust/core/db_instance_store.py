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

#: The bounds a CPU limit, a swap allowance and a probe's thresholds may take. They are
#: sanity bounds that keep a typo from becoming an argument of ``docker``; the manager
#: checks what the machine can give.
CPU_RANGE = (0.1, 256.0)
SWAP_CEILING_MB = 4 * 1024 * 1024
INTERVAL_RANGE = (2, 3600)
TIMEOUT_RANGE = (1, 300)
RETRIES_RANGE = (1, 100)

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
        cpu_limit: How many CPUs the container may use (``1.5``), or None for
            all the server has.
        swap_limit_mb: How much swap it may use on top of its memory; 0 gives it
            none, None leaves Docker's own default.
        probe_interval_s: Seconds between health probes, or None for Noust's default.
        probe_timeout_s: Seconds a probe may take, or None for the default.
        probe_retries: Failed probes in a row before the engine is called
            unhealthy, or None for the default.
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
    cpu_limit: float | None = None
    swap_limit_mb: int | None = None
    probe_interval_s: int | None = None
    probe_timeout_s: int | None = None
    probe_retries: int | None = None

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
            "cpu_limit": self.cpu_limit,
            "swap_limit_mb": self.swap_limit_mb,
            "probe_interval_s": self.probe_interval_s,
            "probe_timeout_s": self.probe_timeout_s,
            "probe_retries": self.probe_retries,
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
        cpu_limit=row["cpu_limit"],
        swap_limit_mb=row["swap_limit_mb"],
        probe_interval_s=row["probe_interval_s"],
        probe_timeout_s=row["probe_timeout_s"],
        probe_retries=row["probe_retries"],
    )


def _in_range(value: object, bounds: tuple[int, int], field: str, what: str) -> int:
    """
    Accept a whole number inside bounds.

    Args:
        value: What was given.
        bounds: The least and the most.
        field: The field the error names.
        what: What the number counts, for the message.

    Returns:
        The number.

    Raises:
        ValidationError: Not a whole number in range.
    """
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(
            f"{value!r} is not a valid number of {what}",
            details=f"Use a whole number from {low} to {high}.",
            field=field,
        )
    return value


def check_cpus(value: object) -> float:
    """
    Accept a number of CPUs.

    Args:
        value: A number, such as ``0.5``, ``2`` or ``"1.5"``.

    Returns:
        The number, rounded to hundredths (what Docker keeps).

    Raises:
        ValidationError: Not a number in :data:`CPU_RANGE`.
    """
    try:
        number = float(str(value).strip()) if not isinstance(value, bool) else float("nan")
    except ValueError:
        number = float("nan")
    low, high = CPU_RANGE
    if not low <= number <= high:
        raise ValidationError(
            f"{value!r} is not a number of CPUs",
            details=f"Use a number from {low:g} to {high:g}, such as 0.5 or 2.",
            field="cpus",
        )
    return round(number, 2)


def check_swap(value: object) -> int:
    """
    Accept an amount of swap in megabytes.

    Args:
        value: Whole megabytes; 0 is no swap.

    Returns:
        The amount.

    Raises:
        ValidationError: Not a whole number from 0 to the sanity bound.
    """
    return _in_range(value, (0, SWAP_CEILING_MB), "swap_mb", "megabytes of swap")


def check_probe(
    *, interval_s: object, timeout_s: object, retries: object
) -> tuple[int | None, int | None, int | None]:
    """
    Accept the thresholds of a health probe; None is "the default".

    Args:
        interval_s: Seconds between probes.
        timeout_s: Seconds a probe may take.
        retries: Failed probes in a row before the engine is called unhealthy.

    Returns:
        The three, each checked.

    Raises:
        ValidationError: A value is out of range, or both the timeout and the interval are
            given and the timeout is the longer.
    """
    interval = (
        None
        if interval_s is None
        else _in_range(interval_s, INTERVAL_RANGE, "interval", "seconds between probes")
    )
    timeout = (
        None
        if timeout_s is None
        else _in_range(timeout_s, TIMEOUT_RANGE, "timeout", "seconds a probe may take")
    )
    tries = (
        None if retries is None else _in_range(retries, RETRIES_RANGE, "retries", "failed probes")
    )
    if interval is not None and timeout is not None and timeout > interval:
        raise ValidationError(
            "A probe cannot take longer than the time between probes",
            details=f"The timeout is {timeout} s and the interval {interval} s.",
            field="timeout",
        )
    return interval, timeout, tries


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

    def set_cpu_limit(self, name: str, cpus: float | None) -> bool:
        """
        Store how many CPUs an instance's container may use.

        Args:
            name: The instance's name.
            cpus: A number of CPUs, such as ``0.5`` or ``2``; None removes the limit.

        Returns:
            True when the instance exists and the limit was written.

        Raises:
            ValidationError: Not a number in :data:`CPU_RANGE`.
        """
        value = None if cpus is None else check_cpus(cpus)
        return self._set(name, {"cpu_limit": value})

    def set_swap_limit(self, name: str, megabytes: int | None) -> bool:
        """
        Store how much swap an instance's container may use on top of its memory.

        Args:
            name: The instance's name.
            megabytes: The allowance; 0 gives it no swap, None leaves Docker's default.

        Returns:
            True when the instance exists and the allowance was written.

        Raises:
            ValidationError: Not an amount in range.
        """
        value = None if megabytes is None else check_swap(megabytes)
        return self._set(name, {"swap_limit_mb": value})

    def set_probe(
        self,
        name: str,
        *,
        interval_s: int | None,
        timeout_s: int | None,
        retries: int | None,
    ) -> bool:
        """
        Store the thresholds of an instance's health probe.

        Args:
            name: The instance's name.
            interval_s: Seconds between probes; None for the default.
            timeout_s: Seconds a probe may take; None for the default.
            retries: Failed probes in a row before it is called unhealthy; None for the default.

        Returns:
            True when the instance exists and the thresholds were written.

        Raises:
            ValidationError: A value is out of range, or the timeout is longer than the interval.
        """
        checked_interval, checked_timeout, checked_retries = check_probe(
            interval_s=interval_s, timeout_s=timeout_s, retries=retries
        )
        return self._set(
            name,
            {
                "probe_interval_s": checked_interval,
                "probe_timeout_s": checked_timeout,
                "probe_retries": checked_retries,
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
