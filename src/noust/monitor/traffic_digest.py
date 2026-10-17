# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an access log says about a site, summed by the hour (checklist G-08).

:mod:`noust.monitor.traffic` reads the new lines of each site's log every few
seconds and only counted them. This turns the same lines into what an operator
asks of a proxy: how many requests there were, how they ended (2xx, 3xx, 4xx,
5xx and the codes themselves), how many bytes were served, which paths were
asked for most, from which networks, and from where visitors came.

Three decisions keep that safe to keep for months:

- **No query string, ever.** A URL's query is where tokens, e-mail addresses and
  session ids live. Only the method and the path are kept, and a path segment
  that is a number, a UUID or a long hexadecimal string becomes ``:n``,
  ``:uuid`` or ``:hash``, so ``GET /orders/48211`` and ``GET /orders/48212`` are
  one row, and the table of top paths does not grow with the number of orders.
- **No address of a person.** A client is its network: the first 24 bits of an
  IPv4 address, the first 48 of an IPv6 one. Whoever needs the exact address has
  the access log, which is the web server's to rotate.
- **Bounded.** The most distinct keys a hour of a site keeps for each question is
  :data:`KEYS_PER_HOUR`; a scan of a thousand different paths cannot make the
  table of one hour grow without limit.

Pure: it parses bytes and returns counts. The metrics store keeps them
(:meth:`~noust.monitor.timeseries.MetricsStore.add_traffic`).
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

#: The questions a site's traffic is asked.
DIMENSIONS: tuple[str, ...] = ("path", "network", "status", "referrer")

#: The most distinct keys one hour of one site keeps for one question.
KEYS_PER_HOUR = 50

#: Longest key kept: a path longer than this is a probe, not a page.
MAX_KEY = 200

#: What stands for a visit that carried no referrer.
DIRECT = "(direct)"

#: ``ip ident user [time] "request" status bytes "referrer" "agent"``: the
#: combined format nginx and Apache share. Only what is needed is captured, and
#: everything after the status is optional so a log in the common format is
#: still counted (the totals are the same as :func:`noust.monitor.traffic.count_lines`).
_LINE = re.compile(
    rb'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]*)\] "(?P<request>(?:[^"\\]|\\.)*)" '
    rb"(?P<status>\d{3})(?: (?P<bytes>\S+))?"
    rb'(?: "(?P<referrer>(?:[^"\\]|\\.)*)")?'
)

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}

#: ``16/Oct/2026:10:15:30 +0200``.
_TIME = re.compile(rb"(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX = re.compile(r"[0-9a-fA-F]{16,}")
_NUMBER = re.compile(r"\d+")

#: The request line: method, target, protocol. A bad request (``\x16\x03...``,
#: an empty line) has no target and counts as a request without a path.
_REQUEST = re.compile(r"([A-Z]{3,10}) (\S+)(?: \S+)?")

_TIME_CACHE: dict[bytes, int | None] = {}
_TIME_CACHE_MAX = 4096


@dataclass
class HourCounts:
    """
    Everything one site was asked in one hour.

    Attributes:
        requests: Lines counted.
        classes: Requests by status class, indexed by the first digit (``classes[5]``
            is the 5xx).
        bytes: Bytes of the bodies served.
        keys: For each of :data:`DIMENSIONS`, how many times each key was seen.
    """

    requests: int = 0
    classes: list[int] = field(default_factory=lambda: [0] * 10)
    bytes: int = 0
    keys: dict[str, Counter[str]] = field(
        default_factory=lambda: {dimension: Counter() for dimension in DIMENSIONS}
    )

    def merge(self, other: HourCounts) -> None:
        """
        Add another hour's counts to this one.

        Args:
            other: The counts to add.
        """
        self.requests += other.requests
        self.bytes += other.bytes
        for index, count in enumerate(other.classes):
            self.classes[index] += count
        for dimension, counter in other.keys.items():
            self.keys[dimension].update(counter)


@dataclass
class Digest:
    """
    What a stretch of a log said.

    Attributes:
        requests: Lines counted (the same number
            :func:`~noust.monitor.traffic.count_lines` gives).
        errors_5xx: Of those, the 5xx.
        hours: By the hour (epoch seconds, UTC, hour-aligned).
    """

    requests: int = 0
    errors_5xx: int = 0
    hours: dict[int, HourCounts] = field(default_factory=dict)


def parse_time(raw: bytes) -> int | None:
    """
    Read the time of a log line.

    Args:
        raw: What is between the brackets: ``16/Oct/2026:10:15:30 +0200``.

    Returns:
        Epoch seconds (UTC), or None when it is not a time. Cached: a busy site
        has hundreds of lines in the same second.
    """
    if raw in _TIME_CACHE:
        return _TIME_CACHE[raw]
    match = _TIME.fullmatch(raw)
    seconds: int | None = None
    if match is not None:
        day, month, year, hour, minute, second, sign, zone_h, zone_m = match.groups()
        number = _MONTHS.get(month.decode("ascii").capitalize())
        if number is not None:
            try:
                # datetime refuses a 32nd of September, which timegm would roll over.
                moment = datetime(
                    int(year),
                    number,
                    int(day),
                    int(hour),
                    int(minute),
                    int(second),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                seconds = None
            else:
                offset = int(zone_h) * 3600 + int(zone_m) * 60
                seconds = int(moment.timestamp()) - (offset if sign == b"+" else -offset)
    if len(_TIME_CACHE) >= _TIME_CACHE_MAX:
        _TIME_CACHE.clear()
    _TIME_CACHE[raw] = seconds
    return seconds


def normalize_path(request: str) -> str | None:
    """
    Turn a request line into the key of a page: method and path, no query.

    Args:
        request: ``GET /orders/48211?token=abc HTTP/1.1``.

    Returns:
        ``GET /orders/:n``; None when the line is not a request (garbage sent to
        the port).
    """
    match = _REQUEST.fullmatch(request)
    if match is None:
        return None
    method, target = match.groups()
    if not target.startswith("/"):
        # An absolute-form target (a proxy request) or an authority: not a page of this site.
        return None
    path = urlsplit(target).path or "/"
    segments = []
    for segment in path.split("/"):
        if _NUMBER.fullmatch(segment):
            segments.append(":n")
        elif _UUID.fullmatch(segment):
            segments.append(":uuid")
        elif _HEX.fullmatch(segment):
            segments.append(":hash")
        else:
            segments.append(segment)
    return f"{method} {'/'.join(segments)}"[:MAX_KEY]


def network_of(address: str) -> str | None:
    """
    Name the network a client address is in, and not the client.

    Args:
        address: The client address as the log has it.

    Returns:
        ``203.0.113.0/24`` for an IPv4 address, ``2001:db8:1::/48`` for an IPv6
        one; None when it is neither (a hostname, ``-``).
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    prefix = 24 if parsed.version == 4 else 48
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def referrer_host(referrer: str) -> str:
    """
    Args:
        referrer: The ``Referer`` header as logged.

    Returns:
        The host it names, never a path or a query; :data:`DIRECT` for none.
    """
    if not referrer or referrer == "-":
        return DIRECT
    host = urlsplit(referrer).hostname
    return (host or DIRECT)[:MAX_KEY]


def analyse(data: bytes) -> Digest:
    """
    Digest complete access log lines.

    Args:
        data: Log lines, newline separated.

    Returns:
        The totals and the counts by hour. A line that is not in the combined
        format is not a request and is not counted; one whose time cannot be read
        is counted in the totals but kept out of the hours.
    """
    digest = Digest()
    for line in data.split(b"\n"):
        match = _LINE.match(line)
        if match is None:
            continue
        digest.requests += 1
        status = match.group("status").decode("ascii")
        if status[0] == "5":
            digest.errors_5xx += 1
        stamp = parse_time(match.group("time"))
        if stamp is None:
            continue
        hour = stamp - stamp % 3600
        counts = digest.hours.get(hour)
        if counts is None:
            counts = digest.hours[hour] = HourCounts()
        counts.requests += 1
        counts.classes[int(status[0])] += 1
        sent = match.group("bytes")
        if sent and sent.isdigit():
            counts.bytes += int(sent)
        counts.keys["status"][status] += 1
        path = normalize_path(match.group("request").decode("utf-8", "replace"))
        if path is not None:
            counts.keys["path"][path] += 1
        network = network_of(match.group("ip").decode("ascii", "replace"))
        if network is not None:
            counts.keys["network"][network] += 1
        referrer = match.group("referrer")
        counts.keys["referrer"][
            referrer_host(referrer.decode("utf-8", "replace")) if referrer is not None else DIRECT
        ] += 1
    for counts in digest.hours.values():
        for dimension, counter in counts.keys.items():
            if len(counter) > KEYS_PER_HOUR:
                counts.keys[dimension] = Counter(dict(counter.most_common(KEYS_PER_HOUR)))
    return digest
