# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client certificates an application's site accepts, kept in the store.

One table of the networking fragment of schema v14, ``client_certificates``. A row is a
certificate Noust signed for a person or a machine to present to one application's site: its
serial (what the web server reads of a presented certificate), its fingerprint and name, when it
expires and, once it was revoked, when and by whom. The private key is never here: it is handed to
whoever the certificate was made for, once, and Noust keeps nothing it could be rebuilt from.

:class:`ClientCertificateStore` is the only reader and writer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class ClientCertificate:
    """
    A certificate signed for a site's clients.

    Attributes:
        id: The row.
        app_domain: The application's primary domain.
        name: What it is for (``ana-laptop``): the certificate's common name.
        serial: The certificate's serial, upper-case hex without a prefix, as the web server
            reads it (``$ssl_client_serial``).
        fingerprint: Its SHA-256 fingerprint, colon-separated.
        not_before: When it begins to be valid, ISO 8601 UTC.
        not_after: When it stops, ISO 8601 UTC.
        issued_by: Who asked for it, an actor label.
        issued_at: When, ISO 8601 UTC.
        revoked_at: When it was revoked, or None.
        revoked_by: Who revoked it, or None.
    """

    id: int
    app_domain: str
    name: str
    serial: str
    fingerprint: str
    not_before: str
    not_after: str
    issued_by: str | None
    issued_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None

    @property
    def revoked(self) -> bool:
        """Whether it was revoked."""
        return self.revoked_at is not None

    def to_dict(self) -> dict[str, str | bool | None]:
        """
        Returns:
            Every field but the row, JSON-ready.
        """
        return {
            "name": self.name,
            "serial": self.serial,
            "fingerprint": self.fingerprint,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
        }


#: The serials of a site, as the store hands them back. The store has a method called ``list``,
#: which inside its body hides the builtin from a type annotation.
Serials = list[str]


class ClientCertificateStore:
    """
    Read and write the client certificates of applications.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def add(
        self,
        app_domain: str,
        *,
        name: str,
        serial: str,
        fingerprint: str,
        not_before: str,
        not_after: str,
        issued_by: str | None,
    ) -> ClientCertificate:
        """
        Record a certificate that was signed.

        Args:
            app_domain: The application's primary domain.
            name: What it is for.
            serial: Its serial.
            fingerprint: Its SHA-256 fingerprint.
            not_before: When it begins to be valid.
            not_after: When it stops.
            issued_by: Who asked for it.

        Returns:
            The record.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO client_certificates "
                "(app_domain, name, serial, fingerprint, not_before, not_after, issued_by, "
                "issued_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (app_domain, name, serial, fingerprint, not_before, not_after, issued_by, now),
            )
        found = self.get(app_domain, serial)
        assert found is not None  # noqa: S101 - it was just inserted
        return found

    def get(self, app_domain: str, serial: str) -> ClientCertificate | None:
        """
        Args:
            app_domain: The application's primary domain.
            serial: A certificate's serial.

        Returns:
            The record, or None.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM client_certificates WHERE app_domain = ? AND serial = ?",
                (app_domain, serial.upper()),
            )
            .fetchone()
        )
        return _certificate(row) if row is not None else None

    def list(self, app_domain: str) -> list[ClientCertificate]:
        """
        Args:
            app_domain: The application's primary domain.

        Returns:
            Its certificates, newest first.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM client_certificates WHERE app_domain = ? ORDER BY id DESC",
                (app_domain,),
            )
            .fetchall()
        )
        return [_certificate(row) for row in rows]

    def revoked_serials(self, app_domain: str) -> Serials:
        """
        Args:
            app_domain: The application's primary domain.

        Returns:
            The serials of its revoked certificates, oldest first.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT serial FROM client_certificates "
                "WHERE app_domain = ? AND revoked_at IS NOT NULL ORDER BY id",
                (app_domain,),
            )
            .fetchall()
        )
        return [str(row["serial"]) for row in rows]

    def revoke(self, app_domain: str, serial: str, *, by: str | None) -> bool:
        """
        Mark a certificate revoked.

        Args:
            app_domain: The application's primary domain.
            serial: The certificate's serial.
            by: Who revokes it.

        Returns:
            True when it was revoked now; False when it was unknown or already revoked.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE client_certificates SET revoked_at = ?, revoked_by = ? "
                "WHERE app_domain = ? AND serial = ? AND revoked_at IS NULL",
                (now, by, app_domain, serial.upper()),
            )
            return cursor.rowcount > 0

    def forget(self, app_domain: str) -> None:
        """
        Drop the records of an application that was deleted.

        Args:
            app_domain: The application's primary domain.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM client_certificates WHERE app_domain = ?", (app_domain,))


def _certificate(row: sqlite3.Row) -> ClientCertificate:
    """
    Args:
        row: A row of ``client_certificates``.

    Returns:
        The record.
    """
    return ClientCertificate(
        id=int(row["id"]),
        app_domain=str(row["app_domain"]),
        name=str(row["name"]),
        serial=str(row["serial"]),
        fingerprint=str(row["fingerprint"]),
        not_before=str(row["not_before"]),
        not_after=str(row["not_after"]),
        issued_by=row["issued_by"],
        issued_at=str(row["issued_at"]),
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
    )
