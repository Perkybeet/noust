# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The names of the per-container series of a Compose stack (checklist G-04).

An application's series are ``app.<domain>.<what>``; a stack's containers also
have their own, ``app.<domain>.ctr.<container>.<what>``, so a chart (and a
scrape) can say *which* service is the one reading the disk. The container
name is the part that may carry anything, so it is kept to letters, digits,
``_`` and ``-`` (Docker allows dots, which a metric name cannot spare), and the
domain, which has dots, is found by reading the name from its end.

One definition (rule 3): the sampler writes these names, the status endpoint
lists them and the exporter parses them back, all through this module.
"""

from __future__ import annotations

import re

#: The segment that marks a per-container series.
SEGMENT = "ctr"

#: What is measured per container: the application's own series, one container
#: at a time.
SUFFIXES: tuple[str, ...] = (
    "cpu.percent",
    "mem.bytes",
    "io.read_bytes_s",
    "io.write_bytes_s",
    "net.rx_bytes_s",
    "net.tx_bytes_s",
)

#: The longest container name kept.
MAX_NAME = 64

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

#: ``docker compose run`` leaves a container named ``<project>-<service>-run-<hex>``
#: for the length of one command: a series for each would never stop being new.
_ONE_OFF = re.compile(r".*-run-[0-9a-f]{6,}")

_PARSE = re.compile(
    r"app\.(?P<domain>.+)\."
    + SEGMENT
    + r"\.(?P<container>[A-Za-z0-9_-]+)\.(?P<suffix>"
    + "|".join(re.escape(suffix) for suffix in SUFFIXES)
    + r")"
)


def container_label(name: str) -> str | None:
    """
    Args:
        name: A container's name as Docker prints it.

    Returns:
        The name as it is written in a series, or None for a container that
        lives for one command (``compose run``) and gets no series.
    """
    if _ONE_OFF.fullmatch(name):
        return None
    label = _UNSAFE.sub("_", name.strip())[:MAX_NAME]
    return label or None


def container_series(domain: str, container: str, suffix: str) -> str:
    """
    Args:
        domain: The application.
        container: The label from :func:`container_label`.
        suffix: One of :data:`SUFFIXES`.

    Returns:
        The series' name.
    """
    return f"app.{domain}.{SEGMENT}.{container}.{suffix}"


def parse_container_series(series: str) -> tuple[str, str, str] | None:
    """
    Args:
        series: A metric name.

    Returns:
        ``(domain, container, suffix)`` when it is a per-container series,
        else None.
    """
    match = _PARSE.fullmatch(series)
    if match is None:
        return None
    return match.group("domain"), match.group("container"), match.group("suffix")
