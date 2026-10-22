# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether an update must have its backup first: the per-application setting.

An update takes a backup first so there is a way back from it. Until 4.0 a
backup that failed was a warning and the update went on, which is the right call
for an application that holds nothing but code and the wrong one for an
application with a database: a migration that runs after a backup that did not
happen cannot be undone. Cloudron's rule is that when the backup fails the
update is not applied; this is Noust's, per application:

- **Required** (the default for an application with a database): a backup that
  fails, or cannot be taken, stops the update before anything changes.
- **Not required** (the default for one without): a failed backup is a warning.

An operator's choice is a row in ``app_update_policy``
(``schema_v14.BACKUPS_SQL``), a table of its own so a deploy's full-row write of
``apps`` cannot put back what was changed while it ran. No row means the
default. This is the only module that reads or writes the table (rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class UpdateBackupPolicy:
    """
    What an application's updates do when their backup does not happen.

    Attributes:
        domain: The application.
        required: Whether the update is refused when its backup fails.
        explicit: Whether an operator chose it (False: the default applies).
        default_required: What the default is for this application.
        databases: The databases the application uses, which is why the
            default is what it is.
    """

    domain: str
    required: bool
    explicit: bool
    default_required: bool
    databases: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """
        Describe the policy for the API and the command line.

        Returns:
            Every attribute, with ``databases`` as a list.
        """
        return {
            "domain": self.domain,
            "required": self.required,
            "explicit": self.explicit,
            "default_required": self.default_required,
            "databases": list(self.databases),
        }


def _databases_of(domain: str, store: NoustStore) -> tuple[str, ...]:
    """
    Name the databases an application uses, as ``engine/name``.

    Args:
        domain: The application.
        store: The store.

    Returns:
        The databases linked to it. A Compose stack's own databases are not
        rows of the store and are covered by their own switch
        (``noust app backup-before-update``).
    """
    app = store.get_app(domain)
    if app is None or app.id is None:
        return ()
    return tuple(f"{db.engine}/{db.name}" for db in store.list_databases(app_id=app.id))


def get_policy(domain: str, *, store: NoustStore | None = None) -> UpdateBackupPolicy | None:
    """
    Read an application's backup requirement.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The policy, or None when the application does not exist.
    """
    target = store or get_store()
    if target.get_app(domain) is None:
        return None
    databases = _databases_of(domain, target)
    default = bool(databases)
    with target._transaction() as cursor:
        cursor.execute(
            "SELECT p.backup_required FROM app_update_policy p "
            "JOIN apps a ON a.id = p.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return UpdateBackupPolicy(domain, default, False, default, databases)
    return UpdateBackupPolicy(domain, bool(row[0]), True, default, databases)


def set_required(
    domain: str, required: bool | None, *, updated_by: str | None, store: NoustStore | None = None
) -> UpdateBackupPolicy | None:
    """
    Choose whether an application's updates need their backup, or go back to the default.

    Args:
        domain: The application's domain.
        required: True or False to choose, None to follow the default again.
        updated_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        The policy now in force, or None when the application does not exist.
    """
    target = store or get_store()
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return None
        if required is None:
            cursor.execute("DELETE FROM app_update_policy WHERE app_id = ?", (row[0],))
        else:
            cursor.execute(
                "INSERT INTO app_update_policy (app_id, backup_required, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
                "backup_required = excluded.backup_required, updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at",
                (row[0], 1 if required else 0, updated_by, now),
            )
    return get_policy(domain, store=target)
