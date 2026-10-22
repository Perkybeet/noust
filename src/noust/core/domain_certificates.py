# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which names of an application hold a certificate of their own.

An application's certificate is one lineage, named after its primary domain, that covers
every name it answers on. That is the right default and the wrong shape for some names: one
that should not appear next to the others in a certificate transparency log, one whose
DNS stops pointing here and fails the renewal of all its neighbours, one a customer
brought. A name can ask for a certificate of its own instead (``domains.certificate``):

- ``shared`` (the default, and what every name was before): the lineage under the primary
  domain covers it.
- ``own``: certbot keeps a lineage for this name alone, named after it
  (:func:`lineage_name`), the web server gets a server block for it, and the shared lineage
  stops naming it.

The pure rules live here so the store that records the choice, the certificate manager that
orders, the web server manager that writes the site and the commands that list it all say
the same thing (CONTRIBUTING, rule 3). Nothing here touches a file or a process.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from noust.core.exceptions import ValidationError
from noust.validators.domain import WILDCARD_PREFIX, is_valid_domain, is_wildcard, wildcard_base

#: The lineage under the primary domain covers the name.
SHARED = "shared"
#: The name has a lineage of its own.
OWN = "own"
MODES: tuple[str, ...] = (SHARED, OWN)

#: Applications whose certificate is ordered by their deploy for names only it knows (a
#: monorepo's workspace subdomains), so a name cannot be taken out of it.
TYPES_WITHOUT_OWN_CERTIFICATE: frozenset[str] = frozenset({"monorepo"})

#: What a wildcard's lineage starts with. A directory cannot be called ``*.example.com`` in
#: any way a person should have to quote, and a host name cannot start with an underscore, so
#: nothing a person adds can ever be called this.
_WILDCARD_LINEAGE_PREFIX = "_wildcard."


class HasCertificate(Protocol):
    """What the helpers below read of a domain row."""

    domain: str
    certificate: str


def check_mode(mode: str) -> str:
    """
    Refuse a value that is not a way for a name to be covered.

    Args:
        mode: ``shared`` or ``own``.

    Returns:
        The mode, unchanged.

    Raises:
        ValidationError: It is neither. The match is exact: ``Own`` and ``own `` are not
            modes, because the value is written to a column the web server's rendering
            reads back.
    """
    if mode not in MODES:
        raise ValidationError(
            f"{mode!r} is not a way for a name to be covered",
            details=f"Choose one of: {', '.join(MODES)}.",
            field="certificate",
        )
    return mode


def lineage_name(name: str) -> str:
    """
    Name the certbot lineage that holds a name's own certificate.

    Args:
        name: A domain, or a wildcard such as ``*.example.com``.

    Returns:
        The name itself; for a wildcard, ``_wildcard.`` and what it is a wildcard of.
        It becomes a directory under ``/etc/letsencrypt/live`` and a ``--cert-name``
        argument, so nothing that is not a domain gets this far.

    Raises:
        ValidationError: The name is not a domain or a wildcard of one.
    """
    if not isinstance(name, str):
        raise ValidationError("A certificate is named after a host name", field="name")
    candidate = name.strip().lower()
    if is_wildcard(candidate):
        return f"{_WILDCARD_LINEAGE_PREFIX}{wildcard_base(candidate)}"
    valid, reason = is_valid_domain(candidate)
    if not valid or candidate.startswith(WILDCARD_PREFIX):
        raise ValidationError(
            f"{name!r} cannot name a certificate",
            details=f"{reason or 'Not a domain name'}. A certificate is kept under the name "
            "it covers, so it must be a bare host name such as shop.example.com.",
            field="name",
        )
    return candidate


def is_wildcard_lineage(lineage: str) -> bool:
    """
    Args:
        lineage: A lineage name.

    Returns:
        True when it is the lineage of a wildcard name (see :func:`lineage_name`).
    """
    return lineage.startswith(_WILDCARD_LINEAGE_PREFIX)


def shared_names(records: Iterable[HasCertificate]) -> list[str]:
    """
    List the names the lineage under the primary domain must cover.

    Args:
        records: An application's domain rows.

    Returns:
        The names that did not ask for a certificate of their own, in the rows' order.
    """
    return [record.domain for record in records if record.certificate != OWN]


def own_names(records: Iterable[HasCertificate]) -> list[str]:
    """
    List the names that hold a certificate of their own.

    Args:
        records: An application's domain rows.

    Returns:
        Those names, in the rows' order.
    """
    return [record.domain for record in records if record.certificate == OWN]
