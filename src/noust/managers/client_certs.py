# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Client certificates: an application's site that only answers who holds one of its certificates.

The check happens in the TLS handshake, before a single byte of HTTP: nginx's ``ssl_verify_client``
refuses a visitor with no certificate signed by the site's authority, so a stolen password, a
forgotten account or a bug in the application is not the way in. It is the cheapest strong
authentication there is for an admin interface used by a few people and machines.

- **One authority per application** (``<config>/tls/clients/<app>/``), made on first use with the
  same machinery as the database authority (:class:`~noust.managers.database.tls_authority.TlsAuthority`)
  but a name of its own. A certificate of one application's authority is no key to another's site,
  and none of them is a key to a database: a team that runs an application owns who may reach it.
- **A certificate can only authenticate a client** (extended key usage ``clientAuth``), is signed
  for a year by default and carries a random 128-bit serial. The private key is made, handed over
  once (as a PKCS#12 bundle a browser imports, and as the PEM pair) and not kept: Noust stores the
  serial, the fingerprint and the dates.
- **Revoking is the web server's refusal of a serial**, rendered into the site: a list of revoked
  serials in a ``map`` and a ``403`` for any of them. There is no certificate revocation list to
  fetch, sign or let go stale, and a revocation is in force the moment the site is reloaded.
- The key crosses no command line: openssl is given the password through its environment, and the
  files it needs to make the bundle are in a private, memory-backed directory removed on every way out.

nginx only; Apache is refused where the policy is applied.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit, sealing
from noust.core.client_cert_store import ClientCertificate, ClientCertificateStore
from noust.core.exceptions import DatabaseEngineError, NoustError, ValidationError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import secrets_dir
from noust.core.utils import domain_to_app_name
from noust.managers.database.tls_authority import (
    CertificateInfo,
    TlsAuthority,
    authority_dir,
    check_client_request,
)

#: How long a client certificate is valid when none is asked for.
DEFAULT_DAYS = 365

#: Seconds openssl may take to make a bundle.
OPENSSL_TIMEOUT = 60


@dataclass(frozen=True)
class IssuedClientCertificate:
    """
    A certificate just made, with what the person it is for needs.

    Attributes:
        record: What is kept of it.
        bundle: The PKCS#12 file a browser or a keychain imports.
        password: What opens the bundle. Shown once and kept nowhere.
        certificate: The certificate, PEM.
        key: The private key, PEM. Handed over once and kept nowhere.
        ca: The authority's certificate, PEM.
    """

    record: ClientCertificate
    bundle: bytes
    password: str
    certificate: str
    key: str
    ca: str

    def bundle_base64(self) -> str:
        """
        Returns:
            The bundle as text, for an API answer.
        """
        return base64.b64encode(self.bundle).decode("ascii")


def clients_directory() -> Path:
    """
    Returns:
        The directory the applications' client authorities live in.
    """
    return authority_dir() / "clients"


class ClientCertificates:
    """
    Make, list and revoke the client certificates of applications.

    Args:
        store: The record of them; the process-wide store's by default.
        runner: What openssl runs through; the process-wide runner by default.
        fs: What files are written through; the process-wide filesystem by default.
        root: The directory the authorities live in; ``/etc/noust/tls/clients`` by default.
    """

    def __init__(
        self,
        store: ClientCertificateStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        root: Path | None = None,
    ) -> None:
        self.records = store or ClientCertificateStore()
        self._runner = runner
        self._fs = fs
        self._root = root

    @property
    def runner(self) -> CommandRunner:
        """What openssl runs through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """What files are written through."""
        return self._fs or get_fs()

    def directory(self, app_domain: str) -> Path:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            Where its authority and the certificates it signed are kept.
        """
        return (self._root or clients_directory()) / domain_to_app_name(app_domain)

    def authority(self, app_domain: str) -> TlsAuthority:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            Its authority, with a name that says whose it is.
        """
        return TlsAuthority(
            runner=self._runner,
            fs=self._fs,
            directory=self.directory(app_domain),
            subject=f"Noust client CA for {app_domain}",
        )

    def ca_path(self, app_domain: str) -> Path:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            The file nginx reads to know whose certificates to accept.
        """
        return self.authority(app_domain).ca_path

    def ensure_authority(self, app_domain: str) -> CertificateInfo:
        """
        Make the application's authority when it has none.

        Args:
            app_domain: An application's primary domain.

        Returns:
            Its certificate.

        Raises:
            DatabaseEngineError: openssl could not make it.
        """
        require_server_role("Certificates")
        existed = self.ca_path(app_domain).exists()
        if not existed:
            self.fs.make_dir(self.directory(app_domain), mode=0o755, parents=True)
        found = self.authority(app_domain).ensure_ca()
        if not existed:
            audit.record(
                "certs.client", target=f"app:{app_domain}", details={"action": "authority"}
            )
        return found

    def ca_certificate(self, app_domain: str) -> str | None:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            The authority's certificate, PEM: what a person installs to trust the site's
            certificate chain is not this one (it is for the server to know whose clients to
            accept), but it is public and an operator wants to read it. None before the
            authority exists.
        """
        path = self.ca_path(app_domain)
        return path.read_text() if path.exists() else None

    def list(self, app_domain: str) -> list[ClientCertificate]:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            Its certificates, newest first.
        """
        return self.records.list(app_domain)

    def issue(
        self,
        app_domain: str,
        name: str,
        *,
        days: int = DEFAULT_DAYS,
        actor: str | None = None,
    ) -> IssuedClientCertificate:
        """
        Sign a certificate for a client of an application's site.

        Args:
            app_domain: The application's primary domain.
            name: What the certificate is for (``ana-laptop``): its common name.
            days: How long it is valid.
            actor: Who asks, for the record.

        Returns:
            The certificate and key, and the bundle a browser imports with the password that
            opens it. None of the private parts is kept.

        Raises:
            ValidationError: The name or the validity is not acceptable, or a certificate of
                that name is already valid for this application.
            DatabaseEngineError: openssl failed.
        """
        require_server_role("Certificates")
        check_client_request(name, days)
        taken = [c for c in self.records.list(app_domain) if c.name == name and not c.revoked]
        if taken:
            raise ValidationError(
                f"{app_domain} already has a certificate called {name}",
                details="Give this one another name, or revoke the first.",
                field="name",
            )
        self.ensure_authority(app_domain)
        signed = self.authority(app_domain).issue_client(name, days=days)
        if is_rehearsal():
            raise NoustError("A rehearsal makes no certificate")
        directory = self.directory(app_domain) / "issued"
        self.fs.make_dir(directory, mode=0o755, parents=True)
        path = directory / f"{signed.serial}.crt"
        self.fs.write_text(path, signed.certificate, mode=0o644)
        info = self.authority(app_domain).inspect(path)
        record = self.records.add(
            app_domain,
            name=name,
            serial=signed.serial,
            fingerprint=info.fingerprint,
            not_before=_iso(info.not_before),
            not_after=_iso(info.not_after),
            issued_by=actor,
        )
        password = secrets.token_urlsafe(12)
        bundle = self._bundle(signed.key, signed.certificate, signed.ca, name, password)
        audit.record(
            "certs.client",
            target=f"app:{app_domain}",
            details={"action": "issue", "name": name, "serial": signed.serial, "days": days},
        )
        return IssuedClientCertificate(
            record=record,
            bundle=bundle,
            password=password,
            certificate=signed.certificate,
            key=signed.key,
            ca=signed.ca,
        )

    def revoke(self, app_domain: str, serial: str, *, actor: str | None = None) -> bool:
        """
        Revoke a certificate: from the next time the site is rendered, it is refused.

        Args:
            app_domain: The application's primary domain.
            serial: The certificate's serial.
            actor: Who revokes it.

        Returns:
            True when it was revoked now.

        Raises:
            ValidationError: There is no such certificate for this application.
        """
        require_server_role("Certificates")
        if self.records.get(app_domain, serial) is None:
            raise ValidationError(
                f"{app_domain} has no certificate with the serial {serial}",
                details=f"List them with: noust app policy clients list {app_domain}",
                field="serial",
            )
        revoked = self.records.revoke(app_domain, serial, by=actor)
        if revoked:
            audit.record(
                "certs.client",
                target=f"app:{app_domain}",
                details={"action": "revoke", "serial": serial.upper()},
            )
        return revoked

    def context(self, app_domain: str) -> dict[str, Any]:
        """
        What the site's template needs to ask for a client certificate.

        Args:
            app_domain: The application's primary domain.

        Returns:
            ``ca``: the authority's file; ``revoked``: the serials the site refuses.
        """
        return {
            "ca": str(self.ca_path(app_domain)),
            "revoked": self.records.revoked_serials(app_domain),
        }

    def forget(self, app_domain: str) -> None:
        """
        Let go of an application's authority and records when it is deleted.

        Args:
            app_domain: The application's primary domain.
        """
        self.records.forget(app_domain)
        directory = self.directory(app_domain)
        if directory.exists():
            self.fs.remove_tree(directory)

    # -- internals --------------------------------------------------------------------

    def _bundle(self, key: str, certificate: str, ca: str, name: str, password: str) -> bytes:
        """
        Pack a key and its certificate into a PKCS#12 file a browser imports.

        The algorithms are the older, widely understood ones (3DES and SHA-1 for the MAC): macOS'
        keychain and some phones refuse the modern defaults, and the bundle only protects the key
        between here and the device it is imported on.

        Args:
            key: The private key, PEM.
            certificate: The certificate, PEM.
            ca: The authority's certificate, PEM.
            name: The friendly name in the bundle.
            password: What opens it.

        Returns:
            The file's bytes.

        Raises:
            DatabaseEngineError: openssl failed.
        """
        base = sealing.plaintext_copy_dir(secrets_dir()) / "clientcert"
        sealing.ensure_private_dir(base, self.fs)
        work = base / secrets.token_hex(8)
        try:
            self.fs.make_dir(work, mode=0o700, parents=True)
            key_file, certificate_file, ca_file, out = (
                work / "key",
                work / "cert",
                work / "ca",
                work / "bundle.p12",
            )
            self.fs.write_text(key_file, key, mode=SECRET_MODE)
            self.fs.write_text(certificate_file, certificate, mode=SECRET_MODE)
            self.fs.write_text(ca_file, ca, mode=SECRET_MODE)
            result = self.runner.run(
                [
                    "openssl",
                    "pkcs12",
                    "-export",
                    "-inkey",
                    str(key_file),
                    "-in",
                    str(certificate_file),
                    "-certfile",
                    str(ca_file),
                    "-name",
                    name,
                    "-keypbe",
                    "PBE-SHA1-3DES",
                    "-certpbe",
                    "PBE-SHA1-3DES",
                    "-macalg",
                    "sha1",
                    "-passout",
                    "env:NOUST_P12_PASSWORD",
                    "-out",
                    str(out),
                ],
                env={"NOUST_P12_PASSWORD": password},
                timeout=OPENSSL_TIMEOUT,
            )
            if not result.success or not out.exists():
                raise DatabaseEngineError(
                    "openssl could not make the certificate bundle",
                    details="Check the installation with: openssl version",
                    output=(result.stderr or "").strip() or None,
                )
            return out.read_bytes()
        finally:
            self.fs.remove_tree(work)


def _iso(moment: datetime) -> str:
    """
    Args:
        moment: A moment.

    Returns:
        It as ISO 8601 in UTC.
    """
    return moment.astimezone(timezone.utc).isoformat()
