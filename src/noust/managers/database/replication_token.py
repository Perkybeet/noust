# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The join code a primary gives a replica (checklist C-14).

Preparing a replica on the primary makes an account, a slot and a line in ``pg_hba.conf``; the
replica needs to know where to connect, as whom, with what password, over which certificate, and
what the primary requires of it. All of that travels once, as one line the operator carries to the
other server::

    noust-replica:v1:<base64 of a JSON object>

It holds the account's password, so it is a secret: it is printed once, written to a file only when
asked for (0600), and shown again only by ``noust db replica token`` on the primary. It holds no
key of anything else.

The code is text a person pastes, so reading it is an allowlist (rule 4): every field is checked
for its type and its alphabet before anything uses it, and a code that is not exactly what
:func:`encode` makes is refused with the field named.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError
from noust.validators.replica import check_replica_host, check_replica_name, role_for, slot_for

__all__ = ["ENGINES", "PREFIX", "REQUIRED_SETTINGS", "ReplicaToken", "encode", "parse"]

#: The engines a code can be for, and the flavours MySQL's key covers.
ENGINES = ("postgresql", "mysql")
FLAVOURS = ("mariadb", "mysql")

#: What every code starts with, and the version of the format after it.
PREFIX = "noust-replica:v1:"

#: The most a code may weigh once decoded; one with a certificate chain is a few kilobytes.
MAX_DECODED = 64 * 1024

#: The settings a hot standby must have at least as high as its primary, or it refuses to start
#: (the server's own list, "Hot Standby" in the PostgreSQL manual).
REQUIRED_SETTINGS = (
    "max_connections",
    "max_locks_per_transaction",
    "max_prepared_transactions",
    "max_wal_senders",
    "max_worker_processes",
)

#: The password Noust generates: letters and digits, long.
_PASSWORD = re.compile(r"[A-Za-z0-9]{24,128}")

#: One or more PEM certificates and nothing else around them.
_PEM = re.compile(
    r"(?:-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]{40,8000}\n-----END CERTIFICATE-----\n){1,8}"
)

_KEYS = {"v", "e", "f", "n", "h", "p", "u", "w", "s", "c", "m", "b", "r"}


@dataclass(frozen=True)
class ReplicaToken:
    """
    Everything a replica needs to follow a primary.

    Attributes:
        name: The replica's name on the primary.
        host: The name or address it connects to; the primary's certificate must cover it.
        port: The primary's port.
        role: The replication account.
        password: The account's password.
        slot: The physical replication slot made for it.
        major: The primary's version: PostgreSQL's major (a physical copy needs the same one),
            or ``major * 100 + minor`` for MySQL (``1104`` is MariaDB 11.4, ``804`` MySQL 8.4).
        size: The primary's size in bytes when the code was made, to refuse a disk that is too
            small before copying.
        requires: The values of :data:`REQUIRED_SETTINGS` on the primary (PostgreSQL), or the
            primary's ``server_id`` (MySQL).
        ca: The certificate authority to trust, PEM; empty for the system's own.
        engine: ``postgresql`` or ``mysql`` (MySQL and MariaDB).
        flavour: ``mariadb`` or ``mysql`` for MySQL's key; empty for PostgreSQL.
    """

    name: str
    host: str
    port: int
    role: str
    password: str
    slot: str
    major: int
    size: int = 0
    requires: dict[str, int] = field(default_factory=dict)
    ca: str = ""
    engine: str = "postgresql"
    flavour: str = ""


def encode(token: ReplicaToken) -> str:
    """
    Write a join code.

    Args:
        token: What the replica needs.

    Returns:
        The code, one line.
    """
    body = {
        "v": 1,
        "e": token.engine,
        "f": token.flavour,
        "n": token.name,
        "h": token.host,
        "p": token.port,
        "u": token.role,
        "w": token.password,
        "s": token.slot,
        "c": token.ca,
        "m": token.major,
        "b": token.size,
        "r": token.requires,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def _bad(field_name: str, why: str) -> ValidationError:
    """
    Build the refusal of a code.

    Args:
        field_name: The code's field that is wrong.
        why: What is wrong with it.

    Returns:
        The error.
    """
    return ValidationError(
        f"This is not a join code Noust made ({why})",
        details="Copy the whole line that 'noust db replica prepare' printed on the primary, "
        "or print it again there with 'noust db replica token'.",
        field=field_name,
    )


def _whole(value: Any, field_name: str, low: int, high: int) -> int:
    """
    Check an integer field.

    Args:
        value: The decoded value.
        field_name: Its name, for the error.
        low: The smallest accepted.
        high: The largest accepted.

    Returns:
        The integer.

    Raises:
        ValidationError: It is not an integer in range (a boolean is not one).
    """
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _bad(field_name, f"{field_name} is not a number between {low} and {high}")
    return int(value)


def parse(text: str) -> ReplicaToken:
    """
    Read a join code.

    Args:
        text: What the operator pasted.

    Returns:
        The checked code.

    Raises:
        ValidationError: When it is not exactly what :func:`encode` makes, naming the field.
    """
    line = text.strip() if isinstance(text, str) else ""
    if not line.startswith(PREFIX) or "\n" in line or "\r" in line:
        raise _bad("token", "it does not start with noust-replica:v1:")
    try:
        raw = base64.b64decode(line.removeprefix(PREFIX), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad("token", "it was cut or changed on the way") from exc
    if len(raw) > MAX_DECODED:
        raise _bad("token", "it is too long")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _bad("token", "it was cut or changed on the way") from exc
    if not isinstance(body, dict) or set(body) != _KEYS:
        raise _bad("token", "it does not hold the fields of a code")
    if body["v"] != 1 or body["e"] not in ENGINES:
        raise _bad("token", "it is for another version or another engine")
    for key in ("n", "h", "u", "w", "s", "c", "f"):
        if not isinstance(body[key], str):
            raise _bad(key, f"field {key} is not text")
    name = check_replica_name(body["n"])
    host = check_replica_host(body["h"])
    engine = body["e"]
    slot = slot_for(name) if engine == "postgresql" else ""
    if body["u"] != role_for(name) or body["s"] != slot:
        raise _bad("role", "the account or the slot is not the one of that name")
    if body["f"] != ("" if engine == "postgresql" else body["f"]) or (
        engine == "mysql" and body["f"] not in FLAVOURS
    ):
        raise _bad("flavour", "the flavour is not one this engine has")
    if _PASSWORD.fullmatch(body["w"]) is None:
        raise _bad("password", "the password is not one Noust makes")
    if body["c"] and _PEM.fullmatch(body["c"]) is None:
        raise _bad("ca", "the certificate authority is not PEM")
    requires = body["r"]
    wanted = REQUIRED_SETTINGS if engine == "postgresql" else ("server_id",)
    if not isinstance(requires, dict) or set(requires) != set(wanted):
        raise _bad("requires", "the requirements are not the ones a replica needs")
    high = 10_000_000 if engine == "postgresql" else 2**32 - 1
    return ReplicaToken(
        name=name,
        host=host,
        port=_whole(body["p"], "port", 1, 65535),
        role=body["u"],
        password=body["w"],
        slot=body["s"],
        major=_whole(body["m"], "major", 12, 99)
        if engine == "postgresql"
        else _whole(body["m"], "major", 800, 9999),
        size=_whole(body["b"], "size", 0, 2**50),
        requires={key: _whole(requires[key], key, 0, high) for key in wanted},
        ca=body["c"],
        engine=engine,
        flavour=body["f"],
    )
