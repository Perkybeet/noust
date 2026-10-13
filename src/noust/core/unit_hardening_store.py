# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How much of systemd's sandbox an application's unit runs in, as the store keeps it.

One row per application in ``app_hardening`` (``schema_v14.RUNTIME_SQL``): the
profile (``off``, ``default`` or ``strict``) and the extra directories the unit
may write to. No row means the application predates hardening: its unit is as
it always was, and a redeploy never changes that on its own. An operator moves
it with ``noust app hardening``, behind the health gate.

A table of its own, not columns of ``apps``, so the full-row write of a deploy
cannot put back what an operator changed meanwhile. This is the only module
that reads or writes it (rule 3); what a profile means is
:mod:`noust.managers.unit_hardening`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: The profiles, from none to most.
PROFILES = ("off", "default", "strict")


@dataclass(frozen=True)
class HardeningRecord:
    """
    What an application's hardening is set to.

    Attributes:
        profile: ``off``, ``default`` or ``strict``.
        writable_paths: Extra directories the unit may write to, beyond the
            application's own tree.
        changed_by: Who set it last.
        changed_at: When, as an ISO timestamp.
    """

    profile: str
    writable_paths: tuple[str, ...] = ()
    changed_by: str | None = None
    changed_at: str | None = None


def get_record(domain: str, *, store: NoustStore | None = None) -> HardeningRecord | None:
    """
    Read an application's hardening.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its record, or None for an application that predates hardening.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT h.profile, h.writable_paths, h.changed_by, h.changed_at "
            "FROM app_hardening h JOIN apps a ON a.id = h.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    try:
        paths = tuple(str(path) for path in json.loads(row[1] or "[]"))
    except (ValueError, TypeError):
        paths = ()
    return HardeningRecord(
        profile=str(row[0]), writable_paths=paths, changed_by=row[2], changed_at=row[3]
    )


def set_record(
    domain: str,
    profile: str,
    writable_paths: tuple[str, ...] = (),
    *,
    changed_by: str | None,
    keep_existing: bool = False,
    store: NoustStore | None = None,
) -> bool:
    """
    Store an application's hardening.

    The paths are validated by the caller (:mod:`noust.managers.unit_hardening`
    owns what may be written); here they only have to be text.

    Args:
        domain: The application's domain.
        profile: ``off``, ``default`` or ``strict``.
        writable_paths: The extra directories.
        changed_by: Who changed it, for the record.
        keep_existing: Write nothing when the application already has a row,
            which is how a first deployment sets the default without undoing
            an operator's choice.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.

    Raises:
        ValidationError: The profile is not one of :data:`PROFILES`.
    """
    if profile not in PROFILES:
        raise ValidationError(
            f"{profile!r} is not a hardening profile",
            details=f"Use one of: {', '.join(PROFILES)}.",
            field="profile",
        )
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        if keep_existing:
            cursor.execute("SELECT 1 FROM app_hardening WHERE app_id = ?", (found[0],))
            if cursor.fetchone() is not None:
                return True
        cursor.execute(
            "INSERT INTO app_hardening (app_id, profile, writable_paths, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "profile = excluded.profile, writable_paths = excluded.writable_paths, "
            "changed_by = excluded.changed_by, changed_at = excluded.changed_at",
            (
                found[0],
                profile,
                json.dumps(list(writable_paths)),
                changed_by,
                datetime.now().isoformat(),
            ),
        )
    return True


def forget_record(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget an application's hardening, as if it predated the feature.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_hardening WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0
