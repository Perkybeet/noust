# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A host engine that serves TLS with a certificate a client can verify (checklist C-27).

A database made public (:mod:`noust.managers.database.public_access`) is reached through a
proxy that passes bytes: whatever certificate the engine presents is the one the client sees,
and the one a package leaves behind is self-signed, so the only thing a client can do is
encrypt without knowing who answers. Here the engine itself is given a real certificate (an
ACME lineage, or one the operator uploaded) for the name clients reach it by, so
``sslmode=verify-full`` works from anywhere and nothing terminates TLS in between.

**What it does.** The certificate and its key are checked (the key is the certificate's, it
covers the name, it has not expired), copied where the engine's account can read them (the
source stays root's: ``/etc/letsencrypt/archive`` is not a directory a database should be
given), and the engine is told to use the copy: PostgreSQL by ``ALTER SYSTEM`` and a reload,
MySQL and MariaDB by an include file and one restart. Then the engine is *asked what it
serves* (a TLS handshake, STARTTLS as the protocol defines it) and the fingerprint must be the
certificate's: a setting the engine silently ignored is not a success.

**Renewal.** ``noust db tls refresh`` compares each engine's copy with its source and, when
the source changed, copies it and makes the engine load it without a restart (PostgreSQL's
reload, MySQL's ``ALTER INSTANCE RELOAD TLS``, MariaDB's ``FLUSH SSL``), again proving what it
serves. certbot's deploy hook runs it after every renewal that succeeds, so a Let's Encrypt
certificate is served the day it is issued; for an uploaded certificate it is the command to
run after uploading a new one, and ``status`` says when the copy is behind its source.

**Undoing it** puts the engine's settings back (``ssl`` as it was, the include file gone) and
removes the copy.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.applock import AppBusyError, app_lock
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.store import get_store
from noust.managers.cert_manager import CertManager
from noust.managers.custom_certificates import served_files
from noust.managers.database import flavours
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.host_tls_records import HostTlsRecord, HostTlsRecords
from noust.managers.database.mysql_include import include_directory
from noust.validators.domain import is_valid_domain

__all__ = ["ENGINES", "HostTls", "leaf_fingerprint"]

#: What each host engine is given: the account that runs it, the protocol a TLS client opens its
#: handshake with, and the directory its copy lives in.
ENGINES: dict[str, dict[str, str]] = {
    "postgresql": {"account": "postgres", "starttls": "postgres"},
    "mysql": {"account": "mysql", "starttls": "mysql"},
}

#: Where the engines' copies are: a directory the engines' accounts can enter, which
#: ``/etc/noust`` (root's) is not.
COPY_ROOT = Path("/etc/ssl/noust-db")

#: Seconds an engine may take to answer after a restart.
READY_TIMEOUT = 180
POLL_SECONDS = 2.0

#: The name of the one-at-a-time lock of this area.
LOCK_KEY = "database-host-tls"

#: A certificate that expires within this many days is called out.
EXPIRY_WARNING_DAYS = 14

_PEM_CERT = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*(?P<body>[A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----"
)
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./-]+")


def leaf_fingerprint(pem: str) -> str | None:
    """
    Compute the SHA-256 fingerprint of the first certificate of a PEM text.

    Args:
        pem: A certificate, or a chain with the leaf first.

    Returns:
        The fingerprint as 64 lower-case hex digits, or None when there is no certificate.
    """
    found = _PEM_CERT.search(pem)
    if found is None:
        return None
    try:
        der = base64.b64decode("".join(found.group("body").split()), validate=True)
    except ValueError:
        return None
    return hashlib.sha256(der).hexdigest()


@dataclass(frozen=True)
class Source:
    """
    The certificate an engine is given, as it was found and checked.

    Attributes:
        fullchain: The certificate and its chain.
        privkey: The key.
        fingerprint: The leaf's SHA-256.
        not_after: When it expires, as openssl prints it.
        expires: The same, as a date.
    """

    fullchain: Path
    privkey: Path
    fingerprint: str
    not_after: str
    expires: datetime


def _parse_openssl_date(text: str) -> datetime:
    """
    Read openssl's ``notAfter`` (``Nov 30 12:00:00 2026 GMT``).

    Args:
        text: The date.

    Returns:
        The instant, in UTC.

    Raises:
        ValueError: When it is not one.
    """
    return datetime.strptime(" ".join(text.split()), "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=timezone.utc
    )


class HostTls:
    """
    Give a host engine a certificate of its own, and keep it current.
    """

    def __init__(
        self,
        manager: BaseDatabaseManager,
        *,
        records: HostTlsRecords | None = None,
        live_dir: Path | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Args:
            manager: The engine's manager (PostgreSQL or MySQL/MariaDB on the host).
            records: The store of records. The process-wide store's when None.
            live_dir: certbot's ``live`` directory. :attr:`CertManager.LIVE_DIR` when None.
            fs: The filesystem seam. The process-wide one when None.
            clock: The current instant. The real one when None.
            sleep: Waits between two looks at the engine.
        """
        self.manager = manager
        self._records = records
        self._live_dir = live_dir
        self._fs = fs
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep

    # ==================== Collaborators ====================

    @property
    def engine(self) -> str:
        """The engine's key."""
        return str(self.manager.ENGINE_NAME)

    @property
    def records(self) -> HostTlsRecords:
        """The store of records."""
        if self._records is None:
            self._records = HostTlsRecords(get_store())
        return self._records

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam every change goes through."""
        return self._fs or get_fs()

    @property
    def live_dir(self) -> Path:
        """certbot's ``live`` directory."""
        return self._live_dir or CertManager.LIVE_DIR

    def directory(self) -> Path:
        """
        Returns:
            Where this engine's copy of the certificate and key is kept.
        """
        return flavours.HOST.at(str(COPY_ROOT)) / self.engine

    def _audit(self, event: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name.
            outcome: ``ok`` or ``failure``.
            **details: Anything else worth keeping. Never a key.
        """
        audit.record(event, target=f"database:{self.engine}", outcome=outcome, details=details)

    # ==================== Looking at a certificate ====================

    def _run(self, argv: list[str], *, input: str | None = None, timeout: int = 60) -> Any:
        """
        Run a command through the manager's runner.

        Args:
            argv: The command.
            input: Standard input.
            timeout: Deadline in seconds.

        Returns:
            The result.
        """
        return self.manager.runner.run(argv, input=input, timeout=timeout)

    def inspect(self, fullchain: Path, privkey: Path, hostname: str) -> Source:
        """
        Check a certificate and its key before an engine is given them.

        Args:
            fullchain: The certificate and its chain.
            privkey: The key.
            hostname: The name clients reach the engine by.

        Returns:
            What was found.

        Raises:
            ValidationError: When a file is missing, the key is not the certificate's, the
                certificate does not cover the name or has expired.
        """
        for path, what in ((fullchain, "certificate"), (privkey, "key")):
            if not path.is_file():
                raise ValidationError(
                    f"The {what} {path} does not exist",
                    details="Order it first (noust cert create), or upload one "
                    "(noust cert upload), then name it here.",
                    field="certificate",
                )
        text = fullchain.read_text(encoding="utf-8", errors="replace")
        fingerprint = leaf_fingerprint(text)
        if fingerprint is None:
            raise ValidationError(
                f"{fullchain} holds no certificate", details="It must be PEM.", field="certificate"
            )
        dates = self._run(["openssl", "x509", "-in", str(fullchain), "-noout", "-enddate"])
        ended = (dates.stdout or "").strip().removeprefix("notAfter=")
        try:
            expires = _parse_openssl_date(ended)
        except ValueError as exc:
            raise ValidationError(
                f"openssl could not read the expiry of {fullchain}",
                details=(dates.stderr or dates.stdout or "").strip() or "No output.",
                field="certificate",
            ) from exc
        if expires <= self._clock():
            raise ValidationError(
                f"The certificate {fullchain} expired on {ended}",
                details="Renew it first; an engine given an expired certificate refuses every "
                "client that checks.",
                field="certificate",
            )
        covers = self._run(
            ["openssl", "x509", "-in", str(fullchain), "-noout", "-checkhost", hostname]
        )
        if "does match" not in (covers.stdout or ""):
            raise ValidationError(
                f"The certificate does not cover {hostname}",
                details="A client checking the name would refuse it. Order or upload a "
                "certificate for the name clients use.",
                field="hostname",
            )
        public = self._run(["openssl", "x509", "-in", str(fullchain), "-noout", "-pubkey"])
        mine = self._run(["openssl", "pkey", "-in", str(privkey), "-pubout"])
        if not public.success or not mine.success or public.stdout.strip() != mine.stdout.strip():
            raise ValidationError(
                "The key is not the certificate's",
                details=f"{privkey} does not match {fullchain}; the engine would refuse to start "
                "TLS with them.",
                field="certificate",
            )
        return Source(fullchain, privkey, fingerprint, ended, expires)

    def source_paths(
        self, hostname: str, source: str, certificate: str | None
    ) -> tuple[Path, Path]:
        """
        Name the files of the certificate an engine is given.

        Args:
            hostname: The name clients reach the engine by.
            source: ``acme`` (the lineage named after the host), ``lineage`` or ``custom``.
            certificate: The lineage's or the upload's name, for the last two.

        Returns:
            The certificate and the key.

        Raises:
            ValidationError: When the source is unknown or needs a name it was not given.
        """
        if source not in ("acme", "lineage", "custom"):
            raise ValidationError(
                f"Unknown certificate source {source!r}",
                details="Use acme, lineage or custom.",
                field="source",
            )
        if source != "acme" and not certificate:
            raise ValidationError(
                f"A {source} certificate is named",
                details="Give the lineage's or the upload's name.",
                field="certificate",
            )
        return served_files(hostname, source, certificate, self.live_dir)

    # ==================== Asking the engine ====================

    def _running(self) -> bool:
        return bool(self.manager.is_running())

    def served_fingerprint(self, hostname: str) -> str | None:
        """
        Ask the engine what certificate it presents.

        A TLS handshake, started the way the engine's protocol starts it, with the name a client
        would send.

        Args:
            hostname: The name sent as SNI.

        Returns:
            The leaf's fingerprint, or None when the engine does not answer a handshake.
        """
        result = self._run(
            [
                "openssl",
                "s_client",
                "-connect",
                f"127.0.0.1:{self.manager.server_port()}",
                "-starttls",
                ENGINES[self.engine]["starttls"],
                "-servername",
                hostname,
            ],
            input="",
            timeout=30,
        )
        return leaf_fingerprint(result.stdout or "")

    def _sql(self, sql: str) -> str:
        """
        Run SQL on the engine as its administrator.

        Args:
            sql: The statement, built from this module's constants.

        Returns:
            Its output.

        Raises:
            DatabaseEngineError: When the engine refuses, with its words.
        """
        ok, output = self.manager._execute_sql(sql)  # type: ignore[attr-defined]
        if not ok:
            raise DatabaseEngineError(
                f"{self.manager.DISPLAY_NAME} refused a statement of the TLS setup",
                details=str(output).strip()[-2000:] or "No output.",
            )
        return str(output).strip()

    def _wait_ready(self) -> None:
        """
        Wait for the engine to answer after a restart.

        Raises:
            DatabaseEngineError: When it does not.
        """
        deadline = self._clock() + timedelta(seconds=READY_TIMEOUT)
        while True:
            ok, _output = self.manager._execute_sql("SELECT 1;")  # type: ignore[attr-defined]
            if ok:
                return
            if self._clock() >= deadline:
                raise DatabaseEngineError(
                    f"{self.manager.DISPLAY_NAME} did not answer after the restart",
                    details=f"Inspect it with: journalctl -u {self.manager.service_unit()} -n 50",
                )
            self._sleep(POLL_SECONDS)

    # ==================== The engine's configuration ====================

    def _include_file(self) -> Path:
        """
        Name the file MySQL or MariaDB reads the certificate's settings from.

        Returns:
            ``98-noust-tls.cnf`` in the flavour's include directory.

        Raises:
            DatabaseEngineError: When there is no include directory.
        """
        return include_directory(bool(getattr(self.manager, "is_mariadb", False))) / (
            "98-noust-tls.cnf"
        )

    def _include_text(self, directory: Path) -> str:
        """
        Args:
            directory: Where the copy is.

        Returns:
            The settings that point MySQL or MariaDB at the copy.
        """
        return (
            "# Generated by Noust: the certificate this server presents (see: noust db tls status).\n"
            "[mysqld]\n"
            f"ssl_cert = {directory / 'fullchain.pem'}\n"
            f"ssl_key = {directory / 'privkey.pem'}\n"
            "tls_version = TLSv1.2,TLSv1.3\n"
        )

    def needs_restart(self) -> bool:
        """
        Returns:
            Whether turning TLS on restarts the engine (MySQL and MariaDB read the paths of
            the certificate only at start).
        """
        return self.engine == "mysql"

    def _activate(self, directory: Path, record_ssl: list[str]) -> None:
        """
        Make the engine use the copy.

        Args:
            directory: Where the copy is.
            record_ssl: Receives what ``ssl`` was, for undoing it (PostgreSQL).

        Raises:
            DatabaseEngineError: When the engine refuses.
        """
        if self.engine == "postgresql":
            was = self._sql("SHOW ssl;")
            record_ssl.append(was)
            if was != "on":
                self._sql("ALTER SYSTEM SET ssl = 'on';")
            for name, value in (
                ("ssl_cert_file", str(directory / "fullchain.pem")),
                ("ssl_key_file", str(directory / "privkey.pem")),
                ("ssl_min_protocol_version", "TLSv1.2"),
            ):
                self._sql(f"ALTER SYSTEM SET {name} = '{value}';")
            self._sql("SELECT pg_reload_conf();")
            return
        include = self._include_file()
        self.fs.write_text(include, self._include_text(directory), mode=0o644)
        self.manager.restart()
        self._wait_ready()

    def _deactivate(self, ssl_was: str | None) -> None:
        """
        Put the engine's settings back.

        Args:
            ssl_was: What PostgreSQL's ``ssl`` was before, if known.

        Raises:
            DatabaseEngineError: When the engine refuses.
        """
        if self.engine == "postgresql":
            for name in ("ssl_cert_file", "ssl_key_file", "ssl_min_protocol_version"):
                self._sql(f"ALTER SYSTEM RESET {name};")
            if ssl_was and ssl_was != "on":
                self._sql("ALTER SYSTEM RESET ssl;")
            self._sql("SELECT pg_reload_conf();")
            return
        self.fs.remove(self._include_file())
        self.manager.restart()
        self._wait_ready()

    def _reload(self) -> None:
        """
        Make the engine load the certificate files again, without a restart.

        Raises:
            DatabaseEngineError: When the engine refuses.
        """
        if self.engine == "postgresql":
            self._sql("SELECT pg_reload_conf();")
        elif getattr(self.manager, "is_mariadb", False):
            self._sql("FLUSH SSL;")
        else:
            self._sql("ALTER INSTANCE RELOAD TLS;")

    # ==================== The copy ====================

    def _install_copy(self, source: Source) -> Path:
        """
        Copy the certificate and key where the engine's account can read them.

        Args:
            source: The checked certificate.

        Returns:
            The directory of the copy.

        Raises:
            DatabaseEngineError: When the copy cannot be made or handed over.
        """
        directory = self.directory()
        if _SAFE_PATH.fullmatch(str(directory)) is None:
            raise DatabaseEngineError(f"{directory} cannot be written into a configuration file")
        account = ENGINES[self.engine]["account"]
        try:
            self.fs.make_dir(directory.parent, mode=0o755)
            self.fs.make_dir(directory, mode=0o750)
            self.fs.set_owner(directory, user=account, group=account, mode=0o750)
            self.fs.write_text(
                directory / "fullchain.pem",
                source.fullchain.read_text(encoding="utf-8"),
                mode=0o644,
            )
            self.fs.write_text(
                directory / "privkey.pem", source.privkey.read_text(encoding="utf-8"), mode=0o600
            )
            for name, mode in (("fullchain.pem", 0o644), ("privkey.pem", 0o600)):
                self.fs.set_owner(directory / name, user=account, group=account, mode=mode)
        except (OSError, KeyError) as exc:
            raise DatabaseEngineError(
                f"Could not copy the certificate for {self.manager.DISPLAY_NAME}",
                details=f"{exc}. The engine's account ({account}) must be able to read {directory}.",
            ) from exc
        return directory

    # ==================== What can be done ====================

    def plan(
        self, hostname: str, *, source: str = "acme", certificate: str | None = None
    ) -> dict[str, Any]:
        """
        Check a certificate for an engine and say what giving it would do. Nothing is changed.

        Args:
            hostname: The name clients reach the engine by.
            source: ``acme``, ``lineage`` or ``custom``.
            certificate: The lineage's or the upload's name.

        Returns:
            The engine, the files, the fingerprint, the expiry and whether it restarts.

        Raises:
            ValidationError: When the engine is not one this serves, the name is not a host
                name, or the certificate fails a check.
            DatabaseEngineError: When the engine is not installed.
        """
        if self.engine not in ENGINES:
            raise ValidationError(
                f"{self.manager.DISPLAY_NAME} cannot be given a certificate by Noust yet",
                details="PostgreSQL, MySQL and MariaDB can.",
                field="engine",
            )
        if self.manager.instance is not None:
            raise ValidationError(
                "An instance has its own certificate",
                details="Use: noust db instance tls NAME. This is for the engine of the server.",
                field="engine",
            )
        valid, why = is_valid_domain(hostname)
        if not valid:
            raise ValidationError(
                f"{hostname!r} is not a host name",
                details=f"{why}. Use a name, such as db.example.com.",
                field="hostname",
            )
        if not self.manager.is_installed() or not self._running():
            raise DatabaseEngineError(
                f"{self.manager.DISPLAY_NAME} is not running",
                details="Start it first; Noust asks it what it serves to prove the certificate "
                "is in use.",
            )
        fullchain, privkey = self.source_paths(hostname, source, certificate)
        found = self.inspect(fullchain, privkey, hostname)
        return {
            "engine": self.engine,
            "hostname": hostname,
            "source": source,
            "certificate": certificate,
            "fullchain": str(fullchain),
            "directory": str(self.directory()),
            "fingerprint": found.fingerprint,
            "not_after": found.not_after,
            "restart": self.needs_restart(),
        }

    def enable(
        self,
        hostname: str,
        *,
        confirmed: bool,
        source: str = "acme",
        certificate: str | None = None,
    ) -> dict[str, Any]:
        """
        Give the engine the certificate, and prove it serves it.

        Args:
            hostname: The name clients reach the engine by.
            confirmed: The operator accepted the engine's restart, where there is one (the
                guard is here, not in the caller that asks).
            source: ``acme``, ``lineage`` or ``custom``.
            certificate: The lineage's or the upload's name.

        Returns:
            The status afterwards.

        Raises:
            ValidationError: When the restart was not confirmed or a check fails.
            DatabaseEngineError: When the engine refuses or serves another certificate. Its
                settings are put back first.
        """
        planned = self.plan(hostname, source=source, certificate=certificate)
        if planned["restart"] and not confirmed:
            raise ValidationError(
                f"Giving {self.manager.DISPLAY_NAME} a certificate restarts it",
                details="It reads the certificate's files only when it starts: every connection "
                "is dropped for a few seconds. Confirm to go ahead.",
                field="confirmed",
            )
        if is_rehearsal():
            return {**planned, "enabled": False}
        try:
            with app_lock(LOCK_KEY, "engine certificate"):
                return self._enable(planned, source, certificate)
        except AppBusyError as busy:
            raise DatabaseEngineError(
                "Another change to an engine's certificate is running",
                details=f"{busy.holder.operation if busy.holder else 'another operation'} holds "
                "the lock; wait for it to finish.",
            ) from busy

    def _enable(
        self, planned: dict[str, Any], source: str, certificate: str | None
    ) -> dict[str, Any]:
        """
        Do what :meth:`enable` planned.

        Args:
            planned: :meth:`plan`'s answer.
            source: The certificate's source.
            certificate: The lineage's or the upload's name.

        Returns:
            The status afterwards.

        Raises:
            DatabaseEngineError: When the engine does not serve the certificate.
        """
        hostname = planned["hostname"]
        previous = self.records.get(self.engine)
        fullchain, privkey = self.source_paths(hostname, source, certificate)
        found = self.inspect(fullchain, privkey, hostname)
        directory = self._install_copy(found)
        ssl_was: list[str] = []
        try:
            self._activate(directory, ssl_was)
            self._prove(hostname, found.fingerprint)
        except DatabaseEngineError as exc:
            self._undo_failed_enable(previous, ssl_was)
            exc.details = (f"{exc.details or ''}\nNoust put the engine's settings back.").strip()
            self._audit("db.tls.enable", outcome="failure", hostname=hostname)
            raise
        self.records.save(
            HostTlsRecord(
                engine=self.engine,
                hostname=hostname,
                source=source,
                certificate=certificate,
                installed_dir=str(directory),
                fingerprint=found.fingerprint,
                not_after=found.not_after,
                ssl_was=(ssl_was[0] if ssl_was else None),
                last_refresh_at=self._clock().isoformat(timespec="seconds"),
                last_status="ok",
            )
        )
        self._install_renewal_hook()
        self._audit(
            "db.tls.enable", hostname=hostname, source=source, fingerprint=found.fingerprint
        )
        self.manager.logger.success(
            f"{self.manager.DISPLAY_NAME} serves the certificate of {hostname}"
        )
        return self.status()

    def _undo_failed_enable(self, previous: HostTlsRecord | None, ssl_was: list[str]) -> None:
        """
        Put the engine back after an activation that did not hold.

        Args:
            previous: What the engine was given before, if anything.
            ssl_was: What ``ssl`` was.
        """
        if previous is not None:
            return
        try:
            self._deactivate(ssl_was[0] if ssl_was else None)
        except DatabaseEngineError as exc:
            self.manager.logger.warning(f"Could not undo the TLS setup: {exc.message}")
        self._remove_copy()

    def _remove_copy(self) -> None:
        """
        Remove the engine's copy of the certificate and key, and the directory that held it.
        """
        directory = self.directory()
        if directory.exists():
            self.fs.remove_tree(directory)

    def _prove(self, hostname: str, expected: str) -> None:
        """
        Check that the engine presents the certificate it was given.

        Args:
            hostname: The name sent as SNI.
            expected: The fingerprint it must present.

        Raises:
            DatabaseEngineError: When it presents another or none.
        """
        served = self.served_fingerprint(hostname)
        if served != expected:
            raise DatabaseEngineError(
                f"{self.manager.DISPLAY_NAME} does not present the certificate it was given",
                details=(
                    f"Expected {expected}, got {served or 'no certificate'}. Look at the engine's "
                    f"log: journalctl -u {self.manager.service_unit()} -n 50"
                ),
            )

    def _install_renewal_hook(self) -> None:
        """
        Make certbot run ``noust db tls refresh`` after every renewal.
        """
        try:
            CertManager(runner=self.manager.runner).setup_auto_renewal()
        except (OSError, ValidationError) as exc:
            self.manager.logger.warning(f"Could not install the renewal hook: {exc}")

    def refresh(self) -> dict[str, Any]:
        """
        Bring each engine's copy up to its source, load it, and prove what is served.

        What certbot's deploy hook runs after a renewal, and what to run after uploading a new
        certificate. An engine whose copy is current is only asked what it serves.

        Returns:
            Per engine: ``refreshed`` (a new copy was made), ``fingerprint`` and ``served``.

        Raises:
            DatabaseEngineError: When an engine refuses the new certificate or presents
                another; the record says so.
        """
        report: dict[str, Any] = {}
        record = self.records.get(self.engine)
        if record is None:
            return report
        try:
            with app_lock(LOCK_KEY, "certificate refresh"):
                report[self.engine] = self._refresh(record)
        except AppBusyError:
            report[self.engine] = {"skipped": "another change to the certificate is running"}
        return report

    def _refresh(self, record: HostTlsRecord) -> dict[str, Any]:
        """
        Refresh one engine.

        Args:
            record: What the engine was given.

        Returns:
            What was done.

        Raises:
            DatabaseEngineError: As :meth:`refresh`.
        """
        if not self._running():
            return {"skipped": f"{self.manager.DISPLAY_NAME} is not running"}
        fullchain, privkey = self.source_paths(record.hostname, record.source, record.certificate)
        try:
            found = self.inspect(fullchain, privkey, record.hostname)
        except ValidationError as exc:
            self.records.record_refresh(
                self.engine, ok=False, error=f"{exc.message} {exc.details or ''}"
            )
            raise DatabaseEngineError(exc.message, details=exc.details) from exc
        refreshed = found.fingerprint != record.fingerprint
        if refreshed:
            self._install_copy(found)
            try:
                self._reload()
            except DatabaseEngineError as exc:
                self.records.record_refresh(self.engine, ok=False, error=str(exc.details or exc))
                raise
        try:
            self._prove(record.hostname, found.fingerprint)
        except DatabaseEngineError as exc:
            self.records.record_refresh(self.engine, ok=False, error=str(exc.details or exc))
            raise
        self.records.record_refresh(
            self.engine, ok=True, fingerprint=found.fingerprint, not_after=found.not_after
        )
        if refreshed:
            self._audit("db.tls.refresh", hostname=record.hostname, fingerprint=found.fingerprint)
            self.manager.logger.success(
                f"{self.manager.DISPLAY_NAME} now serves the renewed certificate of {record.hostname}"
            )
        return {
            "refreshed": refreshed,
            "fingerprint": found.fingerprint,
            "served": found.fingerprint,
        }

    def disable(self, *, confirmed: bool) -> dict[str, Any]:
        """
        Put the engine's settings back and remove the copy.

        Args:
            confirmed: The operator accepted the engine's restart, where there is one.

        Returns:
            ``disabled``.

        Raises:
            ValidationError: When the restart was not confirmed.
            DatabaseEngineError: When the engine refuses.
        """
        record = self.records.get(self.engine)
        if record is None:
            return {"disabled": False, "note": "The engine was not given a certificate."}
        if self.needs_restart() and not confirmed:
            raise ValidationError(
                f"Taking the certificate off {self.manager.DISPLAY_NAME} restarts it",
                details="Confirm to go ahead.",
                field="confirmed",
            )
        if is_rehearsal():
            return {"disabled": False, "note": "Rehearsal: nothing was changed."}
        if self._running():
            self._deactivate(record.ssl_was)
        self._remove_copy()
        self.records.delete(self.engine)
        self._audit("db.tls.disable", hostname=record.hostname)
        return {"disabled": True}

    def status(self) -> dict[str, Any]:
        """
        Say what the engine is given and whether it still matches its source.

        Returns:
            ``enabled``, the record, ``source_changed`` (the source is not the copy any more:
            run a refresh), ``served`` (what the engine presents now), ``days_left`` and
            ``warnings``.
        """
        record = self.records.get(self.engine)
        view: dict[str, Any] = {
            "engine": self.engine,
            "enabled": record is not None,
            "record": record.to_dict() if record else None,
            "warnings": [],
        }
        if record is None:
            return view
        warnings: list[str] = view["warnings"]
        try:
            fullchain, _key = self.source_paths(record.hostname, record.source, record.certificate)
            source_print = leaf_fingerprint(fullchain.read_text(encoding="utf-8"))
        except (ValidationError, OSError):
            source_print = None
            warnings.append("The certificate this was copied from cannot be read.")
        view["source_changed"] = source_print is not None and source_print != record.fingerprint
        if view["source_changed"]:
            warnings.append(
                "The certificate has been renewed or replaced and the engine still serves the "
                "old copy. Run: noust db tls refresh"
            )
        if self._running():
            view["served"] = self.served_fingerprint(record.hostname)
            if view["served"] != record.fingerprint:
                warnings.append("The engine presents a different certificate from the copy.")
        else:
            view["served"] = None
            warnings.append(f"{self.manager.DISPLAY_NAME} is not running.")
        if record.not_after:
            try:
                left = (_parse_openssl_date(record.not_after) - self._clock()).days
            except ValueError:
                left = None
            view["days_left"] = left
            if left is not None and left < EXPIRY_WARNING_DAYS:
                warnings.append(f"The certificate expires in {max(left, 0)} days.")
        if record.last_status == "failed":
            warnings.append(f"The last refresh failed: {record.last_error}")
        return view
