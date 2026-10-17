# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a replica of a host database may be called, and where it may be reached.

A replica's name becomes a role (``noust_repl_<name>``), a replication slot (``noust_<name>``),
a line of ``pg_hba.conf``, a file name in the replica's account and a part of a secret's name, so
it is as narrow as the narrowest of them: lower-case letters, digits and single underscores, none
of which needs quoting in SQL or in the file. The host a join code points at reaches ``libpq``
and a passfile, so it is a name or an address and nothing else.

The field names are the CLI's (``name``, ``host``), carried in the error so a caller can put the
refusal next to its input.
"""

from __future__ import annotations

import ipaddress
import re

from noust.core.exceptions import ValidationError
from noust.validators.domain import is_valid_domain

#: The longest replica name. ``noust_repl_`` and the name must stay a role name (63 bytes) with
#: room to spare, and short enough to read in ``pg_stat_replication``.
MAX_NAME_LENGTH = 24

#: Lower-case letters and digits, with single underscores between them, starting with a letter.
_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")

#: What every replication role and slot Noust makes starts with.
ROLE_PREFIX = "noust_repl_"
SLOT_PREFIX = "noust_"


def check_replica_name(name: object) -> str:
    """
    Accept the name an operator gives a replica.

    Args:
        name: The candidate.

    Returns:
        The name, unchanged.

    Raises:
        ValidationError: It is not lower-case words joined by underscores, or is longer than
            :data:`MAX_NAME_LENGTH`.
    """
    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or _NAME.fullmatch(name) is None:
        raise ValidationError(
            f"{name!r} cannot be the name of a replica",
            details=(
                "Use lower-case letters, digits and single underscores, starting with a letter, "
                f"up to {MAX_NAME_LENGTH} characters: reports, standby, eu_west."
            ),
            field="name",
        )
    return name


def role_for(name: str) -> str:
    """
    Name the replication role of a replica.

    Args:
        name: A replica's name (already checked).

    Returns:
        ``noust_repl_<name>``.
    """
    return f"{ROLE_PREFIX}{check_replica_name(name)}"


def slot_for(name: str) -> str:
    """
    Name the replication slot of a replica.

    Args:
        name: A replica's name (already checked).

    Returns:
        ``noust_<name>``.
    """
    return f"{SLOT_PREFIX}{check_replica_name(name)}"


def check_replica_host(host: object) -> str:
    """
    Accept the name or the address a replica reaches its primary by.

    Args:
        host: A host name or an IP address.

    Returns:
        The host, unchanged.

    Raises:
        ValidationError: It is neither a domain name nor an IP address.
    """
    if isinstance(host, str) and host == host.strip() and 0 < len(host) <= 253:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if is_valid_domain(host)[0]:
                return host
        else:
            return host
    raise ValidationError(
        f"{host!r} cannot be the host a replica connects to",
        details="Give a domain name the certificate covers, or an IP address.",
        field="host",
    )
