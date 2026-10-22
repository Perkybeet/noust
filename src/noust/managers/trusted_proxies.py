# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which networks a site may believe about the client's address: Cloudflare's.

Behind a CDN every connection arrives from the CDN, so a site that logs, limits
or filters by address sees only the CDN's unless the web server is told which
header carries the real one - and from whom it may be believed. Believing the
header from anyone lets any client write its own address, so the networks are
the whole point: they are Cloudflare's published ranges, and nothing else.

The ranges change rarely and a server may be offline when they do, so there are
two sources: the last list fetched (kept in Noust's state directory), and the
list built in below, which is what a server that never fetched uses. Fetching
validates everything: every line must be a network, none may be so wide that
trusting it is trusting the internet, and a list of fewer than a handful of
entries is an error page, not a list. A failed fetch changes nothing.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from noust import __version__
from noust.core import paths
from noust.core.exceptions import IntegrationError
from noust.core.fs import FileSystem, get_fs

_log = logging.getLogger(__name__)

#: Where Cloudflare publishes its ranges, one network per line.
CLOUDFLARE_IPS_V4 = "https://www.cloudflare.com/ips-v4"
CLOUDFLARE_IPS_V6 = "https://www.cloudflare.com/ips-v6"

#: Cloudflare's ranges as published on 2026-10-13: what a server that never
#: fetched trusts.
CLOUDFLARE_BUILT_IN: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

#: Seconds before a fetch is abandoned.
FETCH_TIMEOUT = 15

#: The most bytes read of one list.
_MAX_BYTES = 64 * 1024

#: A list shorter than this is not Cloudflare's.
_MIN_RANGES = 5

#: The widest network trusted, by family: a ``/4`` would trust a quarter of the internet.
_MIN_PREFIX = {4: 8, 6: 16}

#: How old the fetched list may be before a refresh is due.
STALE_AFTER_DAYS = 7

Opener = Callable[..., IO[bytes]]


@dataclass(frozen=True)
class RangeList:
    """
    The ranges in force.

    Attributes:
        networks: The networks, IPv4 then IPv6.
        source: ``fetched`` or ``built-in``.
        fetched_at: When the fetched list was read, ISO 8601 UTC; None for the built-in one.
    """

    networks: tuple[str, ...]
    source: str
    fetched_at: str | None = None


def cache_path() -> Path:
    """
    Returns:
        The file the last fetched list is kept in.
    """
    return paths.state_dir() / "trusted-proxies" / "cloudflare.json"


def validated_networks(lines: list[str]) -> list[str]:
    """
    Check a list of networks, refusing one that would trust too much.

    Args:
        lines: Candidate networks, one per entry.

    Returns:
        The networks in canonical form.

    Raises:
        IntegrationError: A line is not a network, or one is wider than is safe.
    """
    found: list[str] = []
    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=True)
        except ValueError:
            raise IntegrationError(
                f"{text!r} is not a network",
                details="The list of ranges has an entry that is not an address range.",
            ) from None
        if network.prefixlen < _MIN_PREFIX[network.version]:
            raise IntegrationError(
                f"{network} is too wide to trust",
                details="Trusting it would trust a large part of the internet to name clients.",
            )
        found.append(str(network))
    return found


def current_ranges() -> RangeList:
    """
    Read the ranges in force.

    Returns:
        The fetched list when there is a sound one on disk, else the built-in one.
    """
    try:
        document = json.loads(cache_path().read_text(encoding="utf-8"))
        networks = validated_networks([str(item) for item in document["networks"]])
        if len(networks) >= _MIN_RANGES:
            return RangeList(tuple(networks), "fetched", str(document.get("fetched_at") or ""))
    except (OSError, ValueError, KeyError, TypeError, IntegrationError) as exc:
        _log.debug("No usable fetched list of Cloudflare ranges (%s); using the built-in one", exc)
    return RangeList(CLOUDFLARE_BUILT_IN, "built-in")


def is_stale(now: datetime | None = None) -> bool:
    """
    Tell whether the ranges should be fetched again.

    Args:
        now: The current time; the clock by default.

    Returns:
        True when nothing was ever fetched, or the last fetch is older than
        :data:`STALE_AFTER_DAYS`.
    """
    current = current_ranges()
    if current.fetched_at is None or not current.fetched_at:
        return True
    try:
        fetched = datetime.fromisoformat(current.fetched_at)
    except ValueError:
        return True
    age = (now or datetime.now(timezone.utc)) - fetched
    return age.days >= STALE_AFTER_DAYS


def _read(url: str, opener: Opener) -> list[str]:
    """
    Fetch one list.

    Args:
        url: Where it is.
        opener: Replacement for :func:`urllib.request.urlopen`.

    Returns:
        Its lines.

    Raises:
        IntegrationError: The list could not be read.
    """
    request = urllib.request.Request(url, headers={"User-Agent": f"noust/{__version__}"})
    try:
        with opener(request, timeout=FETCH_TIMEOUT) as response:
            raw = response.read(_MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise IntegrationError(
            f"Could not read {url}",
            details="Check this server's outbound HTTPS access to cloudflare.com.",
            output=str(getattr(exc, "reason", None) or exc),
        ) from None
    if len(raw) > _MAX_BYTES:
        raise IntegrationError(f"{url} is larger than a list of ranges can be")
    return raw.decode("utf-8", errors="replace").splitlines()


def refresh(*, opener: Opener | None = None, fs: FileSystem | None = None) -> RangeList:
    """
    Fetch Cloudflare's published ranges and keep them.

    Nothing changes unless every list read is sound.

    Args:
        opener: Replacement for :func:`urllib.request.urlopen` (tests).
        fs: The filesystem to write through.

    Returns:
        The ranges now in force.

    Raises:
        IntegrationError: A list could not be read or is not sound; the previous
            list stays.
    """
    use = opener or urllib.request.urlopen
    networks = validated_networks(_read(CLOUDFLARE_IPS_V4, use) + _read(CLOUDFLARE_IPS_V6, use))
    if len(networks) < _MIN_RANGES:
        raise IntegrationError(
            "Cloudflare's list of ranges is too short to be one",
            details=f"It named {len(networks)} networks; the previous list is kept.",
        )
    when = datetime.now(timezone.utc).isoformat()
    destination = cache_path()
    fs = fs or get_fs()
    fs.make_dir(destination.parent, mode=0o755, parents=True)
    fs.write_text(
        destination, json.dumps({"fetched_at": when, "networks": networks}) + "\n", mode=0o644
    )
    return RangeList(tuple(networks), "fetched", when)
