# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of the certificate a host engine serves TLS with.

One table from schema v14 (``core/schema_v14.py``, the databases fragment): ``db_host_tls``,
one row per engine. It says which name the engine is reached by, which certificate Noust copies
from (an ACME lineage, an uploaded certificate), where the copy is and the fingerprint of the
copy the engine serves, so that a renewal is noticed by comparing the source with it. Never a
key.

Rows go through the store's own transaction so ``--dry-run`` rehearses them like every other
write.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore

__all__ = ["HostTlsRecord", "HostTlsRecords"]

#: The longest error a row keeps.
_MAX_ERROR = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class HostTlsRecord:
    """
    The certificate an engine serves.

    Attributes:
        engine: The engine's key (``postgresql``, ``mysql``).
        hostname: The name clients reach the engine by, which the certificate must cover.
        source: ``acme`` (the lineage named after the host), ``lineage`` (another lineage) or
            ``custom`` (an uploaded certificate).
        certificate: The lineage's or the upload's name, for the last two.
        installed_dir: Where the engine's copy of the certificate and key is.
        fingerprint: The SHA-256 of the leaf certificate of that copy.
        not_after: When it expires, as openssl prints it.
        ssl_was: What the engine's ``ssl`` setting was before, so turning it off restores it.
        last_refresh_at: When the copy was last made or checked.
        last_status: ``ok`` or ``failed``.
        last_error: What the last failed refresh said.
        created_at: When TLS was turned on.
        updated_at: When the row last changed.
    """

    engine: str
    hostname: str
    source: str
    installed_dir: str
    certificate: str | None = None
    fingerprint: str | None = None
    not_after: str | None = None
    ssl_was: str | None = None
    last_refresh_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> HostTlsRecord:
        """
        Args:
            row: The ``db_host_tls`` row.

        Returns:
            The record.
        """
        return cls(**dict(row))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record as plain data, JSON-ready.
        """
        return asdict(self)


class HostTlsRecords:
    """
    Read and write the records, through a store's transactions.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the table.
        """
        self.store = store

    def get(self, engine: str) -> HostTlsRecord | None:
        """
        Args:
            engine: The engine's key.

        Returns:
            Its record, or None when it serves no certificate of Noust's.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_host_tls WHERE engine = ?", (engine,))
            row = cursor.fetchone()
            return HostTlsRecord.from_row(row) if row else None

    def records(self) -> list[HostTlsRecord]:
        """
        Returns:
            Every record, by engine.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_host_tls ORDER BY engine")
            return [HostTlsRecord.from_row(row) for row in cursor.fetchall()]

    def save(self, record: HostTlsRecord) -> HostTlsRecord:
        """
        Create or replace a record.

        Args:
            record: The record.

        Returns:
            The record as stored.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO db_host_tls (engine, hostname, source, certificate, installed_dir, "
                "fingerprint, not_after, ssl_was, last_refresh_at, last_status, last_error, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine) DO UPDATE SET hostname = excluded.hostname, "
                "source = excluded.source, certificate = excluded.certificate, "
                "installed_dir = excluded.installed_dir, fingerprint = excluded.fingerprint, "
                "not_after = excluded.not_after, "
                "ssl_was = COALESCE(db_host_tls.ssl_was, excluded.ssl_was), "
                "last_refresh_at = excluded.last_refresh_at, last_status = excluded.last_status, "
                "last_error = excluded.last_error, updated_at = excluded.updated_at",
                (
                    record.engine,
                    record.hostname,
                    record.source,
                    record.certificate,
                    record.installed_dir,
                    record.fingerprint,
                    record.not_after,
                    record.ssl_was,
                    record.last_refresh_at,
                    record.last_status,
                    (record.last_error or "")[-_MAX_ERROR:] or None,
                    stamp,
                    stamp,
                ),
            )
        stored = self.get(record.engine)
        return stored if stored is not None else record

    def record_refresh(
        self,
        engine: str,
        *,
        ok: bool,
        error: str | None = None,
        fingerprint: str | None = None,
        not_after: str | None = None,
    ) -> None:
        """
        Note how a refresh ended.

        Args:
            engine: The engine's key.
            ok: Whether the engine now serves the source's certificate.
            error: What went wrong, verbatim.
            fingerprint: The new copy's, when a copy was made.
            not_after: Its expiry.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_host_tls SET last_refresh_at = ?, last_status = ?, last_error = ?, "
                "fingerprint = COALESCE(?, fingerprint), not_after = COALESCE(?, not_after), "
                "updated_at = ? WHERE engine = ?",
                (
                    _now(),
                    "ok" if ok else "failed",
                    None if ok else (error or "")[-_MAX_ERROR:],
                    fingerprint,
                    not_after,
                    _now(),
                    engine,
                ),
            )

    def delete(self, engine: str) -> bool:
        """
        Forget a record.

        Args:
            engine: The engine's key.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM db_host_tls WHERE engine = ?", (engine,))
            return cursor.rowcount > 0
