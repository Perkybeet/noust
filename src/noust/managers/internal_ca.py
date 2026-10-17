# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A certificate authority of this server, for names that only resolve inside a private network.

Let's Encrypt signs a name it can prove, and a name that only resolves on a tailnet, a VPN or a
home network has no such proof: ``wiki.home.arpa`` and ``grafana.lan`` are not on the public
internet. The usual answers are a browser warning for ever, or a certificate authority of your own.
This is the second, made small enough to be safe:

- **One authority per server** (``<config>/ca/``): ``ca.crt`` is public, ``ca.key`` is root's alone
  (0600 in a 0700 directory) and is read only by openssl, to sign. It is made when the operator asks
  (:meth:`InternalCA.init`) and **never again by itself**: a new root would have to be trusted again
  on every device, so the authority that is about to expire is a warning, not a surprise.
- **Name constraints, in the certificate.** The authority can only ever sign the suffixes and the
  private address ranges it was made with (by default ``lan``, ``internal``, ``home.arpa``,
  ``local``, ``localhost``, ``test`` and the private, tailnet and loopback ranges). The extension is
  critical, so every client that trusts the root enforces it: a leaked key cannot be used to
  impersonate ``bank.com`` to the people who installed it. Noust refuses a name outside the
  constraints before openssl is asked, and refuses to make an authority that would sign a public
  top-level domain or a public address range.
- **Leaves are uploaded certificates.** What it signs (P-256, ``serverAuth`` only, 90 days by
  default, a random 128-bit serial) is kept as an upload named ``internal-<name>``, so everything
  that serves, lists, warns about and backs up an uploaded certificate does the same for these; and
  because the authority is ours they are renewed by the network maintenance pass, thirty days before
  they expire, and the sites that serve them reload.
- **Trust is the operator's to install**, per device, and :mod:`noust.managers.ca_trust` says how
  on each system. The root certificate is public and can be downloaded; the key never leaves.

Everything goes through openssl by the runner, as for the database authority, which this builds on
(:class:`~noust.managers.database.tls_authority.TlsAuthority`).
"""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit, paths
from noust.core.certificate_store import CustomCertificate
from noust.core.exceptions import CertificateConflictError, CertificateError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.teams.model import ResourceNotFoundError
from noust.managers.custom_certificates import CustomCertificates
from noust.managers.database.tls_authority import (
    CA_VALID_DAYS,
    OPENSSL_TIMEOUT,
    RENEW_BEFORE_DAYS,
    CertificateInfo,
    TlsAuthority,
)
from noust.managers.private_networks import is_private_range
from noust.validators.domain import is_valid_domain, is_wildcard

__all__ = [
    "DEFAULT_DNS",
    "DEFAULT_NETWORKS",
    "LEAF_DAYS",
    "MAX_LEAF_DAYS",
    "UPLOADED_BY",
    "UPLOAD_PREFIX",
    "CaStatus",
    "InternalAuthority",
    "InternalCA",
    "IssuedName",
    "NoAuthorityError",
    "ca_directory",
    "parse_name_constraints",
]

#: How long a certificate it signs is valid unless asked otherwise.
LEAF_DAYS = 90

#: The longest a certificate is made for. A browser distrusts a longer-lived certificate from a
#: public authority (398 days), a private one is not held to it, and a year is already a long
#: time for a key that nothing renews by hand.
MAX_LEAF_DAYS = 397

#: The days left at which the authority itself is warned about.
CA_WARN_DAYS = 180

#: The most names one certificate carries.
MAX_NAMES = 20

#: What an uploaded certificate made here is called, and who it says uploaded it.
UPLOAD_PREFIX = "internal-"
UPLOADED_BY = "internal-ca"

#: The suffixes a name can have and only be private (RFC 6761, 6762, 8375 and the ones everybody
#: uses at home), and the ranges an address can be in and only be private.
DEFAULT_DNS: tuple[str, ...] = ("lan", "internal", "home.arpa", "local", "localhost", "test")
DEFAULT_NETWORKS: tuple[str, ...] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "fc00::/7",
    "::1/128",
)

#: One-label suffixes that are private. Any other single label is a top-level domain (``com``):
#: constraining an authority to it would let it sign for the whole TLD.
_PRIVATE_LABELS = frozenset(
    {"lan", "internal", "local", "localhost", "test", "intranet", "corp", "home", "private"}
)

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_SLUG = re.compile(r"[^a-z0-9]+")


class NoAuthorityError(ResourceNotFoundError, CertificateError):
    """This server has no internal certificate authority; the API answers 404."""


def ca_directory() -> Path:
    """
    Returns:
        The directory the authority lives in (``/etc/noust/ca``): apart from the database
        authority's (``tls/``), which signs for engines and must not sign for web sites.
    """
    return paths.config_dir() / "ca"


# -- names ------------------------------------------------------------------------------------


def normal_suffix(value: str) -> str:
    """
    Check a DNS suffix the authority may be limited to, and give its canonical spelling.

    Args:
        value: ``lan``, ``.home.arpa``, ``corp.example.com``.

    Returns:
        Lower case, without a leading dot or ``*.``.

    Raises:
        ValidationError: It is not a name, or it is a top-level domain that is not private.
    """
    suffix = value.strip().lower().removeprefix("*").removeprefix(".")
    labels = suffix.split(".")
    if not suffix or not all(_LABEL.fullmatch(label) for label in labels):
        raise ValidationError(
            f"{value!r} cannot be a name the authority signs for",
            details="Use a domain such as home.arpa or corp.example.com.",
            field="permit",
        )
    if len(labels) == 1 and suffix not in _PRIVATE_LABELS:
        raise ValidationError(
            f"{suffix!r} is a top-level domain: the authority would sign for every name under it",
            details="Name a domain you own or one that is private on purpose "
            f"({', '.join(sorted(_PRIVATE_LABELS))}).",
            field="permit",
        )
    return suffix


def normal_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """
    Check an address range the authority may be limited to.

    Args:
        value: ``10.0.0.0/8``, ``fd7a:115c:a1e0::/48``, a single address.

    Returns:
        The network.

    Raises:
        ValidationError: It is not one, or it includes addresses on the public internet.
    """
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        raise ValidationError(
            f"{value!r} is not an address range",
            details="Use 10.0.0.0/8 or fd00::/8.",
            field="permit",
        ) from None
    if not is_private_range(str(network)):
        raise ValidationError(
            f"{network} includes addresses on the public internet",
            details="The authority only signs for private ranges: 10.0.0.0/8, 172.16.0.0/12, "
            "192.168.0.0/16, 100.64.0.0/10 (a tailnet), fc00::/7.",
            field="permit",
        )
    return network


def _constraint(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> str:
    """
    Args:
        network: An address range.

    Returns:
        How openssl spells it in a name constraint: ``IP:10.0.0.0/255.0.0.0``.
    """
    return f"IP:{network.network_address}/{network.netmask}"


def parse_name_constraints(output: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Read what ``openssl x509 -noout -ext nameConstraints`` prints.

    Args:
        output: Its output.

    Returns:
        The permitted DNS suffixes and the permitted address ranges (``10.0.0.0/8``).
    """
    dns: list[str] = []
    networks: list[str] = []
    permitted = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Permitted"):
            permitted = True
            continue
        if stripped.startswith("Excluded"):
            permitted = False
            continue
        if not permitted:
            continue
        if stripped.startswith("DNS:"):
            dns.append(stripped[4:].strip().lower().removeprefix("."))
        elif stripped.startswith("IP:"):
            address, _, mask = stripped[3:].strip().partition("/")
            network = _network_of(address, mask)
            if network is not None:
                networks.append(str(network))
    return tuple(dns), tuple(networks)


def _network_of(address: str, mask: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """
    Read a range the way openssl prints it in a name constraint.

    openssl prints an address with its netmask, an IPv6 one expanded
    (``FC00:0:0:0:0:0:0:0/FE00:0:0:0:0:0:0:0``), which is a form ``ipaddress`` only accepts for
    IPv4: the mask is turned into a prefix length here.

    Args:
        address: The network address.
        mask: The netmask.

    Returns:
        The network, or None when it is not one (an address, or a mask that is not contiguous).
    """
    try:
        base = ipaddress.ip_address(address)
        bits = int(ipaddress.ip_address(mask))
    except ValueError:
        return None
    width = base.max_prefixlen
    prefix = bin(bits).count("1")
    if bits != ((1 << prefix) - 1) << (width - prefix):
        return None
    return ipaddress.ip_network((base, prefix), strict=False)


def is_permitted(host: str, dns: Sequence[str], networks: Sequence[str]) -> bool:
    """
    Say whether a name is one an authority with these constraints can sign.

    Args:
        host: A DNS name (a wildcard counts by its base) or an IP address.
        dns: The permitted suffixes.
        networks: The permitted address ranges.

    Returns:
        True when the name is a permitted suffix or below one, or the address is in a range.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        base = host.lower().removeprefix("*.")
        return any(base == suffix or base.endswith(f".{suffix}") for suffix in dns)
    return any(
        address in network
        for text in networks
        if (network := ipaddress.ip_network(text)).version == address.version
    )


# -- the authority ------------------------------------------------------------------------------


class InternalAuthority(TlsAuthority):
    """
    The database authority's machinery with an authority of its own: its own directory, a name
    that says what it is for, name constraints, and a root that is never made twice.

    Args:
        subject: What the root is called in a device's list of authorities; needed to make it.
        permit_dns: The suffixes it will sign for, when it is made.
        permit_networks: The address ranges it will sign for, when it is made.
        days: How long the root is valid, when it is made.
        runner: What openssl runs through; the process-wide runner by default.
        fs: What files are written through; the process-wide filesystem by default.
        directory: Where it lives; ``/etc/noust/ca`` by default.
    """

    def __init__(
        self,
        *,
        subject: str | None = None,
        permit_dns: Sequence[str] = DEFAULT_DNS,
        permit_networks: Sequence[str] = DEFAULT_NETWORKS,
        days: int = CA_VALID_DAYS,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        directory: Path | None = None,
    ) -> None:
        constraints = [f"permitted;DNS:{suffix}" for suffix in permit_dns] + [
            f"permitted;{_constraint(ipaddress.ip_network(text))}" for text in permit_networks
        ]
        super().__init__(
            runner=runner,
            fs=fs,
            directory=directory or ca_directory(),
            subject=subject,
            ca_days=days,
            ca_extensions=(
                ("nameConstraints=critical," + ",".join(constraints),) if constraints else ()
            ),
        )

    def mint(self) -> CertificateInfo:
        """
        Make the root: its key, readable by root alone, and its certificate with the constraints.

        Returns:
            Its certificate.

        Raises:
            CertificateError: There is one already, or it has no name to be called by.
            DatabaseEngineError: openssl failed.
        """
        if self.ca_path.exists() or self.key_path.exists():
            raise CertificateConflictError(
                "This server already has an internal certificate authority",
                details=f"Its files are in {self.directory}. Making another means trusting the "
                "new root again on every device: remove the old one first "
                "(noust cert ca remove).",
            )
        if not self._subject:
            raise CertificateError("The authority has no name to be made with")
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE)
        return self._mint_ca()

    def ensure_ca(self) -> CertificateInfo:
        """
        Read the root; never make it.

        Returns:
            Its certificate.

        Raises:
            CertificateError: There is none.
        """
        found = self.existing()
        if found is None or not self.key_path.exists():
            raise NoAuthorityError(
                "This server has no internal certificate authority",
                details="Make it: noust cert ca init",
            )
        return found

    def constraints(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """
        Returns:
            What the root's own certificate limits it to: the DNS suffixes and the address
            ranges. The one place the limits are read from is the certificate, which every
            client enforces.

        Raises:
            CertificateError: openssl could not read it.
        """
        result = self.runner.run(
            ["openssl", "x509", "-in", str(self.ca_path), "-noout", "-ext", "nameConstraints"],
            timeout=OPENSSL_TIMEOUT,
        )
        if not result.success:
            raise CertificateError(
                "openssl could not read the limits of the authority's certificate",
                details="Check the installation with: openssl version",
                output=(result.stderr or result.stdout).strip() or None,
            )
        return parse_name_constraints(result.stdout)


@dataclass(frozen=True)
class IssuedName:
    """
    A certificate this authority signed, as the operator reads it.

    Attributes:
        name: What it is kept under (``internal-wiki-home-arpa``).
        names: The DNS names on it (an address on the certificate is not a name the web server
            routes by, so it is not listed).
        not_after: When it stops being valid, ISO 8601.
        days_left: Whole days until then; negative once it has.
        fingerprint: Its SHA-256.
        apps: The applications that serve it.
        expiring: It is within the renewal window.
    """

    name: str
    names: tuple[str, ...]
    not_after: str
    days_left: int
    fingerprint: str
    apps: tuple[str, ...] = ()
    expiring: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "names": list(self.names),
            "not_after": self.not_after,
            "days_left": self.days_left,
            "fingerprint": self.fingerprint,
            "apps": list(self.apps),
            "expiring": self.expiring,
        }


@dataclass(frozen=True)
class CaStatus:
    """
    The authority as the operator reads it.

    Attributes:
        subject: What it is called.
        fingerprint: The root's SHA-256, which a device compares before it trusts it.
        not_before: When it begins to be valid.
        not_after: When it expires.
        days_left: Whole days until then.
        permitted_dns: The suffixes it can sign for.
        permitted_networks: The address ranges it can sign for.
        issued: How many certificates it signed that are kept.
        warnings: Sentences about its state (it expires within six months).
    """

    subject: str
    fingerprint: str
    not_before: str
    not_after: str
    days_left: int
    permitted_dns: tuple[str, ...]
    permitted_networks: tuple[str, ...]
    issued: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready. The key is not one of them.
        """
        return {
            "subject": self.subject,
            "fingerprint": self.fingerprint,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "days_left": self.days_left,
            "permitted_dns": list(self.permitted_dns),
            "permitted_networks": list(self.permitted_networks),
            "issued": self.issued,
            "warnings": list(self.warnings),
        }


class InternalCA:
    """
    Make the authority, sign certificates with it, and keep them renewed.

    Args:
        authority: The authority; the server's by default.
        uploads: Where what it signs is kept; the uploaded certificates by default.
        runner: What openssl runs through; the process-wide runner by default.
        fs: What files are written through; the process-wide filesystem by default.
        now: The current time (tests pin it).
    """

    def __init__(
        self,
        authority: InternalAuthority | None = None,
        uploads: CustomCertificates | None = None,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        now: datetime | None = None,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._authority = authority
        self.uploads = uploads or CustomCertificates(runner=runner, fs=fs)
        self._now = now

    @property
    def runner(self) -> CommandRunner:
        """What openssl runs through."""
        return self._runner or get_runner()

    @property
    def authority(self) -> InternalAuthority:
        """The authority; built over the server's directory when none was given."""
        if self._authority is None:
            self._authority = InternalAuthority(runner=self._runner, fs=self._fs)
        return self._authority

    def _moment(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    # -- the authority ---------------------------------------------------------------------

    def exists(self) -> bool:
        """
        Returns:
            Whether this server has an authority.
        """
        return self.authority.ca_path.exists() and self.authority.key_path.exists()

    def init(
        self,
        *,
        permit_dns: Sequence[str] = (),
        permit_networks: Sequence[str] = (),
        only: bool = False,
        days: int = CA_VALID_DAYS,
        name: str | None = None,
    ) -> CaStatus:
        """
        Make the authority.

        Args:
            permit_dns: More suffixes to sign for, besides the private defaults.
            permit_networks: More address ranges, besides the private defaults.
            only: Sign for exactly these and not for the defaults.
            days: How long the root is valid.
            name: What it is called on a device; ``Noust internal CA (<hostname>)`` by default.

        Returns:
            Its state.

        Raises:
            ValidationError: A suffix or range is not acceptable, or ``days`` is out of range.
            CertificateError: There is an authority already.
            DatabaseEngineError: openssl failed.
        """
        require_server_role("Certificates")
        if not 30 <= days <= 7300:
            raise ValidationError("The authority is valid for 30 to 7300 days", field="days")
        dns = tuple(dict.fromkeys(normal_suffix(item) for item in permit_dns))
        networks = tuple(
            dict.fromkeys(str(normal_network(item)) for item in permit_networks),
        )
        if not only:
            dns = tuple(dict.fromkeys([*DEFAULT_DNS, *dns]))
            networks = tuple(dict.fromkeys([*DEFAULT_NETWORKS, *networks]))
        if not dns and not networks:
            raise ValidationError(
                "Name at least one suffix or address range the authority signs for",
                field="permit",
            )
        label = (name or "").strip() or f"Noust internal CA ({_hostname()})"
        if not re.fullmatch(r"[A-Za-z0-9 ._()-]{3,64}", label):
            raise ValidationError(
                f"{label!r} cannot be the name of an authority",
                details="Use letters, digits, spaces and . _ ( ) -, up to 64.",
                field="name",
            )
        minted = InternalAuthority(
            subject=label,
            permit_dns=dns,
            permit_networks=networks,
            days=days,
            runner=self._runner,
            fs=self._fs,
            directory=self.authority.directory,
        )
        minted.mint()
        self._authority = minted
        audit.record(
            "certs.ca",
            target="ca:internal",
            details={"action": "init", "permit_dns": list(dns), "permit_networks": list(networks)},
        )
        made = self.status()
        if made is None:
            raise CertificateError("The authority was made but cannot be read back")
        return made

    def status(self) -> CaStatus | None:
        """
        Returns:
            The authority's state, or None when this server has none.

        Raises:
            DatabaseEngineError: openssl could not read its certificate.
        """
        if not self.exists():
            return None
        info = self.authority.inspect(self.authority.ca_path)
        dns, networks = self.authority.constraints()
        left = info.days_left(self._moment())
        warnings: list[str] = []
        if left < CA_WARN_DAYS:
            warnings.append(
                f"The authority expires in {left} days. A new root has to be trusted again on "
                "every device: plan it now (noust cert ca remove, then noust cert ca init)."
            )
        return CaStatus(
            subject=info.subject,
            fingerprint=info.fingerprint,
            not_before=info.not_before.isoformat(timespec="seconds"),
            not_after=info.not_after.isoformat(timespec="seconds"),
            days_left=left,
            permitted_dns=dns,
            permitted_networks=networks,
            issued=len(self.issued()),
            warnings=tuple(warnings),
        )

    def root_pem(self) -> str:
        """
        Returns:
            The root certificate, PEM: public, what a device installs.

        Raises:
            CertificateError: There is no authority.
        """
        self.authority.ensure_ca()
        return self.authority.ca_path.read_text()

    def root_der(self) -> bytes:
        """
        Returns:
            The root certificate, DER: the form Windows, Android and Java import.

        Raises:
            CertificateError: There is no authority, or its file is not a certificate.
        """
        pem = self.root_pem()
        body = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
        try:
            return base64.b64decode(body, validate=True)
        except ValueError as exc:
            raise CertificateError("The authority's certificate is damaged") from exc

    def remove(self, *, force: bool = False) -> None:
        """
        Delete the authority and its key.

        Args:
            force: Go on although certificates it signed are still kept (they stop being
                renewable, and devices that trusted the root no longer have an authority).

        Raises:
            CertificateError: There is none.
            ValidationError: Certificates it signed are kept and ``force`` is False.
        """
        require_server_role("Certificates")
        self.authority.ensure_ca()
        left = self.issued()
        if left and not force:
            raise ValidationError(
                f"{len(left)} certificate(s) signed by it are still kept: "
                + ", ".join(item.name for item in left),
                details="Remove them (noust cert remove-upload NAME) or accept that they are "
                "orphans (--force).",
                field="force",
            )
        (self._fs or get_fs()).remove_tree(self.authority.directory)
        audit.record(
            "certs.ca", target="ca:internal", details={"action": "remove", "orphaned": len(left)}
        )

    # -- what it signs ---------------------------------------------------------------------

    def issue(
        self, label: str, names: Sequence[str], *, days: int = LEAF_DAYS
    ) -> CustomCertificate:
        """
        Sign a certificate for some names and keep it as an upload named ``internal-<label>``.

        Args:
            label: What it is for (``wiki``); lower case letters, digits and dashes.
            names: DNS names (a wildcard is one label) and addresses, at least one DNS name.
            days: How long it is valid.

        Returns:
            The record of the upload.

        Raises:
            ValidationError: A name is outside what the authority signs for, the label or
                the days are not acceptable, or the web server's name rules refuse a name.
            CertificateError: There is no authority, or it has expired.
            DatabaseEngineError: openssl failed.
        """
        require_server_role("Certificates")
        slug = _SLUG.sub("-", label.lower()).strip("-")[:48]
        if not slug:
            raise ValidationError(
                f"{label!r} cannot be a certificate's name",
                details="Use letters and digits.",
                field="name",
            )
        info = self.authority.ensure_ca()
        left = info.days_left(self._moment())
        if left <= 1:
            raise CertificateError(
                "The authority has expired",
                details="Make a new one (noust cert ca remove, noust cert ca init) and trust it again.",
            )
        if not 1 <= days <= MAX_LEAF_DAYS:
            raise ValidationError(
                f"A certificate is valid for 1 to {MAX_LEAF_DAYS} days", field="days"
            )
        cleaned = self._names(names)
        dns, networks = self.authority.constraints()
        outside = [host for host in cleaned if not is_permitted(host, dns, networks)]
        if outside:
            raise ValidationError(
                f"{', '.join(outside)} is outside what this authority signs for",
                details=f"It signs for {', '.join([*dns, *networks])}. To sign others, make the "
                "authority again with --permit (and trust the new root).",
                field="names",
            )
        upload_name = f"{UPLOAD_PREFIX}{slug}"
        first = next(host for host in cleaned if _is_name(host))
        issued = self.authority.issue(
            first.removeprefix("*."), cleaned, days=min(days, max(left - 1, 1)), web=True
        )
        stored = self.uploads.upload(
            upload_name, issued.certificate, issued.key, uploaded_by=UPLOADED_BY
        )
        audit.record(
            "certs.ca",
            target=f"cert:{upload_name}",
            details={"action": "issue", "names": cleaned, "days": days},
        )
        return stored

    def issued(self) -> list[IssuedName]:
        """
        Returns:
            The certificates this authority signed that are kept, soonest to expire first.
        """
        found: list[IssuedName] = []
        moment = self._moment()
        for record in self.uploads.uploaded():
            if record.uploaded_by != UPLOADED_BY:
                continue
            expires = _moment_of(record.not_after, moment)
            left = (expires - moment).days
            found.append(
                IssuedName(
                    name=record.name,
                    names=tuple(record.names),
                    not_after=record.not_after or "",
                    days_left=left,
                    fingerprint=record.fingerprint or "",
                    apps=tuple(self.uploads.records.domains_using("custom", record.name)),
                    expiring=left < _renew_before(_moment_of(record.not_before, expires), expires),
                )
            )
        return sorted(found, key=lambda item: item.days_left)

    def renew_due(self) -> list[IssuedName]:
        """
        Returns:
            The certificates within the renewal window.
        """
        return [item for item in self.issued() if item.expiring]

    def renew(self, name: str) -> CustomCertificate:
        """
        Sign a certificate again for the names it has, for as long as it was made for.

        Args:
            name: Its upload name.

        Returns:
            The record of the new upload.

        Raises:
            CertificateError: There is no authority, or nothing signed by it is kept under that
                name.
            ValidationError: A name is no longer something the authority signs for.
            DatabaseEngineError: openssl failed.
        """
        require_server_role("Certificates")
        record = self.uploads.get(name)
        if record.uploaded_by != UPLOADED_BY or not name.startswith(UPLOAD_PREFIX):
            raise CertificateError(
                f"{name} was not signed by the internal authority",
                details="List what was: noust cert ca list",
            )
        from noust.managers.custom_certificates import fullchain_path

        current = self.authority.inspect(fullchain_path(name))
        days = max((current.not_after - current.not_before).days, 1)
        return self.issue(
            name.removeprefix(UPLOAD_PREFIX),
            current.names or record.names,
            days=min(days, MAX_LEAF_DAYS),
        )

    # -- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _names(names: Sequence[str]) -> list[str]:
        """
        Check the names for a certificate.

        Args:
            names: DNS names and addresses.

        Returns:
            Them lower case, without repeats.

        Raises:
            ValidationError: There are none or too many, one is neither a name nor an address,
                or none is a DNS name.
        """
        cleaned: list[str] = []
        for raw in names:
            host = raw.strip().lower().rstrip(".")
            if not host:
                continue
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                if not (is_valid_domain(host)[0] or is_wildcard(host)):
                    raise ValidationError(
                        f"{raw!r} cannot be a name on a certificate",
                        details="Use a DNS name such as wiki.home.arpa, *.corp.lan or an address.",
                        field="names",
                    ) from None
            if host not in cleaned:
                cleaned.append(host)
        if not cleaned or len(cleaned) > MAX_NAMES:
            raise ValidationError(
                f"A certificate carries 1 to {MAX_NAMES} names, not {len(cleaned)}", field="names"
            )
        if not any(_is_name(host) for host in cleaned):
            raise ValidationError(
                "A certificate needs at least one DNS name",
                details="An address can be on it, but the web server serves a certificate by a name.",
                field="names",
            )
        return cleaned


def _moment_of(text: str | None, default: datetime) -> datetime:
    """
    Args:
        text: An ISO 8601 moment as the record keeps it, or None.
        default: What to answer when there is none or it cannot be read.

    Returns:
        That moment, in UTC.
    """
    try:
        moment = datetime.fromisoformat(text or "")
    except ValueError:
        return default
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _renew_before(begins: datetime, ends: datetime) -> float:
    """
    The days left at which a certificate is made again: thirty, or a third of its term when it
    was made for less than three months, so that a short one is not signed again every hour.

    Args:
        begins: When it began to be valid.
        ends: When it stops.

    Returns:
        The threshold in days.
    """
    return min(RENEW_BEFORE_DAYS, (ends - begins).total_seconds() / 86400 / 3)


def _is_name(host: str) -> bool:
    """
    Args:
        host: A DNS name or an address.

    Returns:
        True for a DNS name.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def _hostname() -> str:
    """
    Returns:
        This server's name, for the authority's label.
    """
    return socket.gethostname() or "server"
