# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application reaches an engine over TLS: the mode, the authority, and what a client reads.

A link between an application and a database can say how the application treats the
engine's certificate (the same words a client library uses):

- ``require``: the connection is encrypted; nothing is checked about who answers.
- ``verify-ca``: it is encrypted, and the certificate was signed by Noust's authority.
- ``verify-full``: and the certificate is for the name the application connected to.

Redis, Valkey and KeyDB in an instance serve TLS on their one port (``required``): the URL is
``rediss://`` and carries redis-py's ``ssl_cert_reqs``, ``ssl_ca_certs`` and ``ssl_check_hostname``.

Noust writes the mode and the path of the authority's certificate into the connection
string (``sslmode=verify-full&sslrootcert=/etc/noust/tls/ca.crt``), which every
application account can read: the authority's *public* certificate is the only file
handed over, its key never leaves root. ``DB_SSL_MODE`` and ``DB_SSL_CA`` accompany it
when the link also writes the ``DB_*`` variables, for the clients that take them apart.

A mode is only honest if the engine can keep it. ``verify-ca`` and ``verify-full`` need
an engine whose certificate is the authority's, which is an instance of Noust's that
serves TLS; ``require`` needs one that serves TLS at all. The server's own engine
qualifies for ``require`` only (its certificate is its package's), and an engine in
someone else's container for none: Noust cannot tell what it serves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noust.core.db_instance_store import TLS_OFF, DbInstanceStore
from noust.core.exceptions import DatabaseError, ValidationError
from noust.managers.database.instances import engine_of, is_instance_key
from noust.managers.database.tls_authority import ca_certificate_path

__all__ = ["LINK_MODES", "LinkTls", "check_link_tls", "client_options", "extra_variables"]

#: The modes a link may ask for, loosest first.
LINK_MODES: tuple[str, ...] = ("require", "verify-ca", "verify-full")

#: What MySQL's clients call each mode.
_MYSQL_MODES = {"require": "REQUIRED", "verify-ca": "VERIFY_CA", "verify-full": "VERIFY_IDENTITY"}


@dataclass(frozen=True)
class LinkTls:
    """
    What a link's TLS mode comes to.

    Attributes:
        mode: The mode.
        ca: The authority's certificate, which the application is pointed at.
    """

    mode: str
    ca: Path


def client_options(engine: str, mode: str | None, ca: Path | None = None) -> dict[str, str]:
    """
    Name the query parameters a client reads to use TLS in a mode.

    Args:
        engine: An engine name or an instance key.
        mode: One of :data:`LINK_MODES`, or None for none.
        ca: The authority's certificate; Noust's by default.

    Returns:
        The parameters, empty for no mode or an engine whose URL cannot carry them.
    """
    if mode is None:
        return {}
    authority = str(ca or ca_certificate_path())
    family = engine_of(engine)
    if family == "postgresql":
        options = {"sslmode": mode}
        if mode != "require":
            options["sslrootcert"] = authority
        return options
    if family == "mysql":
        options = {"ssl-mode": _MYSQL_MODES[mode]}
        if mode != "require":
            options["ssl-ca"] = authority
        return options
    if family == "redis":
        # redis-py's names; a client that reads none of them is given the same facts as
        # DB_SSL_MODE and DB_SSL_CA, and the scheme (rediss) says it speaks TLS.
        if mode == "require":
            return {"ssl_cert_reqs": "none"}
        return {
            "ssl_cert_reqs": "required",
            "ssl_ca_certs": authority,
            "ssl_check_hostname": "true" if mode == "verify-full" else "false",
        }
    if family == "mongodb":
        options = {"tls": "true"}
        if mode != "require":
            options["tlsCAFile"] = authority
        if mode == "verify-ca":
            options["tlsAllowInvalidHostnames"] = "true"
        if mode == "require":
            options["tlsInsecure"] = "true"
        return options
    return {}


def extra_variables(mode: str | None, ca: Path | None = None) -> dict[str, str]:
    """
    Name the ``DB_*`` variables that carry the mode, for clients that take them apart.

    Args:
        mode: One of :data:`LINK_MODES`, or None.
        ca: The authority's certificate; Noust's by default.

    Returns:
        ``DB_SSL_MODE`` and, where the mode verifies, ``DB_SSL_CA``; none for no mode.
    """
    if mode is None:
        return {}
    variables = {"DB_SSL_MODE": mode}
    if mode != "require":
        variables["DB_SSL_CA"] = str(ca or ca_certificate_path())
    return variables


def check_link_tls(
    engine: str, mode: str | None, *, instances: DbInstanceStore | None = None
) -> LinkTls | None:
    """
    Refuse a mode the engine cannot keep.

    Args:
        engine: The engine key the link is to.
        mode: The mode asked for.
        instances: The instances Noust runs; the process-wide store's by default.

    Returns:
        What the mode comes to, or None when none was asked for.

    Raises:
        ValidationError: It is not a mode, or the engine cannot keep it. The error
            says what would let it.
    """
    if mode is None:
        return None
    if mode not in LINK_MODES:
        raise ValidationError(
            f"{mode!r} is not a TLS mode for a link",
            details=f"Use one of: {', '.join(LINK_MODES)}.",
            field="tls",
        )
    if engine_of(engine) == "redis" and not is_instance_key(engine):
        raise ValidationError(
            "The server's own Redis or Valkey serves no TLS through Noust",
            details=(
                "Link without --tls, or move the database to an instance of Noust's that serves "
                "TLS (noust db instance create, then noust db instance tls NAME --mode required)."
            ),
            field="tls",
        )
    if not is_instance_key(engine):
        if mode != "require":
            raise ValidationError(
                f"{mode} needs an engine whose certificate is Noust's authority's",
                details=(
                    "The server's own engine presents the certificate its package made, which "
                    "nothing can verify. Use --tls require, or move the database to an "
                    "instance of Noust's that serves TLS: noust db move"
                ),
                field="tls",
            )
        return LinkTls(mode, ca_certificate_path())
    store = instances or DbInstanceStore()
    record = store.get(engine.partition("@")[2])
    if record is None:
        raise ValidationError(
            f"{engine} is not an instance of Noust's, so Noust cannot tell what TLS it serves",
            details="Link without --tls.",
            field="tls",
        )
    if record.tls_mode == TLS_OFF:
        raise DatabaseError(
            f"{record.name} does not serve TLS",
            details=f"Turn it on first: noust db instance tls {record.name} --mode "
            + ("required" if engine_of(engine) == "redis" else "optional"),
        )
    return LinkTls(mode, ca_certificate_path())
