# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How each application's certificate comes to be, and the certificates uploaded by hand.

Two tables of the networking fragment of schema v14. ``certificate_settings``
has at most one row per application and says where its certificate comes from:
the lineage the certificate authority keeps under the application's name
(``acme``, ordered with the HTTP-01 or the DNS-01 challenge), an uploaded one
(``custom``), or another lineage that already covers its name (``lineage``, a
wildcard). ``custom_certificates`` lists what was uploaded: names, issuer,
validity and fingerprint; the certificate and its key are files
(:mod:`noust.managers.custom_certificates`), never columns.

:class:`CertificateStore` is the only reader and writer. An application with no
row gets the defaults, which are what every application did before there were
settings: HTTP-01, the lineage under its own name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

#: The lineage the certificate authority keeps under the application's name.
SOURCE_ACME = "acme"
#: A certificate the operator uploaded.
SOURCE_CUSTOM = "custom"
#: Another lineage that covers the name, such as a wildcard.
SOURCE_LINEAGE = "lineage"
SOURCES: tuple[str, ...] = (SOURCE_ACME, SOURCE_CUSTOM, SOURCE_LINEAGE)

#: The challenge answered over the application's own port 80.
CHALLENGE_HTTP = "http-01"
#: The challenge answered with a DNS record, which needs no open port.
CHALLENGE_DNS = "dns-01"
CHALLENGES: tuple[str, ...] = (CHALLENGE_HTTP, CHALLENGE_DNS)


@dataclass(frozen=True)
class CertificateSettings:
    """
    Where an application's certificate comes from.

    Attributes:
        domain: The application's primary domain.
        source: One of :data:`SOURCES`.
        name: The custom certificate or the lineage served, for those sources.
        challenge: One of :data:`CHALLENGES`, for ``acme``.
        dns_provider: The DNS account that answers DNS-01, for that challenge.
        updated_by: Who set it.
        updated_at: When, ISO 8601 UTC; empty for the defaults.
    """

    domain: str
    source: str = SOURCE_ACME
    name: str | None = None
    challenge: str = CHALLENGE_HTTP
    dns_provider: str | None = None
    updated_by: str | None = None
    updated_at: str = ""

    @property
    def is_default(self) -> bool:
        """Whether this is what an application without a row gets."""
        return (
            self.source == SOURCE_ACME
            and self.challenge == CHALLENGE_HTTP
            and self.name is None
            and self.dns_provider is None
        )


@dataclass(frozen=True)
class CustomCertificate:
    """
    A certificate the operator uploaded.

    Attributes:
        name: What it is called; a directory name of Noust's certificates directory.
        names: The names it covers (subject alternative names), wildcards included.
        subject: Its subject, as openssl prints it.
        issuer: Its issuer.
        key_type: ``RSA 2048``, ``EC prime256v1``...
        fingerprint: SHA-256 of the leaf, colon separated.
        not_before: Start of validity, ISO 8601 UTC.
        not_after: End of validity, ISO 8601 UTC.
        uploaded_by: Who uploaded it.
        uploaded_at: When, ISO 8601 UTC.
    """

    name: str
    names: tuple[str, ...]
    subject: str | None
    issuer: str | None
    key_type: str | None
    fingerprint: str
    not_before: str | None
    not_after: str | None
    uploaded_by: str | None
    uploaded_at: str


class CertificateStore:
    """
    Read and write certificate settings and the list of uploaded certificates.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get_settings(self, domain: str) -> CertificateSettings:
        """
        Read an application's certificate settings.

        Args:
            domain: Its primary domain.

        Returns:
            The settings; the defaults when it has none.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM certificate_settings WHERE domain = ?", (domain,))
            .fetchone()
        )
        if row is None:
            return CertificateSettings(domain=domain)
        return CertificateSettings(
            domain=str(row["domain"]),
            source=str(row["source"]),
            name=row["name"],
            challenge=str(row["challenge"]),
            dns_provider=row["dns_provider"],
            updated_by=row["updated_by"],
            updated_at=str(row["updated_at"]),
        )

    def set_settings(self, settings: CertificateSettings) -> None:
        """
        Keep an application's certificate settings; the defaults remove the row.

        Args:
            settings: What to keep.
        """
        with self.store._transaction() as cursor:
            if settings.is_default:
                cursor.execute(
                    "DELETE FROM certificate_settings WHERE domain = ?", (settings.domain,)
                )
                return
            cursor.execute(
                """
                INSERT INTO certificate_settings
                    (domain, source, name, challenge, dns_provider, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    source = excluded.source,
                    name = excluded.name,
                    challenge = excluded.challenge,
                    dns_provider = excluded.dns_provider,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    settings.domain,
                    settings.source,
                    settings.name,
                    settings.challenge,
                    settings.dns_provider,
                    settings.updated_by,
                    _now(),
                ),
            )

    def domains_using(self, source: str, name: str) -> list[str]:
        """
        List the applications served by a certificate or lineage.

        Args:
            source: ``custom`` or ``lineage``.
            name: The certificate or lineage.

        Returns:
            Their primary domains, sorted.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT domain FROM certificate_settings WHERE source = ? AND name = ? "
                "ORDER BY domain",
                (source, name),
            )
            .fetchall()
        )
        return [str(row["domain"]) for row in rows]

    def domains_with_provider(self, provider: str) -> list[str]:
        """
        List the applications whose certificates are ordered through a DNS account.

        Args:
            provider: The DNS account's name.

        Returns:
            Their primary domains, sorted.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT domain FROM certificate_settings WHERE dns_provider = ? ORDER BY domain",
                (provider,),
            )
            .fetchall()
        )
        return [str(row["domain"]) for row in rows]

    def forget(self, domain: str) -> None:
        """
        Drop the settings of an application that is gone.

        Args:
            domain: Its primary domain.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM certificate_settings WHERE domain = ?", (domain,))

    def list_custom(self) -> list[CustomCertificate]:
        """
        Returns:
            The uploaded certificates, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM custom_certificates ORDER BY name")
            .fetchall()
        )
        return [_custom(row) for row in rows]

    def get_custom(self, name: str) -> CustomCertificate | None:
        """
        Args:
            name: The certificate's name.

        Returns:
            The record, or None when nothing was uploaded under that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM custom_certificates WHERE name = ?", (name,))
            .fetchone()
        )
        return None if row is None else _custom(row)

    def save_custom(self, certificate: CustomCertificate) -> None:
        """
        Record an uploaded certificate, replacing one of the same name.

        Args:
            certificate: What to record.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO custom_certificates
                    (name, names, subject, issuer, key_type, fingerprint,
                     not_before, not_after, uploaded_by, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    names = excluded.names,
                    subject = excluded.subject,
                    issuer = excluded.issuer,
                    key_type = excluded.key_type,
                    fingerprint = excluded.fingerprint,
                    not_before = excluded.not_before,
                    not_after = excluded.not_after,
                    uploaded_by = excluded.uploaded_by,
                    uploaded_at = excluded.uploaded_at
                """,
                (
                    certificate.name,
                    json.dumps(list(certificate.names)),
                    certificate.subject,
                    certificate.issuer,
                    certificate.key_type,
                    certificate.fingerprint,
                    certificate.not_before,
                    certificate.not_after,
                    certificate.uploaded_by,
                    certificate.uploaded_at,
                ),
            )

    def remove_custom(self, name: str) -> bool:
        """
        Forget an uploaded certificate.

        Args:
            name: The certificate's name.

        Returns:
            True when it was recorded.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM custom_certificates WHERE name = ?", (name,))
            return cursor.rowcount > 0


def _custom(row: object) -> CustomCertificate:
    """
    Read an uploaded certificate out of its row.

    Args:
        row: A ``custom_certificates`` row.

    Returns:
        The record.
    """
    data = dict(row)  # type: ignore[call-overload]
    try:
        names = tuple(str(item) for item in json.loads(data["names"] or "[]"))
    except (TypeError, ValueError):
        names = ()
    return CustomCertificate(
        name=str(data["name"]),
        names=names,
        subject=data["subject"],
        issuer=data["issuer"],
        key_type=data["key_type"],
        fingerprint=str(data["fingerprint"]),
        not_before=data["not_before"],
        not_after=data["not_after"],
        uploaded_by=data["uploaded_by"],
        uploaded_at=str(data["uploaded_at"]),
    )


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat()
