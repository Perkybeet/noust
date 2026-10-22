# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's build block is, as the store keeps it.

Two things live in the ``app_build`` table (``schema_v14.BUILD_SQL``), one row
per application:

- ``document``: the operator's ``build:`` block, the YAML Noust wrote after
  validating it (:mod:`noust.deployers.helpers.build_config`). It replaces the
  repository's whole, the same precedence as the operator's hooks. No document
  means the repository's ``noust.yaml`` applies.
- ``applied``: what the application's unit was last written from, as JSON (its
  start command, base directory and runtime). An update compares it with what
  the next deployment would write and rewrites a stale unit, so a start command
  changed in the repository, or by the operator, reaches the process without a
  redeploy; and ``noust runtime remove`` reads it to refuse removing a runtime
  something still runs on.

The table is not a set of columns on ``apps`` on purpose: a deploy writes the
whole ``apps`` row it read when it started, and a setting an operator changed
meanwhile would be put back. This is the only module that reads or writes the
table (rule 3); validation of the document is the caller's, at the chokepoint
that builds it (``set_operator_build``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: The largest document stored; a build block is a handful of lines.
MAX_DOCUMENT_BYTES = 64 * 1024


def get_document(domain: str, *, store: NoustStore | None = None) -> str | None:
    """
    Read the build block the operator set for an application.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The YAML document as stored, or None when the operator set none.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT b.document FROM app_build b JOIN apps a ON a.id = b.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    return str(row[0]) if row is not None and row[0] is not None else None


def set_document(
    domain: str,
    document: str | None,
    *,
    updated_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Store, or clear, the operator's build block for an application.

    The document is validated by the caller; here it only has to be text of a
    sane size, because it is read back as YAML on every deployment.

    Args:
        domain: The application's domain.
        document: The YAML document, or None to clear it.
        updated_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.

    Raises:
        ValidationError: The document is larger than :data:`MAX_DOCUMENT_BYTES`.
    """
    if document is not None and len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValidationError(
            f"The build block is larger than {MAX_DOCUMENT_BYTES // 1024} KiB",
            details="A build block is a handful of lines.",
            field="build",
        )
    target = store or get_store()
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return False
        app_id = row[0]
        cursor.execute(
            "INSERT INTO app_build (app_id, document, applied, updated_by, updated_at) "
            "VALUES (?, ?, NULL, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "document = excluded.document, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (app_id, document, updated_by, now),
        )
        # A row with neither half holds nothing worth keeping.
        cursor.execute(
            "DELETE FROM app_build WHERE app_id = ? AND document IS NULL AND applied IS NULL",
            (app_id,),
        )
    return True


def get_applied(domain: str, *, store: NoustStore | None = None) -> dict[str, Any] | None:
    """
    Read what an application's unit was last written from.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The record, or None when the unit was never written from a build
        block (an application that has none, or from before 4.0).
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT b.applied FROM app_build b JOIN apps a ON a.id = b.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    try:
        data = json.loads(row[0])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def set_applied(
    domain: str, applied: Mapping[str, Any] | None, *, store: NoustStore | None = None
) -> bool:
    """
    Record what an application's unit was just written from.

    Args:
        domain: The application's domain.
        applied: The record, or None to forget it (the unit is written from
            nothing a build block says).
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    target = store or get_store()
    text = None if applied is None else json.dumps(applied, sort_keys=True)
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return False
        app_id = row[0]
        cursor.execute(
            "INSERT INTO app_build (app_id, document, applied, updated_by, updated_at) "
            "VALUES (?, NULL, ?, NULL, ?) ON CONFLICT(app_id) DO UPDATE SET applied = excluded.applied",
            (app_id, text, now),
        )
        cursor.execute(
            "DELETE FROM app_build WHERE app_id = ? AND document IS NULL AND applied IS NULL",
            (app_id,),
        )
    return True


def runtime_users(language: str, version: str, *, store: NoustStore | None = None) -> list[str]:
    """
    Name the applications whose unit was written for a runtime.

    Args:
        language: ``node``, ``python`` or ``php``.
        version: The full version of the installed runtime.
        store: The store; the process-wide one when None.

    Returns:
        Their domains, sorted.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT a.domain, b.applied FROM app_build b JOIN apps a ON a.id = b.app_id "
            "WHERE b.applied IS NOT NULL"
        )
        rows = cursor.fetchall()
    users = []
    for domain, text in rows:
        try:
            runtime = (json.loads(text) or {}).get("runtime") or {}
        except (ValueError, AttributeError):
            continue
        if runtime.get("language") == language and runtime.get("version") == version:
            users.append(str(domain))
    return sorted(users)


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "get_applied",
    "get_document",
    "runtime_users",
    "set_applied",
    "set_document",
]
