# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Certificates the operator brings: a Cloudflare Origin Certificate, a corporate CA's, a
purchased one.

An application can serve a certificate it did not order (:mod:`noust.deployers.certificates`
assigns it). What is checked before one is kept, because a wrong pair found at the
first reload is an outage:

- it is PEM, one leaf first, and its key is the one for that leaf (the public keys are
  compared, the private key reaching openssl on its standard input, never in an
  argument);
- the key is not encrypted - nothing could type its passphrase when nginx starts;
- it has not expired, and it names something;
- the chain, when there is one, leads to the leaf's issuer (a warning, not a refusal:
  an Origin Certificate chains to an authority no system trusts, and is meant to).

The files are kept in Noust's certificates directory, one directory per certificate
(``fullchain.pem`` and ``privkey.pem``, the key 0600 in a 0700 directory), through the
filesystem seam. A site names them by path, so the web server's own test of the
configuration is the last check. What an operator saved is never rewritten by Noust,
renewal included: there is none; the console and ``noust health`` say how long is left.
"""

from __future__ import annotations

import re
import secrets as token
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from noust.central import require_server_role
from noust.core import audit, paths
from noust.core.certificate_store import CertificateStore, CustomCertificate
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.teams.model import ResourceNotFoundError
from noust.validators.domain import is_valid_domain, is_wildcard, wildcard_covers


class UploadNotFoundError(ResourceNotFoundError, CertificateError):
    """Nothing was uploaded under that name; the API answers 404."""


#: The most bytes of a certificate or a key that is looked at.
MAX_PEM_BYTES = 64 * 1024

#: Names a certificate may be kept under.
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

_BLOCK = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]+)-----\r?\n(?P<body>[A-Za-z0-9+/=\r\n]+?)-----END (?P=label)-----"
)

#: openssl's timestamps: ``Nov 30 12:00:00 2026 GMT``.
_TIME = "%b %d %H:%M:%S %Y"

#: Seconds a call to openssl may take.
_OPENSSL_TIMEOUT = 30

_KEY_LABELS = ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY")


def certificates_root() -> Path:
    """
    Returns:
        The directory uploaded certificates are kept in.
    """
    return paths.config_dir() / "certs"


def certificate_dir(name: str) -> Path:
    """
    Args:
        name: A certificate's name.

    Returns:
        Its directory.
    """
    return certificates_root() / name


def fullchain_path(name: str) -> Path:
    """
    Args:
        name: A certificate's name.

    Returns:
        The certificate and its chain, which a web server serves.
    """
    return certificate_dir(name) / "fullchain.pem"


def key_path(name: str) -> Path:
    """
    Args:
        name: A certificate's name.

    Returns:
        Its private key.
    """
    return certificate_dir(name) / "privkey.pem"


def served_files(domain: str, source: str, name: str | None, live_dir: Path) -> tuple[Path, Path]:
    """
    Name the files a site serves, whichever way its certificate came to be.

    The one mapping, so the certificate manager that checks the files and the web
    server manager that writes their paths into the site cannot disagree.

    Args:
        domain: The application's primary domain.
        source: ``acme``, ``custom`` or ``lineage``.
        name: The upload or the lineage, for the last two.
        live_dir: certbot's ``live`` directory.

    Returns:
        ``(fullchain, private key)``: the upload's, the named lineage's, or the
        lineage under the domain's own name.
    """
    if source == "custom" and name:
        return fullchain_path(name), key_path(name)
    base = live_dir / (name if source == "lineage" and name else domain)
    return base / "fullchain.pem", base / "privkey.pem"


def covers(names: Sequence[str], hostname: str) -> bool:
    """
    Tell whether a certificate's names cover a host.

    Args:
        names: The names on the certificate, wildcards included.
        hostname: The host (or a wildcard name) to be served with it.

    Returns:
        True when one is the host, or a wildcard of one label above it; a wildcard
        host is covered only by the same wildcard.
    """
    host = hostname.lower().strip()
    for name in names:
        candidate = name.lower().strip()
        if candidate == host:
            return True
        if not is_wildcard(host) and is_wildcard(candidate) and wildcard_covers(candidate, host):
            return True
    return False


@dataclass(frozen=True)
class Inspection:
    """
    What openssl says about a certificate.

    Attributes:
        subject: The subject.
        issuer: The issuer.
        names: Subject alternative names (DNS), or the common name when there are none.
        key_type: ``RSA 2048``, ``EC prime256v1``...
        fingerprint: SHA-256, colon separated.
        not_before: Start of validity.
        not_after: End of validity.
    """

    subject: str
    issuer: str
    names: tuple[str, ...]
    key_type: str
    fingerprint: str
    not_before: datetime
    not_after: datetime


def pem_blocks(text: str) -> list[tuple[str, str]]:
    """
    Split PEM text into its blocks.

    Args:
        text: PEM text.

    Returns:
        ``(label, block)`` for each, in order; the block is complete, markers included.
    """
    return [
        (m.group("label"), m.group(0).replace("\r\n", "\n") + "\n") for m in _BLOCK.finditer(text)
    ]


class CustomCertificates:
    """
    Upload, inspect and remove the certificates the operator brings.

    Args:
        store: The record of them; built over the process-wide store by default.
        runner: Runs openssl; the process-wide runner by default.
        fs: Writes the files; the process-wide filesystem by default.
        now: The current time (tests pin it).
    """

    def __init__(
        self,
        store: CertificateStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        now: datetime | None = None,
    ) -> None:
        self.records = store or CertificateStore()
        self._runner = runner
        self._fs = fs
        self._now = now

    @property
    def runner(self) -> CommandRunner:
        """The runner that runs openssl."""
        return self._runner or get_runner()

    def uploaded(self) -> list[CustomCertificate]:
        """
        Returns:
            The uploaded certificates, by name.
        """
        return self.records.list_custom()

    def get(self, name: str) -> CustomCertificate:
        """
        Args:
            name: The certificate's name.

        Returns:
            The record.

        Raises:
            CertificateError: Nothing was uploaded under that name.
        """
        found = self.records.get_custom(name)
        if found is None:
            raise UploadNotFoundError(
                f"No uploaded certificate is called {name}",
                details="List them with: noust cert uploads",
            )
        return found

    def upload(
        self,
        name: str,
        certificate: str,
        private_key: str,
        chain: str = "",
        *,
        uploaded_by: str | None = None,
    ) -> CustomCertificate:
        """
        Check a certificate and its key, and keep them.

        Args:
            name: What to call it: lower case letters, digits, dots, dashes, underscores.
            certificate: PEM: the leaf first, then its chain if it comes in the same text.
            private_key: PEM: the key for the leaf, not encrypted.
            chain: PEM: the intermediates, when they come apart.
            uploaded_by: Who uploaded it.

        Returns:
            The record.

        Raises:
            ValidationError: The name, the PEM or the key is not acceptable, the key is
                not the certificate's, or it has expired.
            CertificateError: openssl could not read it.
        """
        require_server_role("Certificates")
        if not _NAME.fullmatch(name):
            raise ValidationError(
                f"{name!r} cannot be a certificate name",
                details="Use lower case letters, digits, dots, dashes and underscores.",
                field="name",
            )
        blocks = [b for label, b in pem_blocks(certificate) if label == "CERTIFICATE"]
        blocks += [b for label, b in pem_blocks(chain) if label == "CERTIFICATE"]
        if not blocks:
            raise ValidationError(
                "That is not a PEM certificate",
                details="It starts with -----BEGIN CERTIFICATE-----. Convert a DER file with "
                "openssl x509 -inform der -in cert.cer -out cert.pem.",
                field="certificate",
            )
        for text in (certificate, private_key, chain):
            if len(text.encode()) > MAX_PEM_BYTES:
                raise ValidationError(
                    "That file is far too large to be a certificate", field="certificate"
                )
        key = self._key_block(private_key)
        leaf = blocks[0]
        inspection = self._inspect(leaf)
        self._require_valid(inspection)
        self._require_matching_key(leaf, key)
        warning = self._chain_warning(leaf, blocks[1:])
        stored = CustomCertificate(
            name=name,
            names=inspection.names,
            subject=inspection.subject,
            issuer=inspection.issuer,
            key_type=inspection.key_type,
            fingerprint=inspection.fingerprint,
            not_before=inspection.not_before.isoformat(),
            not_after=inspection.not_after.isoformat(),
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        fs = self._fs or get_fs()
        directory = certificate_dir(name)
        fs.make_dir(certificates_root(), mode=0o755, parents=True)
        fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
        fs.write_text(fullchain_path(name), "".join(blocks), mode=0o644)
        fs.write_text(key_path(name), key, mode=SECRET_MODE)
        self.records.save_custom(stored)
        audit.record(
            "certs.upload",
            target=f"cert:{name}",
            details={
                "names": list(inspection.names),
                "not_after": stored.not_after,
                "fingerprint": stored.fingerprint,
                "chain_warning": warning,
            },
        )
        return stored

    def remove(self, name: str) -> None:
        """
        Delete an uploaded certificate and its key.

        Args:
            name: The certificate's name.

        Raises:
            CertificateError: Nothing was uploaded under that name.
            ValidationError: An application still serves it.
        """
        self.get(name)
        users = self.records.domains_using("custom", name)
        if users:
            raise ValidationError(
                f"{name} is served by {', '.join(users)}",
                details="Give them another certificate first: noust cert use APP --acme",
                field="name",
            )
        (self._fs or get_fs()).remove_tree(certificate_dir(name))
        self.records.remove_custom(name)
        audit.record("certs.upload", target=f"cert:{name}", details={"action": "remove"})

    def chain_warning(self, name: str) -> str | None:
        """
        Check the chain of an uploaded certificate again.

        Args:
            name: The certificate's name.

        Returns:
            What is wrong with the chain, or None.
        """
        self.get(name)
        blocks = [
            b for label, b in pem_blocks(fullchain_path(name).read_text()) if label == "CERTIFICATE"
        ]
        return (
            self._chain_warning(blocks[0], blocks[1:])
            if blocks
            else "the certificate file is empty"
        )

    # -- checks --------------------------------------------------------------

    def _key_block(self, text: str) -> str:
        """
        Pick the private key out of PEM text, refusing one that needs a passphrase.

        Args:
            text: PEM text.

        Returns:
            The key block.

        Raises:
            ValidationError: There is none, or it is encrypted.
        """
        blocks = pem_blocks(text)
        if (
            any(label == "ENCRYPTED PRIVATE KEY" for label, _ in blocks)
            or "Proc-Type: 4,ENCRYPTED" in text
        ):
            raise ValidationError(
                "That private key is protected by a passphrase",
                details="Nothing could type it when the web server starts. Remove it first: "
                "openssl pkey -in key.pem -out key-clear.pem",
                field="private_key",
            )
        for label, block in blocks:
            if label in _KEY_LABELS:
                return block
        raise ValidationError(
            "That is not a PEM private key",
            details="It starts with -----BEGIN PRIVATE KEY----- (or RSA PRIVATE KEY, EC PRIVATE KEY).",
            field="private_key",
        )

    def _openssl(self, argv: list[str], pem: str) -> str:
        """
        Run openssl on a PEM given on its standard input.

        Args:
            argv: The command.
            pem: What to give it.

        Returns:
            Its output.

        Raises:
            CertificateError: It failed; its own words are the details.
        """
        result = self.runner.run(argv, input=pem, timeout=_OPENSSL_TIMEOUT)
        if not result.success:
            raise CertificateError(
                "openssl could not read that",
                details=(result.stderr or result.stdout).strip() or "No output.",
                output=(result.stderr or result.stdout).strip() or None,
            )
        return result.stdout

    def _inspect(self, leaf: str) -> Inspection:
        """
        Read what a certificate says about itself.

        Args:
            leaf: The leaf certificate, PEM.

        Returns:
            What it says.

        Raises:
            CertificateError: openssl could not read it.
        """
        output = self._openssl(
            ["openssl", "x509", "-noout", "-text", "-fingerprint", "-sha256"], leaf
        )
        return parse_inspection(output)

    def _require_valid(self, inspection: Inspection) -> None:
        """
        Refuse a certificate that has expired or names nothing.

        Args:
            inspection: What it says.

        Raises:
            ValidationError: It has expired, or names nothing.
        """
        now = self._now or datetime.now(timezone.utc)
        if inspection.not_after <= now:
            raise ValidationError(
                f"That certificate expired on {inspection.not_after:%Y-%m-%d}",
                details="Get a current one from the authority that issued it.",
                field="certificate",
            )
        if not inspection.names:
            raise ValidationError(
                "That certificate names nothing",
                details="It has no subject alternative names and no common name.",
                field="certificate",
            )

    def _require_matching_key(self, leaf: str, key: str) -> None:
        """
        Refuse a key that is not the certificate's.

        Args:
            leaf: The leaf certificate, PEM.
            key: The private key, PEM.

        Raises:
            ValidationError: The public keys differ.
            CertificateError: openssl could not read one of them.
        """
        from_certificate = self._openssl(["openssl", "x509", "-noout", "-pubkey"], leaf).strip()
        from_key = self._openssl(["openssl", "pkey", "-pubout"], key).strip()
        if from_certificate != from_key:
            raise ValidationError(
                "That private key is not the one for that certificate",
                details="The public keys differ. Use the key that was generated with the "
                "certificate request.",
                field="private_key",
            )

    def _chain_warning(self, leaf: str, chain: list[str]) -> str | None:
        """
        Say whether the chain leads to the leaf's issuer, without making it a condition.

        Args:
            leaf: The leaf, PEM.
            chain: The intermediates, PEM.

        Returns:
            A sentence when the chain does not complete, else None.
        """
        if not chain:
            return None
        staging = certificates_root() / f".check-{token.token_hex(6)}"
        fs = self._fs or get_fs()
        try:
            fs.make_dir(staging, mode=SECRET_DIR_MODE, parents=True, exist_ok=False)
            fs.write_text(staging / "leaf.pem", leaf, mode=SECRET_MODE)
            fs.write_text(staging / "chain.pem", "".join(chain), mode=SECRET_MODE)
            result = self.runner.run(
                [
                    "openssl",
                    "verify",
                    "-partial_chain",
                    "-CAfile",
                    str(staging / "chain.pem"),
                    str(staging / "leaf.pem"),
                ],
                timeout=_OPENSSL_TIMEOUT,
            )
        except OSError:
            return None
        finally:
            if staging.exists():
                fs.remove_tree(staging)
        if result.success:
            return None
        return (
            "The chain does not lead to the certificate's issuer: "
            + (result.stderr or result.stdout).strip().splitlines()[-1][:200]
        )


def parse_inspection(output: str) -> Inspection:
    """
    Read the text ``openssl x509 -text -fingerprint -sha256`` prints.

    Args:
        output: openssl's output.

    Returns:
        What it says.

    Raises:
        CertificateError: The output is not a certificate's.
    """

    def field(label: str) -> str:
        match = re.search(rf"^\s*{label}\s*:\s*(.+)", output, re.MULTILINE)
        return match.group(1).strip() if match else ""

    try:
        not_before = datetime.strptime(field("Not Before").replace(" GMT", ""), _TIME).replace(
            tzinfo=timezone.utc
        )
        not_after = datetime.strptime(field("Not After").replace(" GMT", ""), _TIME).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise CertificateError(
            "openssl did not print the validity of that certificate",
            details="Is it a certificate? It must be PEM.",
        ) from None
    subject = field("Subject")
    names = [
        item.strip()[4:].lower()
        for item in re.findall(r"DNS:[^,\s]+", output)
        if is_valid_domain(item.strip()[4:].lower())[0] or is_wildcard(item.strip()[4:])
    ]
    if not names:
        common = re.search(r"CN\s*=\s*([^,/\n]+)", subject)
        if common and (
            is_valid_domain(common.group(1).strip().lower())[0] or is_wildcard(common.group(1))
        ):
            names = [common.group(1).strip().lower()]
    fingerprint = re.search(r"Fingerprint=([0-9A-Fa-f:]+)", output)
    algorithm = field("Public Key Algorithm")
    bits = re.search(r"Public-Key:\s*\((\d+) bit\)", output)
    curve = re.search(r"ASN1 OID:\s*(\S+)", output)
    if "rsa" in algorithm.lower():
        key_type = f"RSA {bits.group(1)}" if bits else "RSA"
    elif "ec" in algorithm.lower() and curve:
        key_type = f"EC {curve.group(1)}"
    else:
        key_type = algorithm or "unknown"
    return Inspection(
        subject=subject,
        issuer=field("Issuer"),
        names=tuple(dict.fromkeys(names)),
        key_type=key_type,
        fingerprint=fingerprint.group(1).upper() if fingerprint else "",
        not_before=not_before,
        not_after=not_after,
    )
