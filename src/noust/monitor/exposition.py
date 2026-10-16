# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The newest value of every metric, in the format Prometheus reads (checklist G-06).

The history Noust keeps (:mod:`noust.monitor.timeseries`) answers the console;
a Prometheus, a Grafana Agent, a Netdata or an OpenTelemetry Collector asks a
different question, "what is it now?", every fifteen seconds, in a text format
they all read. This module is that answer. It is pure: the caller hands it the
newest values and it writes the text, so what a scrape sees is tested without a
server.

Two formats from one list of families. The classic Prometheus text exposition
(``text/plain; version=0.0.4``) and OpenMetrics (``application/openmetrics-text``),
which differ here in the terminator (``# EOF``) and the content type; every
Noust metric is a gauge because the collector has already turned the kernel's
counters into rates.

Names follow the usual conventions (``noust_`` prefix, the unit as a suffix,
``_per_second`` for a rate) and the series of the history are mapped, never
passed through: a metric nobody described here is not exported, so a new series
cannot change a scrape's shape or its cardinality by accident. An application's
series carry ``app``, a stack's containers ``app`` and ``container``, a database's
``engine`` and ``database``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from noust.monitor.series import parse_container_series

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
OPENMETRICS_CONTENT_TYPE = "application/openmetrics-text; version=1.0.0; charset=utf-8"


@dataclass(frozen=True)
class Family:
    """
    One exported metric.

    Attributes:
        name: Its name in the exposition.
        help: What it is, in a sentence.
    """

    name: str
    help: str


@dataclass(frozen=True)
class Sample:
    """
    One value of a family.

    Attributes:
        family: The family.
        value: The value.
        labels: The labels, in the order they are written.
    """

    family: Family
    value: float
    labels: tuple[tuple[str, str], ...] = ()


def _f(name: str, help_text: str) -> Family:
    """
    Args:
        name: The metric's name, without the prefix.
        help_text: What it is.

    Returns:
        The family.
    """
    return Family(f"noust_{name}", help_text)


#: The series of the machine.
SERVER: dict[str, Family] = {
    "cpu.percent": _f("cpu_percent", "CPU in use on the server, percent of all cores."),
    "mem.used_bytes": _f("memory_used_bytes", "Memory in use on the server, in bytes."),
    "mem.total_bytes": _f("memory_total_bytes", "Memory of the server, in bytes."),
    "swap.used_bytes": _f("swap_used_bytes", "Swap in use on the server, in bytes."),
    "swap.total_bytes": _f("swap_total_bytes", "Swap of the server, in bytes."),
    "disk.used_bytes": _f(
        "disk_used_bytes", "Disk in use on the filesystem the applications live on, in bytes."
    ),
    "disk.total_bytes": _f(
        "disk_total_bytes", "Size of the filesystem the applications live on, in bytes."
    ),
    "net.rx_bytes_s": _f(
        "network_receive_bytes_per_second", "Bytes the server receives per second."
    ),
    "net.tx_bytes_s": _f("network_transmit_bytes_per_second", "Bytes the server sends per second."),
    "load.1m": _f("load1", "Load average over one minute."),
}

#: What is measured of an application, by the end of its series' name.
APPLICATION: dict[str, Family] = {
    "cpu.percent": _f("app_cpu_percent", "CPU of an application, percent of one core."),
    "mem.bytes": _f("app_memory_bytes", "Working-set memory of an application, in bytes."),
    "http.requests_per_min": _f(
        "app_http_requests_per_minute", "Requests per minute counted from the access log."
    ),
    "http.errors_5xx_per_min": _f(
        "app_http_errors_5xx_per_minute", "Responses with a 5xx status per minute."
    ),
    "io.read_bytes_s": _f(
        "app_disk_read_bytes_per_second", "Bytes an application reads from disk per second."
    ),
    "io.write_bytes_s": _f(
        "app_disk_write_bytes_per_second", "Bytes an application writes to disk per second."
    ),
    "net.rx_bytes_s": _f(
        "app_network_receive_bytes_per_second", "Bytes an application receives per second."
    ),
    "net.tx_bytes_s": _f(
        "app_network_transmit_bytes_per_second", "Bytes an application sends per second."
    ),
}

#: What is measured of each container of a Compose stack.
CONTAINER: dict[str, Family] = {
    "cpu.percent": _f("container_cpu_percent", "CPU of a container, percent of one core."),
    "mem.bytes": _f("container_memory_bytes", "Working-set memory of a container, in bytes."),
    "io.read_bytes_s": _f(
        "container_disk_read_bytes_per_second", "Bytes a container reads from disk per second."
    ),
    "io.write_bytes_s": _f(
        "container_disk_write_bytes_per_second", "Bytes a container writes to disk per second."
    ),
    "net.rx_bytes_s": _f(
        "container_network_receive_bytes_per_second", "Bytes a container receives per second."
    ),
    "net.tx_bytes_s": _f(
        "container_network_transmit_bytes_per_second", "Bytes a container sends per second."
    ),
}

#: What is measured of a database engine or of one database.
DATABASE: dict[str, Family] = {
    "size": _f("database_size_bytes", "Size of a database, in bytes."),
    "connections": _f("database_connections", "Open connections."),
    "cache_hit": _f("database_cache_hit_percent", "Reads served from memory, percent."),
    "tps": _f("database_transactions_per_second", "Transactions per second."),
    "qps": _f("database_queries_per_second", "Queries per second."),
    "memory": _f("database_memory_bytes", "Memory the engine uses, in bytes."),
    "ops": _f("database_operations_per_second", "Operations per second."),
}

#: Whether the history is being recorded, which tells a scraper the other
#: values are fresh (they are left out when they are not).
RECORDING = _f("metrics_recording", "1 while the metrics history is being recorded, else 0.")

#: The labels of ``noust_info``.
INFO = Family("noust_info", "Constant 1, labelled with the version and the server's name.")

_APPLICATION_SERIES = re.compile(
    r"app\.(?P<domain>.+)\.(?P<suffix>" + "|".join(re.escape(s) for s in APPLICATION) + r")"
)
_DATABASE_SERIES = re.compile(
    r"db\.(?P<engine>[a-z0-9@_-]+)\.(?:(?P<database>.+)\.)?(?P<metric>"
    + "|".join(re.escape(m) for m in DATABASE)
    + r")"
)

_LABEL_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"', "\n": "\\n"})


def classify(series: str, value: float) -> Sample | None:
    """
    Turn one series of the history into a sample of the exposition.

    Args:
        series: The metric's name in the history.
        value: Its newest value.

    Returns:
        The sample, or None for a series nobody described (it is not exported).
    """
    if not math.isfinite(value):
        return None
    if series in SERVER:
        return Sample(SERVER[series], value)
    container = parse_container_series(series)
    if container is not None:
        domain, name, suffix = container
        return Sample(CONTAINER[suffix], value, (("app", domain), ("container", name)))
    match = _APPLICATION_SERIES.fullmatch(series)
    if match is not None:
        return Sample(APPLICATION[match.group("suffix")], value, (("app", match.group("domain")),))
    match = _DATABASE_SERIES.fullmatch(series)
    if match is not None:
        labels = [("engine", match.group("engine"))]
        if match.group("database"):
            labels.append(("database", match.group("database")))
        return Sample(DATABASE[match.group("metric")], value, tuple(labels))
    return None


def samples(values: Mapping[str, float]) -> list[Sample]:
    """
    Args:
        values: Series to its newest value.

    Returns:
        The samples that can be exported, in a stable order.
    """
    found = [sample for name, value in values.items() if (sample := classify(name, value))]
    return sorted(found, key=lambda s: (s.family.name, s.labels))


def _number(value: float) -> str:
    """
    Args:
        value: A finite number.

    Returns:
        It as the exposition writes it: an integer without a decimal point.
    """
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(float(value))


def render(
    found: Sequence[Sample],
    *,
    info: Mapping[str, str] | None = None,
    recording: bool | None = None,
    openmetrics: bool = False,
) -> str:
    """
    Write samples in the text format.

    Args:
        found: The samples (:func:`samples`).
        info: Labels of ``noust_info`` (the version, the server's name); omitted
            when None.
        recording: Whether the history is being recorded, as
            ``noust_metrics_recording``; omitted when None.
        openmetrics: Write OpenMetrics (with its ``# EOF``) rather than the
            classic Prometheus text.

    Returns:
        The exposition, a family's samples together under one ``HELP`` and ``TYPE``.
    """
    lines: list[str] = []
    everything = list(found)
    if info is not None:
        everything.append(Sample(INFO, 1.0, tuple(info.items())))
    if recording is not None:
        everything.append(Sample(RECORDING, 1.0 if recording else 0.0))
    everything.sort(key=lambda s: (s.family.name, s.labels))
    previous = ""
    for sample in everything:
        if sample.family.name != previous:
            previous = sample.family.name
            lines.append(f"# HELP {previous} {sample.family.help}")
            lines.append(f"# TYPE {previous} gauge")
        labels = ",".join(
            f'{name}="{value.translate(_LABEL_ESCAPES)}"' for name, value in sample.labels
        )
        lines.append(
            f"{previous}{{{labels}}} {_number(sample.value)}"
            if labels
            else f"{previous} {_number(sample.value)}"
        )
    if openmetrics:
        lines.append("# EOF")
    return "\n".join(lines) + "\n"


def wants_openmetrics(accept: str | None) -> bool:
    """
    Args:
        accept: The request's ``Accept`` header.

    Returns:
        Whether the scraper prefers OpenMetrics, which Prometheus announces when
        it is configured to ask for it.
    """
    return bool(accept) and "application/openmetrics-text" in str(accept)
