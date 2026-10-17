# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site Noust adopted is, as the store keeps it.

One row per adopted application in ``app_adoption`` (``schema_v14.RUNTIME_SQL``, checklist B-85):
whether it is a web server in front of a unit the operator wrote (``proxy``), PHP served through
FastCGI (``php``) or files (``static``), which unit that is, and which site file and directory
the operator already had. The row is the only thing that makes the unit one Noust may start, stop,
restart and read the logs of: the unit file is never written, so it never gets the marker that
makes the other units Noust's own.

A table of its own, not columns of ``apps``, for the reason ``app_hardening`` is one: the full-row
write of any later operation cannot lose it. This is the only module that reads or writes it
(rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from noust.core.store import NoustStore, get_store
from noust.core.utils import domain_to_app_name

AdoptionKind = Literal["proxy", "php", "static"]

KINDS: tuple[AdoptionKind, ...] = ("proxy", "php", "static")


@dataclass(frozen=True)
class AdoptionRecord:
    """
    What an adopted site is.

    Attributes:
        kind: ``proxy``, ``php`` or ``static``.
        unit: The operator's unit, without ``.service``; None when the web server alone serves it.
        webserver: ``nginx`` or ``apache``.
        site_file: The operator's site file, as it is named in the sites directory.
        root: The directory the site's files are, as the site or the unit says.
        upstream: What the site hands requests to (``127.0.0.1:8000``, a FastCGI socket).
        adopted_by: Who adopted it.
        adopted_at: When, as an ISO timestamp.
    """

    kind: AdoptionKind
    unit: str | None
    webserver: str
    site_file: str
    root: str | None = None
    upstream: str | None = None
    adopted_by: str | None = None
    adopted_at: str | None = None


def _record(row: tuple[object, ...]) -> AdoptionRecord:
    """
    Args:
        row: ``kind, unit, webserver, site_file, root, upstream, adopted_by, adopted_at``.

    Returns:
        The record.
    """
    kind = str(row[0])
    return AdoptionRecord(
        kind="proxy" if kind == "proxy" else "php" if kind == "php" else "static",
        unit=str(row[1]) if row[1] else None,
        webserver=str(row[2]),
        site_file=str(row[3]),
        root=str(row[4]) if row[4] else None,
        upstream=str(row[5]) if row[5] else None,
        adopted_by=str(row[6]) if row[6] else None,
        adopted_at=str(row[7]) if row[7] else None,
    )


def get_record(domain: str, *, store: NoustStore | None = None) -> AdoptionRecord | None:
    """
    Read what an adopted application is.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its record, or None for an application that was not adopted.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT t.kind, t.unit, t.webserver, t.site_file, t.root, t.upstream, "
            "t.adopted_by, t.adopted_at "
            "FROM app_adoption t JOIN apps a ON a.id = t.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    return None if row is None else _record(tuple(row))


def set_record(
    domain: str,
    record: AdoptionRecord,
    *,
    store: NoustStore | None = None,
) -> bool:
    """
    Store what an adopted application is.

    Args:
        domain: The application's domain.
        record: What it is; ``adopted_at`` is set here.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        cursor.execute(
            "INSERT INTO app_adoption "
            "(app_id, kind, unit, webserver, site_file, root, upstream, adopted_by, adopted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "kind = excluded.kind, unit = excluded.unit, webserver = excluded.webserver, "
            "site_file = excluded.site_file, root = excluded.root, upstream = excluded.upstream, "
            "adopted_by = excluded.adopted_by, adopted_at = excluded.adopted_at",
            (
                found[0],
                record.kind,
                record.unit,
                record.webserver,
                record.site_file,
                record.root,
                record.upstream,
                record.adopted_by,
                record.adopted_at or datetime.now().isoformat(),
            ),
        )
    return True


def forget_record(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget that an application was adopted, leaving everything it was untouched.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_adoption WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0


def adopted_units(store: NoustStore | None = None) -> dict[str, str]:
    """
    Name every unit an adopted application runs as, and the application's own name for it.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The unit (without ``.service``) to the name an application of that domain is addressed by
        (:func:`~noust.core.utils.domain_to_app_name`), so a caller that only has the application's
        name finds the unit. A unit-less site is not here.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT t.unit, a.domain FROM app_adoption t JOIN apps a ON a.id = t.app_id "
            "WHERE t.unit IS NOT NULL AND t.unit != ''"
        )
        rows = cursor.fetchall()
    return {str(unit): domain_to_app_name(str(domain)) for unit, domain in rows}
