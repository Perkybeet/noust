# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes an application runs beside its web process, as the store keeps them.

One row per process in ``app_processes`` (``schema_v14.RUNTIME_SQL``): its name,
its command, how many copies run and its own limits. ``source`` says where the
definition came from, the operator or the repository (``noust.yaml``): the
operator's wins, and the repository's are replaced by what it declares at every
deploy (:func:`sync_repository`).

A table of its own, not columns of ``apps``, so the full-row write of a redeploy
cannot touch it. This is the only module that reads or writes the table (rule 3);
what a process is, as a unit, is :mod:`noust.managers.app_processes`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

SOURCE_OPERATOR = "operator"
SOURCE_REPOSITORY = "repository"

#: The most copies of one process. A server has so many cores; past this it is a fleet.
MAX_INSTANCES = 32

#: A process name: it becomes part of a unit name, so it is a label and no more.
#: ``web`` is the application's own process and is never a row.
_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 24
RESERVED_NAMES = frozenset({"web", "main", "app", "proc", "current", "shared"})

#: The longest command line a process may have.
MAX_COMMAND_LENGTH = 2048


@dataclass(frozen=True)
class ProcessRecord:
    """
    One process of an application.

    Attributes:
        name: Its name, such as ``worker``.
        command: The command line, split like a POSIX shell would split it and
            run without one.
        instances: How many copies run; 0 keeps the definition and stops them.
        memory_max_mb: ``MemoryMax`` of each copy, or None.
        cpu_quota_percent: ``CPUQuota`` of each copy, or None.
        tasks_max: ``TasksMax`` of each copy, or None.
        source: ``operator`` or ``repository``.
        updated_by: Who set it last.
        updated_at: When, as an ISO timestamp.
    """

    name: str
    command: str
    instances: int = 1
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    source: str = SOURCE_OPERATOR
    updated_by: str | None = None
    updated_at: str | None = None


def check_name(name: str) -> str:
    """
    Accept a process name.

    Args:
        name: What the caller gave.

    Returns:
        The name.

    Raises:
        ValidationError: It is not a short lower-case label, or it is one the
            application's own processes use.
    """
    how = "Use a short lower-case name such as worker, queue or scheduler."
    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or _NAME.fullmatch(name) is None:
        raise ValidationError(f"{name!r} is not a process name", details=how, field="name")
    if name in RESERVED_NAMES:
        raise ValidationError(
            f"{name!r} is the name of the application's own process", details=how, field="name"
        )
    return name


def check_instances(instances: int) -> int:
    """
    Accept how many copies of a process run.

    Args:
        instances: A count.

    Returns:
        The same count.

    Raises:
        ValidationError: It is not a whole number from 0 to :data:`MAX_INSTANCES`.
    """
    if (
        isinstance(instances, bool)
        or not isinstance(instances, int)
        or not 0 <= instances <= MAX_INSTANCES
    ):
        raise ValidationError(
            f"{instances!r} copies of a process is out of range",
            details=f"Give it from 0 (stopped) to {MAX_INSTANCES} copies.",
            field="instances",
        )
    return instances


def _from_row(row: tuple[object, ...]) -> ProcessRecord:
    """
    Build a record from a selected row.

    Args:
        row: ``name, command, instances, memory, cpu, tasks, source, by, at``.

    Returns:
        The record.
    """
    return ProcessRecord(
        name=str(row[0]),
        command=str(row[1]),
        instances=int(row[2]),  # type: ignore[call-overload]
        memory_max_mb=row[3],  # type: ignore[arg-type]
        cpu_quota_percent=row[4],  # type: ignore[arg-type]
        tasks_max=row[5],  # type: ignore[arg-type]
        source=str(row[6]),
        updated_by=row[7],  # type: ignore[arg-type]
        updated_at=row[8],  # type: ignore[arg-type]
    )


def list_processes(domain: str, *, store: NoustStore | None = None) -> list[ProcessRecord]:
    """
    List an application's processes.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its processes by name; empty for an application with none.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT p.name, p.command, p.instances, p.memory_max_mb, p.cpu_quota_percent, "
            "p.tasks_max, p.source, p.updated_by, p.updated_at "
            "FROM app_processes p JOIN apps a ON a.id = p.app_id "
            "WHERE a.domain = ? ORDER BY p.name",
            (domain,),
        )
        return [_from_row(tuple(row)) for row in cursor.fetchall()]


def get_process(domain: str, name: str, *, store: NoustStore | None = None) -> ProcessRecord | None:
    """
    Read one of an application's processes.

    Args:
        domain: The application's domain.
        name: The process.
        store: The store; the process-wide one when None.

    Returns:
        The record, or None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT p.name, p.command, p.instances, p.memory_max_mb, p.cpu_quota_percent, "
            "p.tasks_max, p.source, p.updated_by, p.updated_at "
            "FROM app_processes p JOIN apps a ON a.id = p.app_id "
            "WHERE a.domain = ? AND p.name = ?",
            (domain, name),
        )
        row = cursor.fetchone()
    return _from_row(tuple(row)) if row is not None else None


def put_process(
    domain: str,
    record: ProcessRecord,
    *,
    updated_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Store a process, replacing the one of that name.

    The command is validated by the manager that builds the unit; here it only
    has to be a line of a sane size.

    Args:
        domain: The application's domain.
        record: The process.
        updated_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.

    Raises:
        ValidationError: The name, the count or the command is not one a unit can have.
    """
    check_name(record.name)
    check_instances(record.instances)
    if (
        not record.command.strip()
        or len(record.command) > MAX_COMMAND_LENGTH
        or any(ord(char) < 0x20 for char in record.command)
    ):
        raise ValidationError(
            "A process needs a command on one line",
            details="Give the program and its arguments, such as: node worker.js",
            field="command",
        )
    if record.source not in (SOURCE_OPERATOR, SOURCE_REPOSITORY):
        raise ValidationError(f"{record.source!r} is not a source of a process", field="source")
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        cursor.execute(
            "INSERT INTO app_processes (app_id, name, command, instances, memory_max_mb, "
            "cpu_quota_percent, tasks_max, source, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id, name) DO UPDATE SET command = excluded.command, "
            "instances = excluded.instances, memory_max_mb = excluded.memory_max_mb, "
            "cpu_quota_percent = excluded.cpu_quota_percent, tasks_max = excluded.tasks_max, "
            "source = excluded.source, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (
                found[0],
                record.name,
                record.command.strip(),
                record.instances,
                record.memory_max_mb,
                record.cpu_quota_percent,
                record.tasks_max,
                record.source,
                updated_by,
                datetime.now().isoformat(),
            ),
        )
    return True


def delete_process(domain: str, name: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget a process.

    Args:
        domain: The application's domain.
        name: The process.
        store: The store; the process-wide one when None.

    Returns:
        True when there was one.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_processes WHERE name = ? AND "
            "app_id = (SELECT id FROM apps WHERE domain = ?)",
            (name, domain),
        )
        return cursor.rowcount > 0


def sync_repository(
    domain: str,
    declared: Iterable[ProcessRecord],
    *,
    store: NoustStore | None = None,
) -> tuple[list[str], list[str]]:
    """
    Make the repository-declared processes exactly what the repository declares.

    The operator's own processes are never touched, and a repository process
    whose name an operator's process already uses is skipped: the operator's wins.
    Copies the operator scaled are kept: a repository that declares a process
    gives its command, not how many copies this server runs of it.

    Args:
        domain: The application's domain.
        declared: What the repository declares.
        store: The store; the process-wide one when None.

    Returns:
        The names added or changed, and the names removed.
    """
    target = store or get_store()
    have = {record.name: record for record in list_processes(domain, store=target)}
    changed: list[str] = []
    wanted = {record.name for record in declared}
    for record in declared:
        known = have.get(record.name)
        if known is not None and known.source == SOURCE_OPERATOR:
            continue
        instances = known.instances if known is not None else record.instances
        merged = ProcessRecord(
            name=record.name,
            command=record.command,
            instances=instances,
            memory_max_mb=record.memory_max_mb,
            cpu_quota_percent=record.cpu_quota_percent,
            tasks_max=record.tasks_max,
            source=SOURCE_REPOSITORY,
        )
        if known is None or (
            known.command,
            known.memory_max_mb,
            known.cpu_quota_percent,
            known.tasks_max,
        ) != (
            merged.command,
            merged.memory_max_mb,
            merged.cpu_quota_percent,
            merged.tasks_max,
        ):
            put_process(domain, merged, updated_by="repository", store=target)
            changed.append(record.name)
    removed = []
    for name, known in have.items():
        if known.source == SOURCE_REPOSITORY and name not in wanted:
            delete_process(domain, name, store=target)
            removed.append(name)
    return changed, removed
