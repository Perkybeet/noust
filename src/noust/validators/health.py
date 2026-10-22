# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's health check may be told: where to probe, what counts
as up, and for how long to wait.

The store calls these before it writes the settings, so every caller - the
CLI, the API, a script - is held to the same rule, and the health gate never
meets a value it cannot use.
"""

from __future__ import annotations

import re
import shlex

from noust.core.exceptions import ValidationError

#: The fewest and the most seconds an application may be given to come up.
#: Below 5 a process that is merely starting fails the gate; past ten minutes
#: an update holds the application's lock for longer than anyone waits.
HEALTH_TIMEOUT_MIN = 5
HEALTH_TIMEOUT_MAX = 600

#: The HTTP status codes there are.
_STATUS_MIN = 100
_STATUS_MAX = 599

#: One item of an expectation: a status, or an inclusive range of them.
_ITEM = re.compile(r"(?P<low>[0-9]{3})(?:-(?P<high>[0-9]{3}))?")

#: Longer than any health endpoint; long enough to reject a pasted blob.
_PATH_MAX = 1024


def check_health_path(path: str) -> str:
    """
    Accept a path on the application, and nothing that could name another host.

    The gate requests ``http://127.0.0.1:<port>`` followed by this path, so
    it must begin with a single slash (``//host`` would be a host) and be
    printable ASCII: a space or a control character would split or corrupt
    the request line, and anything else has to be percent-encoded anyway.

    Args:
        path: What the operator asked for, such as ``/healthz``.

    Returns:
        The path, unchanged.

    Raises:
        ValidationError: It is not such a path.
    """
    how = "Give a path on the application, such as /healthz or /api/health?deep=1."
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValidationError(
            f"{path!r} is not a path on the application",
            details=f"{how} A scheme or a host is not accepted: the check always asks "
            "the application itself, on 127.0.0.1.",
        )
    if len(path) > _PATH_MAX:
        raise ValidationError(
            f"The health check path is longer than {_PATH_MAX} characters", details=how
        )
    if any(not ("\x21" <= char <= "\x7e") for char in path):
        raise ValidationError(
            f"{path!r} contains a space, a control character or a character outside ASCII",
            details=f"{how} Percent-encode anything else (%20 for a space).",
        )
    return path


def parse_health_expect(expect: str) -> tuple[tuple[int, int], ...]:
    """
    Read a list of statuses and ranges, such as ``200-399`` or ``200,204``.

    Args:
        expect: Comma-separated statuses (``204``) and inclusive ranges
            (``200-299``), each within 100-599.

    Returns:
        The ranges, a single status as a range of one.

    Raises:
        ValidationError: It is anything else.
    """
    how = (
        "Use statuses and ranges from 100 to 599, separated by commas: 200-399, or 200,204, "
        "or 200-299,301."
    )
    ranges: list[tuple[int, int]] = []
    items = expect.split(",") if isinstance(expect, str) else [""]
    for raw in items:
        item = raw.strip()
        match = _ITEM.fullmatch(item)
        if match is None:
            raise ValidationError(f"{expect!r} is not a list of HTTP statuses", details=how)
        low = int(match["low"])
        high = int(match["high"] or low)
        if not (_STATUS_MIN <= low <= high <= _STATUS_MAX):
            raise ValidationError(f"{item!r} is not a status range from 100 to 599", details=how)
        ranges.append((low, high))
    return tuple(ranges)


def check_health_expect(expect: str) -> str:
    """
    Accept a list of statuses and ranges, written the one way the store keeps it.

    Args:
        expect: See :func:`parse_health_expect`.

    Returns:
        The same list without spaces: ``200-299,301``.

    Raises:
        ValidationError: See :func:`parse_health_expect`.
    """
    return ",".join(
        str(low) if low == high else f"{low}-{high}" for low, high in parse_health_expect(expect)
    )


def check_health_timeout(timeout: int) -> int:
    """
    Accept how many seconds an application gets to come up.

    Args:
        timeout: Seconds.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from 5 to 600.
    """
    # bool is an int to Python, and True seconds is not something anyone meant.
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not HEALTH_TIMEOUT_MIN <= timeout <= HEALTH_TIMEOUT_MAX
    ):
        raise ValidationError(
            f"A health check timeout of {timeout!r} seconds is out of range",
            details=f"Give it from {HEALTH_TIMEOUT_MIN} to {HEALTH_TIMEOUT_MAX} seconds.",
        )
    return timeout


#: The methods a health probe may use. Only ones that do not change anything
#: an application holds: a probe runs on every activation, every two seconds.
HEALTH_METHODS = ("GET", "HEAD", "OPTIONS", "POST")

#: The schemes a probe may speak. It always connects to 127.0.0.1.
HEALTH_SCHEMES = ("http", "https")

#: The kinds of check: a request, or a command run as the application.
HEALTH_KINDS = ("http", "command")

#: Seconds between two probes, and the most a gate may be asked to wait for.
HEALTH_INTERVAL_MIN = 1
HEALTH_INTERVAL_MAX = 60

#: The fewest and the most probes one activation makes.
HEALTH_RETRIES_MIN = 1
HEALTH_RETRIES_MAX = 100

#: The longest an application may be given before its failures count.
HEALTH_START_PERIOD_MAX = 600

#: A host name as a ``Host:`` header carries it (no port, no path).
_HOST = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)

#: The longest command line a command check may be.
_COMMAND_MAX = 1024


def _whole_number(value: object, low: int, high: int, *, what: str, unit: str) -> int:
    """
    Accept a whole number inside a range.

    Args:
        value: What the caller gave.
        low: The fewest.
        high: The most.
        what: What it is, for the refusal.
        unit: How it is counted, for the refusal.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from ``low`` to ``high``.
    """
    # bool is an int to Python, and True seconds is not something anyone meant.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(
            f"A health check {what} of {value!r} is out of range",
            details=f"Give it from {low} to {high} {unit}.",
        )
    return value


def check_health_kind(kind: str) -> str:
    """
    Accept the kind of check.

    Args:
        kind: ``http`` or ``command``.

    Returns:
        The same kind.

    Raises:
        ValidationError: It is neither.
    """
    if kind not in HEALTH_KINDS:
        raise ValidationError(
            f"{kind!r} is not a kind of health check",
            details=f"Use one of: {', '.join(HEALTH_KINDS)}.",
        )
    return kind


def check_health_method(method: str) -> str:
    """
    Accept the method a probe requests with.

    Args:
        method: ``GET``, ``HEAD``, ``OPTIONS`` or ``POST``, in any case.

    Returns:
        The method in capitals.

    Raises:
        ValidationError: It is none of those.
    """
    upper = method.upper() if isinstance(method, str) else ""
    if upper not in HEALTH_METHODS:
        raise ValidationError(
            f"{method!r} is not a method a health check may use",
            details=f"Use one of: {', '.join(HEALTH_METHODS)}.",
        )
    return upper


def check_health_scheme(scheme: str) -> str:
    """
    Accept the scheme a probe speaks.

    Args:
        scheme: ``http`` or ``https``, in any case.

    Returns:
        The scheme in lower case.

    Raises:
        ValidationError: It is neither.
    """
    lower = scheme.lower() if isinstance(scheme, str) else ""
    if lower not in HEALTH_SCHEMES:
        raise ValidationError(
            f"{scheme!r} is not a scheme a health check may use",
            details="Use http or https. The check still connects to 127.0.0.1: the scheme "
            "is only how it speaks to the application.",
        )
    return lower


def check_health_host(host: str) -> str:
    """
    Accept the host name a probe presents.

    The probe connects to 127.0.0.1 whatever this says; the name is only the
    ``Host:`` header (and the TLS server name), for an application that
    answers one name and refuses the address.

    Args:
        host: A host name such as ``shop.example.com``.

    Returns:
        The name in lower case.

    Raises:
        ValidationError: It is not a host name.
    """
    if not isinstance(host, str) or not host or len(host) > 253 or _HOST.fullmatch(host) is None:
        raise ValidationError(
            f"{host!r} is not a host name",
            details="Give the name the application answers to, such as shop.example.com, "
            "without a scheme, a port or a path. The check connects to 127.0.0.1 either way.",
        )
    return host.lower()


def check_health_port(port: int) -> int:
    """
    Accept the port a probe connects to, when it is not the application's own.

    Args:
        port: A TCP port.

    Returns:
        The same port.

    Raises:
        ValidationError: It is not a port from 1 to 65535.
    """
    return _whole_number(port, 1, 65535, what="port", unit="(TCP ports)")


def check_health_interval(seconds: int) -> int:
    """
    Accept the seconds between two probes.

    Args:
        seconds: Seconds.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from 1 to 60.
    """
    return _whole_number(
        seconds, HEALTH_INTERVAL_MIN, HEALTH_INTERVAL_MAX, what="interval", unit="seconds"
    )


def check_health_retries(retries: int) -> int:
    """
    Accept how many probes one activation makes at most.

    Args:
        retries: A count.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from 1 to 100.
    """
    return _whole_number(
        retries, HEALTH_RETRIES_MIN, HEALTH_RETRIES_MAX, what="retry count", unit="probes"
    )


def check_health_start_period(seconds: int) -> int:
    """
    Accept the seconds an application gets before a failed probe counts.

    Args:
        seconds: Seconds; 0 is no grace.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from 0 to 600.
    """
    return _whole_number(seconds, 0, HEALTH_START_PERIOD_MAX, what="start period", unit="seconds")


def check_health_command(command: str) -> str:
    """
    Accept a command that answers whether the application is up.

    It is an argument vector, split like a POSIX shell would split the line
    but never run by one: ``test -s /app/ready`` is the program ``test`` with
    two arguments. It exits 0 when the application is up.

    Args:
        command: The command line.

    Returns:
        The same line, stripped.

    Raises:
        ValidationError: It is empty, cannot be split, or holds a control
            character.
    """
    how = "Give a program and its arguments, such as: test -s /var/run/app/ready"
    if not isinstance(command, str) or not command.strip():
        raise ValidationError("A command health check needs a command", details=how)
    text = command.strip()
    if len(text) > _COMMAND_MAX:
        raise ValidationError(
            f"The health check command is longer than {_COMMAND_MAX} characters", details=how
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise ValidationError("The health check command holds a control character", details=how)
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ValidationError(f"Cannot read the command: {exc}", details=how) from exc
    if not argv:
        raise ValidationError("A command health check needs a command", details=how)
    return text
