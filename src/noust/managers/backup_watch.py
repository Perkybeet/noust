# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the monitor's backup watches look at, beyond an application's schedule.

``monitor/watch_backups.py`` announces a backup that did not run (``backup_missing``)
and ``monitor/watch_destinations.py`` a destination that stopped answering
(``destination_unreachable``). Both decide when to speak; this module reads the
store for them, so that the watch is one implementation for every kind of backup
Noust takes (rule 3) and not one per kind:

- a database's backup policy, an application's data policy (its directories and
  volumes) and Noust's own state backup each keep the time of their last
  successful run, which an application's schedule does not (its backups are
  files, listed by the backup manager);
- every destination some policy sends to, whichever kind of policy it is.

Nothing here talks; it reports what exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

#: What the monitor's memory keeps a policy under, before its subject.
KIND_DATABASE = "database"
KIND_DATA = "data"
KIND_NOUST = "noust"


@dataclass(frozen=True)
class PolicyBackup:
    """
    A backup policy the monitor can judge by its own record of the last run.

    Attributes:
        key: What the monitor remembers it under (``database:postgresql/shop``).
        kind: ``database``, ``data`` or ``noust``.
        subject: What it is about, as the operator reads it.
        schedule: The policy's calendar.
        last_success: When a backup last succeeded; None when none ever did.
        created: When the policy was made, from when it has had its chance.
        command: What to run to take a backup now.
        application: Whether the subject is an application's domain.
    """

    key: str
    kind: str
    subject: str
    schedule: str
    last_success: datetime | None
    created: datetime | None
    command: str
    application: bool


def moment(text: str | None) -> datetime | None:
    """
    Read a timestamp the store keeps.

    Args:
        text: ISO 8601 text, naive (UTC, as SQLite writes it) or with an offset.

    Returns:
        An aware datetime; None for nothing or something unreadable.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def policy_backups(store: NoustStore | None = None) -> list[PolicyBackup]:
    """
    List every enabled policy that keeps the time of its last good backup.

    An application's own schedule is not here: its backups are files, which the
    watch asks the backup manager about.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        One entry per enabled database policy, per enabled data policy and, if
        it is enabled, Noust's own state backup.
    """
    from noust.managers.database.backup_records import BackupRecords
    from noust.managers.self_backup_records import SelfBackupRecords
    from noust.managers.volume_backup_records import VolumeRecords

    target = store or get_store()
    found: list[PolicyBackup] = []
    for policy in BackupRecords(target).policies():
        if not policy.enabled:
            continue
        subject = f"{policy.engine}/{policy.db_name}"
        found.append(
            PolicyBackup(
                key=f"{KIND_DATABASE}:{subject}",
                kind=KIND_DATABASE,
                subject=subject,
                schedule=policy.schedule,
                last_success=moment(policy.last_success_at),
                created=moment(policy.created_at),
                command=f"noust db backup-run {policy.db_name} -e {policy.engine}",
                application=False,
            )
        )
    for volume in VolumeRecords(target).policies():
        if not volume.enabled:
            continue
        found.append(
            PolicyBackup(
                key=f"{KIND_DATA}:{volume.app_domain}",
                kind=KIND_DATA,
                subject=volume.app_domain,
                schedule=volume.schedule,
                last_success=moment(volume.last_success_at),
                created=moment(volume.created_at),
                command=f"noust backup volumes run {volume.app_domain}",
                application=True,
            )
        )
    own = SelfBackupRecords(target).get()
    if own is not None and own.enabled:
        found.append(
            PolicyBackup(
                key=f"{KIND_NOUST}:state",
                kind=KIND_NOUST,
                subject="noust",
                schedule=own.schedule,
                last_success=moment(own.last_success_at),
                created=moment(own.created_at),
                command="noust self-backup run",
                application=False,
            )
        )
    return found


def destinations_in_use(store: NoustStore | None = None) -> set[str]:
    """
    Name the destinations some backup policy sends to.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        Their names; a destination nothing uses is not worth a message.
    """
    from noust.managers.database.backup_records import BackupRecords
    from noust.managers.self_backup_records import SelfBackupRecords
    from noust.managers.volume_backup_records import VolumeRecords

    target = store or get_store()
    lists: list[list[dict[str, object]]] = [
        policy.destinations for policy in BackupRecords(target).policies()
    ]
    lists.extend(policy.destinations for policy in VolumeRecords(target).policies())
    own = SelfBackupRecords(target).get()
    if own is not None:
        lists.append(own.destinations)
    lists.extend(schedule.destinations for schedule in target.list_backup_schedules())
    used: set[str] = set()
    for entries in lists:
        used.update(str(entry["name"]) for entry in entries if entry.get("name"))
    return used
