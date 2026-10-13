# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A private certificate authority for the database engines, and the certificates it signs.

A database engine that serves TLS needs a certificate, and a client that wants to
*verify* the engine (``verify-ca``, ``verify-full``) needs the authority that
signed it. Let's Encrypt is for names on the public internet; an engine on
``127.0.0.1`` or on a container's name has none. So Noust keeps one small
authority of its own, per server:

- ``<config>/tls/ca.crt`` is **public** (0644): it is what is handed to the
  applications, and what ``noust db link --tls verify-full`` points their
  connection strings at.
- ``<config>/tls/ca.key`` is the authority's key: 0600, root's, and read only
  to sign a certificate. Nothing else ever sees it.

The certificates are P-256 (what every engine's OpenSSL speaks), signed for 825
days, and carry the names the engine is reached by (``localhost``,
``127.0.0.1``, the instance's name and its container's). The authority itself is
valid ten years and is made again, with a note in the log, only when it is about
to expire.

Everything goes through ``openssl``, never a library: the runner runs it, the key
is asked for on standard output so that every file is created 0600 through the
filesystem seam with no window at another mode, and nothing secret is on a
command line. The authority's key is read by openssl from its file; it is never
printed.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.pem import pem_block
from noust.core.runner import CommandRunner, get_runner

__all__ = [
    "CA_VALID_DAYS",
    "RENEW_BEFORE_DAYS",
    "SERVER_VALID_DAYS",
    "CertificateInfo",
    "IssuedCertificate",
    "TlsAuthority",
    "authority_dir",
    "ca_certificate_path",
]

#: How long the authority is valid.
CA_VALID_DAYS = 3650

#: How long a certificate it signs is valid.
SERVER_VALID_DAYS = 825

#: The days left below which a certificate is "expiring": the console says so,
#: and renewing one is offered.
RENEW_BEFORE_DAYS = 30

#: What openssl may take to make a key and sign (an EC key is milliseconds; this
#: is for a machine under load).
OPENSSL_TIMEOUT = 60

#: A DNS name a certificate may carry: labels of letters, digits and dashes.
_DNS_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")

#: The lines of ``openssl x509 -noout -subject -issuer -dates -fingerprint``.
_DATE_FORMAT = "%b %d %H:%M:%S %Y %Z"


def authority_dir() -> Path:
    """
    Returns:
        The directory the authority lives in, under Noust's configuration
        directory (``/etc/noust/tls``).
    """
    return paths.config_dir() / "tls"


def ca_certificate_path() -> Path:
    """
    Returns:
        The authority's public certificate: the file an application is pointed
        at to verify an engine.
    """
    return authority_dir() / "ca.crt"


def _key_path() -> Path:
    """
    Returns:
        The authority's private key.
    """
    return authority_dir() / "ca.key"


@dataclass(frozen=True)
class CertificateInfo:
    """
    What a certificate says of itself.

    Attributes:
        path: The file it was read from.
        subject: Its subject (``CN = pg17``).
        issuer: Who signed it.
        names: The names it is valid for.
        not_before: When it begins to be valid.
        not_after: When it stops.
        fingerprint: Its SHA-256 fingerprint, colon-separated.
    """

    path: str
    subject: str
    issuer: str
    names: tuple[str, ...]
    not_before: datetime
    not_after: datetime
    fingerprint: str

    def days_left(self, now: datetime | None = None) -> int:
        """
        Args:
            now: The moment to count from; the present when None.

        Returns:
            Whole days until it expires; negative once it has.
        """
        moment = now or datetime.now(timezone.utc)
        return (self.not_after - moment).days

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """
        Args:
            now: The moment to count from; the present when None.

        Returns:
            Every field, JSON-ready, with the days left and whether it is expiring.
        """
        left = self.days_left(now)
        return {
            "path": self.path,
            "subject": self.subject,
            "issuer": self.issuer,
            "names": list(self.names),
            "not_before": self.not_before.isoformat(timespec="seconds"),
            "not_after": self.not_after.isoformat(timespec="seconds"),
            "days_left": left,
            "expiring": left < RENEW_BEFORE_DAYS,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class IssuedCertificate:
    """
    A certificate just signed, in memory only.

    The caller writes the files, with the owner and mode its engine wants.

    Attributes:
        key: The private key (PEM).
        certificate: The certificate (PEM).
        ca: The authority's certificate (PEM), to hand to clients and to the
            engine's own trust store.
    """

    key: str
    certificate: str
    ca: str


def _alt_name(host: str) -> str:
    """
    Spell a name for ``subjectAltName``.

    Args:
        host: A DNS name or an IP address.

    Returns:
        ``IP:...`` or ``DNS:...``.

    Raises:
        ValidationError: It is neither, so it could not be a name on a certificate.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _DNS_NAME.fullmatch(host):
            raise ValidationError(
                f"{host!r} cannot be a name on a certificate",
                details="Use a DNS name such as db.example.com or an address such as 127.0.0.1.",
                field="names",
            ) from None
        return f"DNS:{host}"
    return f"IP:{host}"


def _parse_time(text: str) -> datetime:
    """
    Args:
        text: ``Oct 13 10:00:00 2027 GMT``, as openssl prints it.

    Returns:
        That moment in UTC.

    Raises:
        ValueError: It is not a date openssl prints.
    """
    return datetime.strptime(text.strip(), _DATE_FORMAT).replace(tzinfo=timezone.utc)


class TlsAuthority:
    """
    Keep the authority and sign certificates with it.

    Args:
        runner: What openssl runs through; the process-wide runner by default.
        fs: What files are written through; the process-wide filesystem by default.
        directory: Where the authority lives; ``/etc/noust/tls`` by default.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        directory: Path | None = None,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._directory = directory

    @property
    def runner(self) -> CommandRunner:
        """What openssl runs through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """What files are written through."""
        return self._fs or get_fs()

    @property
    def directory(self) -> Path:
        """Where the authority lives."""
        return self._directory or authority_dir()

    @property
    def ca_path(self) -> Path:
        """The authority's public certificate."""
        return self.directory / "ca.crt"

    @property
    def key_path(self) -> Path:
        """The authority's private key."""
        return self.directory / "ca.key"

    # ------------------------------------------------------------- inspecting

    def inspect(self, path: Path) -> CertificateInfo:
        """
        Read a certificate.

        Args:
            path: The file.

        Returns:
            What it says.

        Raises:
            DatabaseEngineError: openssl cannot read it, or prints something unexpected.
        """
        result = self.runner.run(
            [
                "openssl",
                "x509",
                "-in",
                str(path),
                "-noout",
                "-subject",
                "-issuer",
                "-startdate",
                "-enddate",
                "-fingerprint",
                "-sha256",
                "-ext",
                "subjectAltName",
            ],
            timeout=OPENSSL_TIMEOUT,
        )
        if not result.success:
            raise DatabaseEngineError(
                f"openssl could not read the certificate {path}",
                details="It may be damaged. Make a new one with: noust db instance renew-tls NAME",
                output=(result.stderr or result.stdout).strip() or None,
            )
        fields: dict[str, str] = {}
        names: list[str] = []
        in_names = False
        for line in result.stdout.splitlines():
            if in_names:
                names.extend(
                    part.strip().split(":", 1)[1]
                    for part in line.split(",")
                    if ":" in part and part.strip().split(":", 1)[0] in ("DNS", "IP Address")
                )
                in_names = False
                continue
            if "Subject Alternative Name" in line:
                in_names = True
                continue
            key, separator, value = line.partition("=")
            if separator:
                fields[key.strip()] = value.strip()
        try:
            return CertificateInfo(
                path=str(path),
                subject=fields["subject"],
                issuer=fields["issuer"],
                names=tuple(names),
                not_before=_parse_time(fields["notBefore"]),
                not_after=_parse_time(fields["notAfter"]),
                fingerprint=fields.get("sha256 Fingerprint", fields.get("SHA256 Fingerprint", "")),
            )
        except (KeyError, ValueError) as exc:
            raise DatabaseEngineError(
                f"openssl printed something unexpected about {path}",
                details="Check the installation with: openssl version",
                output=result.stdout.strip() or None,
            ) from exc

    # ---------------------------------------------------------- the authority

    def existing(self) -> CertificateInfo | None:
        """
        Read the authority without making it.

        Returns:
            Its certificate, or None while no instance has asked for one (it is made
            the first time an instance turns TLS on).

        Raises:
            DatabaseEngineError: The file is there and openssl cannot read it.
        """
        if not self.ca_path.exists():
            return None
        return self.inspect(self.ca_path)

    def ensure_ca(self) -> CertificateInfo:
        """
        Make the authority when there is none, or when it is about to expire.

        Returns:
            Its certificate.

        Raises:
            DatabaseEngineError: openssl could not make it.
        """
        if self.ca_path.exists() and self.key_path.exists():
            try:
                found = self.inspect(self.ca_path)
            except DatabaseEngineError:
                found = None
            if found is not None and found.days_left() > 2 * RENEW_BEFORE_DAYS:
                return found
        return self._mint_ca()

    def _mint_ca(self) -> CertificateInfo:
        """
        Make the authority's key and certificate.

        Returns:
            Its certificate.

        Raises:
            DatabaseEngineError: openssl failed, or printed no usable pair.
        """
        name = socket.gethostname() or "server"
        result = self.runner.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-sha256",
                "-days",
                str(CA_VALID_DAYS),
                "-nodes",
                "-subj",
                f"/CN=Noust database CA on {name}",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout",
                "/dev/stdout",
            ],
            timeout=OPENSSL_TIMEOUT,
        )
        key = pem_block(result.stdout, "PRIVATE KEY")
        certificate = pem_block(result.stdout, "CERTIFICATE")
        if is_rehearsal():
            return self._placeholder(self.ca_path)
        if not result.success or key is None or certificate is None:
            raise DatabaseEngineError(
                "openssl could not make the certificate authority",
                details="Check the installation with: openssl version",
                output=(result.stderr or "").strip() or None,
            )
        self.fs.make_dir(self.directory, mode=0o755)
        self.fs.write_text(self.key_path, key, mode=SECRET_MODE)
        self.fs.write_text(self.ca_path, certificate, mode=0o644)
        return self.inspect(self.ca_path)

    @staticmethod
    def _placeholder(path: Path) -> CertificateInfo:
        """
        Describe a certificate a rehearsal did not make.

        Args:
            path: Where it would have been written.

        Returns:
            A certificate valid for its full term, from now.
        """
        now = datetime.now(timezone.utc)
        return CertificateInfo(
            path=str(path),
            subject="CN = (dry run)",
            issuer="CN = (dry run)",
            names=(),
            not_before=now,
            not_after=now.replace(year=now.year + 10),
            fingerprint="",
        )

    # ------------------------------------------------------------ certificates

    def issue(self, common_name: str, names: Sequence[str]) -> IssuedCertificate:
        """
        Sign a certificate for an engine.

        Args:
            common_name: Its subject's name (the instance's).
            names: Every name the engine is reached by.

        Returns:
            The key and the certificate, and the authority's certificate.

        Raises:
            ValidationError: A name cannot be on a certificate.
            DatabaseEngineError: openssl failed, or printed no usable pair.
        """
        if not names:
            raise ValidationError(
                "A certificate needs at least one name",
                details="Give the names the engine is reached by.",
                field="names",
            )
        alt = ",".join(_alt_name(host) for host in names)
        if not _DNS_NAME.fullmatch(common_name):
            raise ValidationError(
                f"{common_name!r} cannot be the name of a certificate",
                details="Use letters, digits, dots and dashes.",
                field="name",
            )
        self.ensure_ca()
        if is_rehearsal():
            return IssuedCertificate("(dry run)\n", "(dry run)\n", "(dry run)\n")

        made = self.runner.run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-sha256",
                "-nodes",
                "-subj",
                f"/CN={common_name}",
                "-keyout",
                "/dev/stdout",
            ],
            timeout=OPENSSL_TIMEOUT,
        )
        key = pem_block(made.stdout, "PRIVATE KEY")
        request = pem_block(made.stdout, "CERTIFICATE REQUEST")
        if not made.success or key is None or request is None:
            raise DatabaseEngineError(
                f"openssl could not make a key for {common_name}",
                details="Check the installation with: openssl version",
                output=(made.stderr or "").strip() or None,
            )

        extensions = self.directory / f".{secrets.token_hex(8)}.ext"
        self.fs.write_text(
            extensions,
            "\n".join(
                [
                    "basicConstraints=critical,CA:FALSE",
                    "keyUsage=critical,digitalSignature",
                    "extendedKeyUsage=serverAuth,clientAuth",
                    f"subjectAltName={alt}",
                    "",
                ]
            ),
            mode=SECRET_MODE,
        )
        try:
            signed = self.runner.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    "/dev/stdin",
                    "-CA",
                    str(self.ca_path),
                    "-CAkey",
                    str(self.key_path),
                    "-set_serial",
                    f"0x{secrets.token_hex(16)}",
                    "-days",
                    str(SERVER_VALID_DAYS),
                    "-sha256",
                    "-extfile",
                    str(extensions),
                ],
                input=request,
                timeout=OPENSSL_TIMEOUT,
            )
        finally:
            self.fs.remove(extensions)
        certificate = pem_block(signed.stdout, "CERTIFICATE")
        if not signed.success or certificate is None:
            raise DatabaseEngineError(
                f"openssl could not sign the certificate for {common_name}",
                details="The authority's key may be damaged: remove /etc/noust/tls to start again "
                "(every engine's certificate is then made again).",
                output=(signed.stderr or "").strip() or None,
            )
        authority = self.ca_path.read_text()
        return IssuedCertificate(key=key, certificate=certificate, ca=authority)

    def make_directory(self, path: Path) -> None:
        """
        Make a directory for an engine's files, readable by whoever it is made for.

        Args:
            path: The directory.
        """
        self.fs.make_dir(path, mode=SECRET_DIR_MODE)
