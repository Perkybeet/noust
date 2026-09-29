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
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
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
