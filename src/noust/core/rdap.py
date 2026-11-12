# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust knows of RDAP without asking anyone anything (item 173).

RDAP is how a registry publishes what it knows of a domain, the date its registration expires among
it. Three questions have an answer here, none of them over the network:

- **Who answers for a top-level domain?** IANA's bootstrap list, which ships with Noust as data
  (:mod:`noust.core.rdap_bootstrap_data`, written by ``scripts/generate_rdap_bootstrap.py`` when a
  release is made). An installation never asks IANA. A top-level domain that is not in it has a
  registry that does not publish through RDAP, and that is said, not retried every day.
- **Which names are worth asking about?** There is no list of public suffixes in the package, so the
  registrable domain is found by the registry's own answer: the name is asked whole and then with
  one label less each time, until the registry recognises it (:func:`candidates`). ``shop.example.co.uk``
  is asked as itself, as ``example.co.uk`` and as ``co.uk``; the first one the registry knows is the
  registrable domain, and the caller keeps which it was.
- **When does it expire?** The ``expiration`` event of the answer (:func:`expiration_from`).

What is asked of whom is the manager's (:mod:`noust.managers.domain_registration`): the request is
declared in ``docs/PRIVACY.md``, goes through :mod:`noust.core.outbound`, and is off unless the
operator turned it on.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from noust.core.outbound import UnsafePath, path_segment
from noust.core.rdap_bootstrap_data import SERVICES

#: The most names asked about for one served name: the name and the labels above it, so that a
#: name with many labels is not climbed without end.
MAX_ASKS = 4

#: A label of a domain name as the registries write it: letters, digits and hyphens, not at the ends.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

#: The longest a domain name may be (RFC 1035).
_MAX_NAME = 253

#: The bundled list, by top-level domain.
_TABLE: dict[str, str] = {tld: base for base, tlds in SERVICES for tld in tlds.split()}


def service_for(tld: str) -> str | None:
    """
    Name the RDAP service of a top-level domain.

    Args:
        tld: The top-level domain, in any case, without a dot.

    Returns:
        The base address of its registry's RDAP service, ending in ``/``; None when IANA lists
        none for it (the registry does not publish through RDAP, or only over plain http, which
        Noust does not use).
    """
    return _TABLE.get(tld.strip().lower())


def served_name(name: str) -> str:
    """
    Reduce a name Noust serves to the name a registry could know.

    Args:
        name: A domain as served: a wildcard (``*.example.com``) covers the name beside the star.

    Returns:
        Lower case, without a trailing dot and without a leading ``*.``.
    """
    text = name.strip().lower().rstrip(".")
    return text[2:] if text.startswith("*.") else text


def is_checkable(name: str) -> bool:
    """
    Tell whether a name is one somebody may have registered.

    Args:
        name: A domain as served.

    Returns:
        True for a name of two or more ASCII labels of a registrable shape. An address, a name
        with a single label (``localhost``, ``intranet``), a name with a non-ASCII character (an
        internationalised one is asked about in its ``xn--`` form, which is what Noust serves) or
        with an empty or malformed label is not.
    """
    text = served_name(name)
    if not text or len(text) > _MAX_NAME or not text.isascii():
        return False
    try:
        ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return False
    labels = text.split(".")
    return len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)


def candidates(name: str) -> list[str]:
    """
    List the names to ask a registry about, in order, to find the one it recognises.

    Args:
        name: A domain as served.

    Returns:
        The name itself and then the name with one label less each time, down to two labels, at
        most :data:`MAX_ASKS` of them; empty for a name that is not worth asking about.
    """
    if not is_checkable(name):
        return []
    labels = served_name(name).split(".")
    found = [".".join(labels[start:]) for start in range(len(labels) - 1)]
    return found[:MAX_ASKS]


def rdap_url(base: str, name: str) -> str:
    """
    Make the address of the question "what do you know of this domain".

    Args:
        base: The registry's service, from :func:`service_for`.
        name: The domain asked about.

    Returns:
        ``<base>domain/<name>``, the name as one segment of the path.

    Raises:
        noust.core.outbound.UnsafePath: When the name is not a domain name (so it could be a path)
            or could not be one segment.
    """
    if not is_checkable(name):
        raise UnsafePath(f"Not a domain name that can be asked about: {name!r}")
    return f"{base}domain/{path_segment(served_name(name))}"


def registry_name(base: str) -> str:
    """
    Args:
        base: A registry's service address.

    Returns:
        Its host, which is how an operator knows who was asked.
    """
    return urlsplit(base).hostname or base


def _moment(text: Any) -> datetime | None:
    """
    Args:
        text: An ``eventDate``.

    Returns:
        The moment in UTC, or None when it is not a date a registry would write.
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return (moment - (moment.utcoffset() or timedelta(0))).replace(tzinfo=timezone.utc)


def expiration_from(document: Any) -> datetime | None:
    """
    Read the expiry out of a registry's answer.

    Args:
        document: The parsed answer (an RDAP domain object); anything else is no answer.

    Returns:
        The moment of its ``expiration`` event, in UTC; the later one if it says it twice; None
        when the answer carries none, which is what a registry that does not publish the expiry
        looks like, or is not an object at all. Never raises for what a registry sends.
    """
    if not isinstance(document, Mapping):
        return None
    events = document.get("events")
    if not isinstance(events, list):
        return None
    moments = [
        moment
        for event in events
        if isinstance(event, Mapping)
        and str(event.get("eventAction", "")).strip().lower() == "expiration"
        and (moment := _moment(event.get("eventDate"))) is not None
    ]
    return max(moments) if moments else None


def parse_answer(body: bytes) -> Mapping[str, Any] | None:
    """
    Args:
        body: What the registry sent.

    Returns:
        The JSON object, or None when it is not one.
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return document if isinstance(document, Mapping) else None
