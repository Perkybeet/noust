# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust central run`` does before it serves: the data directory, the
console's first credential, who may connect, and the facts ``noust central
status`` reports.

The first start is decided by the one fact that marks it: no master token
has ever been issued. That start issues one and the caller prints it; every
later start issues none, so the token appears once in ``docker logs`` and
never again (only its hash is kept, as on any Noust).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
import re
import socket
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs

if TYPE_CHECKING:
    from noust.web.auth import TokenManager

#: Who may reach a central's console when nothing else is said: this
#: machine, the private IPv4 ranges (RFC 1918) and IPv6 unique local
#: addresses. A NAS console answers the LAN, and a port forwarded to it by
#: mistake still answers nobody on the internet.
DEFAULT_ALLOWLIST: tuple[str, ...] = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)

#: The environment variable that overrides the allowlist, entries separated
#: by spaces or commas.
ALLOW_IP_ENV = "NOUST_ALLOW_IP"
#: A certificate pair the operator brings, instead of the self-signed one.
TLS_CERT_ENV = "NOUST_TLS_CERT"
TLS_KEY_ENV = "NOUST_TLS_KEY"
#: Names and addresses added to the self-signed certificate on top of the
#: machine's own, separated by spaces or commas. A central in a container sees
#: the container's addresses, not the NAS's: this is how the name people type
#: gets into the certificate.
TLS_NAMES_ENV = "NOUST_TLS_NAMES"

#: The most names one self-signed certificate is given.
MAX_CERTIFICATE_NAMES = 32

_DNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_NAME = re.compile(rf"^{_DNS_LABEL}(?:\.{_DNS_LABEL})*$")


def data_directories() -> tuple[Path, ...]:
    """
    Return the directories a central keeps its data in.

    Returns:
        Under ``NOUST_DATA_DIR`` when it is set; otherwise the system
        configuration and state directories.
    """
    if paths.DATA_DIR is not None:
        return paths.data_layout(paths.DATA_DIR).all()
    return (paths.config_dir(), paths.state_dir())


def ensure_data_dir(
    directories: Sequence[Path] | None = None,
    *,
    root: Path | None = None,
    fs: FileSystem | None = None,
) -> None:
    """
    Create the data directories, owner-only, and check they can be written.

    The store falls back to a per-user location when its directory is not
    there, so the directories are created before anything opens it: a
    central whose store landed outside the volume would lose its fleet on
    the next image update.

    Args:
        directories: What to create; :func:`data_directories` by default.
        root: The data directory itself, checked for writability first;
            ``NOUST_DATA_DIR`` by default.
        fs: The filesystem seam; the process-wide one by default.

    Raises:
        ConfigError: When the data directory cannot be written by this
            user, which on a bind mount means it belongs to someone else.
    """
    fs = fs or get_fs()
    root = root if root is not None else paths.DATA_DIR
    if root is not None and not (root.is_dir() and os.access(root, os.W_OK)):
        raise ConfigError(
            f"The data directory {root} is not writable by uid {os.getuid()}",
            details=(
                "A folder bind-mounted from the host must belong to this user first: "
                f"chown -R {os.getuid()}:{os.getgid()} <the folder on the host>. "
                "A named Docker volume is created with the right owner."
            ),
        )
    for directory in directories if directories is not None else data_directories():
        try:
            fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
        except PermissionError as exc:
            raise ConfigError(
                f"Cannot create {directory} as uid {os.getuid()}",
                details=(
                    f"Run as root, or set {paths.DATA_DIR_ENV} to a directory this user "
                    "owns, as the container image does (/data)."
                ),
            ) from exc


def allowlist(environ: Mapping[str, str] | None = None, config: Config | None = None) -> list[str]:
    """
    Decide who may connect to the central's console.

    Precedence: ``NOUST_ALLOW_IP``, then ``web.ip_whitelist`` in config.yaml,
    then :data:`DEFAULT_ALLOWLIST`. There is no "anyone" by omission: an
    operator who wants it says ``0.0.0.0/0 ::/0`` in so many words.

    Args:
        environ: The environment; the process's own by default.
        config: The configuration; the process-wide one by default.

    Returns:
        Addresses and networks, in the form ``web.ip_whitelist`` takes.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ALLOW_IP_ENV, "")
    entries = [entry for entry in re.split(r"[\s,]+", raw) if entry]
    if entries:
        return entries
    configured = [str(entry) for entry in (config or Config()).get("web.ip_whitelist") or []]
    return configured or list(DEFAULT_ALLOWLIST)


def operator_tls_pair(environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """
    Return the certificate pair the operator brought, if any.

    Args:
        environ: The environment; the process's own by default.

    Returns:
        ``(certificate, key)``, or None to mint a self-signed pair.

    Raises:
        ConfigError: When only one of the two is set.
    """
    env = os.environ if environ is None else environ
    cert = env.get(TLS_CERT_ENV, "").strip()
    key = env.get(TLS_KEY_ENV, "").strip()
    if not cert and not key:
        return None
    if not cert or not key:
        missing = TLS_KEY_ENV if cert else TLS_CERT_ENV
        raise ConfigError(
            "A TLS certificate and its private key travel together",
            details=f"Set {missing} as well, or neither to use the self-signed certificate.",
        )
    return cert, key


def issue_first_token(manager: TokenManager) -> str | None:
    """
    Issue the console's first master token, if none was ever issued.

    Args:
        manager: The console's token manager.

    Returns:
        The token, to be shown once; None on every later start.
    """
    if manager.current_master_generation() is not None:
        return None
    return manager.generate_master_token()


def certificate_fingerprint(path: Path) -> str | None:
    """
    Compute the SHA-256 fingerprint of a PEM certificate.

    It is what a browser shows for a self-signed certificate, so the
    operator can check that the one it warns about is this central's.

    Args:
        path: The certificate file.

    Returns:
        ``AB:CD:...``, or None when the file is missing or holds no
        certificate.
    """
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(
        r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", text, flags=re.DOTALL
    )
    if match is None:
        return None
    try:
        der = base64.b64decode("".join(match.group(1).split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def _san_entry(name: str) -> str | None:
    """
    Turn a host name or address into a ``subjectAltName`` entry.

    Anything that is not exactly a host name or an address is refused: the
    entries are joined with commas into one ``-addext`` argument, so a name
    with a comma, a colon or a space in it could add extensions of its own.

    Args:
        name: A candidate, as typed or as the system reported it.

    Returns:
        ``DNS:name`` or ``IP:address``, or None when it is neither.
    """
    text = name.strip().rstrip(".").split("%", 1)[0]
    if not text:
        return None
    try:
        return f"IP:{ipaddress.ip_address(text).compressed}"
    except ValueError:
        pass
    if len(text) <= 253 and _DNS_NAME.match(text):
        return f"DNS:{text.lower()}"
    return None


def _interface_addresses() -> list[str]:
    """
    List the addresses of this machine's interfaces a client could reach it by.

    Returns:
        Global and private addresses, in the order the system lists them.
        Loopback (added by name), link-local (a client cannot choose the
        interface a certificate is valid for), multicast and unspecified
        addresses are left out.
    """
    reported: list[str] = []
    try:
        import psutil
    except ImportError:
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None)
        except OSError:
            infos = []
        reported = [str(info[4][0]) for info in infos]
    else:
        for interface in psutil.net_if_addrs().values():
            reported.extend(
                str(entry.address)
                for entry in interface
                if entry.family in (socket.AF_INET, socket.AF_INET6)
            )

    usable: list[str] = []
    for text in reported:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or address.is_multicast:
            continue
        if address.is_unspecified:
            continue
        usable.append(str(address))
    return usable


def certificate_names(hostname: str, environ: Mapping[str, str] | None = None) -> list[str]:
    """
    Choose the names a self-signed certificate is valid for.

    Every current browser ignores the certificate's common name and reads only
    ``subjectAltName``, so a certificate without it cannot be trusted by
    anyone, however it is installed.

    Args:
        hostname: The subject: the bind address, or this machine's name when it
            answers on every interface.
        environ: The environment; the process's own by default. ``NOUST_TLS_NAMES``
            adds names and addresses.

    Returns:
        ``DNS:...`` and ``IP:...`` entries, without repeats, at most
        :data:`MAX_CERTIFICATE_NAMES`: the subject, the operator's additions,
        the machine's short name and FQDN, loopback, then the addresses of its
        interfaces.
    """
    env = os.environ if environ is None else environ
    extra = [item for item in re.split(r"[\s,]+", env.get(TLS_NAMES_ENV, "")) if item]
    try:
        fqdn = socket.getfqdn()
    except OSError:
        # A machine whose resolver is off still gets a certificate for what it
        # knows about itself.
        fqdn = ""
    candidates = [
        hostname,
        *extra,
        socket.gethostname(),
        fqdn,
        "localhost",
        "127.0.0.1",
        "::1",
        *_interface_addresses(),
    ]
    names: list[str] = []
    for candidate in candidates:
        entry = _san_entry(candidate)
        if entry is not None and entry not in names:
            names.append(entry)
    return names[:MAX_CERTIFICATE_NAMES]


def _read_tlv(data: bytes, position: int) -> tuple[int, int, int]:
    """
    Read the header of one DER element.

    Args:
        data: DER bytes.
        position: Where the element starts.

    Returns:
        ``(tag, start, end)``: the tag byte and where its value begins and ends.

    Raises:
        ValueError: When the element runs past the data or uses an indefinite
            or oversized length, which DER (and so a certificate) never does.
    """
    if position + 2 > len(data):
        raise ValueError("truncated element")
    tag = data[position]
    length = data[position + 1]
    start = position + 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or start + count > len(data):
            raise ValueError("unsupported length")
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    end = start + length
    if end > len(data):
        raise ValueError("element longer than the data")
    return tag, start, end


def _der_time(tag: int, value: bytes) -> datetime:
    """
    Decode an X.509 ``Time``.

    Args:
        tag: ``0x17`` (UTCTime) or ``0x18`` (GeneralizedTime).
        value: The element's content, ASCII ``...Z``.

    Returns:
        The instant, in UTC.

    Raises:
        ValueError: When it is not one of the two forms.
    """
    text = value.decode("ascii")
    if tag == 0x17 and re.fullmatch(r"\d{12}Z", text):
        year = int(text[:2])
        # RFC 5280: two digits below 50 are 20xx, the rest 19xx.
        return datetime.strptime(
            f"{2000 + year if year < 50 else 1900 + year}{text[2:]}", "%Y%m%d%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
    if tag == 0x18 and re.fullmatch(r"\d{14}Z", text):
        return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
    raise ValueError("not a certificate time")


def certificate_expiry(path: Path) -> datetime | None:
    """
    Read when a PEM certificate expires.

    Read from the certificate itself, without running anything: a status
    report must work on a machine with no openssl and must not spawn a
    process to print a date, the way :func:`certificate_fingerprint` does not.

    Args:
        path: The certificate file.

    Returns:
        The expiry, in UTC, or None when the file is missing or holds no
        certificate this can read.
    """
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(
        r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", text, flags=re.DOTALL
    )
    if match is None:
        return None
    try:
        der = base64.b64decode("".join(match.group(1).split()), validate=True)
        # Certificate ::= SEQUENCE { tbsCertificate SEQUENCE { [0] version?,
        # serial, signature, issuer, validity SEQUENCE { notBefore, notAfter }
        # ... } ... }
        _, certificate, _ = _read_tlv(der, 0)
        _, position, _ = _read_tlv(der, certificate)
        tag, _, end = _read_tlv(der, position)
        if tag == 0xA0:
            position = end
        # serial, signature algorithm, issuer: skip three elements.
        for _skipped in range(3):
            _, _, position = _read_tlv(der, position)
        _, validity, _ = _read_tlv(der, position)
        _, _, after_not_before = _read_tlv(der, validity)
        tag, start, end = _read_tlv(der, after_not_before)
        return _der_time(tag, der[start:end])
    except (binascii.Error, ValueError, IndexError):
        return None


def count_nodes(db_path: Path) -> int | None:
    """
    Count the servers the central manages.

    Read-only, on a connection of its own: a status report must not create,
    migrate or lock the store.

    Args:
        db_path: The store's database file.

    Returns:
        The number of nodes, or None when there is no store or no nodes
        table in it yet.
    """
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return int(row[0]) if row else 0
