# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's health check asks beyond its path, statuses and wait.

The path, the accepted statuses and the wait are columns of ``apps`` since
schema v9 and are written by ``NoustStore.set_app_health``. Everything 4.0 adds
to the check lives here, in the ``app_health`` table (``schema_v14.RUNTIME_SQL``),
one row per application: whether it asks over HTTP or by running a command, the
method, scheme, host name and port of the request, the seconds between probes,
the most probes, and the start period.

A table of its own, not columns of ``apps``, because a deploy writes the whole
``apps`` row it read when it started: a setting an operator changed meanwhile
would be put back. This is the only module that reads or writes the table
(rule 3), and :func:`set_extras` the only writer, so a value the gate could not
use never reaches the row, whoever asked.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.validators.health import (
    check_health_command,
    check_health_host,
    check_health_interval,
    check_health_kind,
    check_health_method,
    check_health_port,
    check_health_retries,
    check_health_scheme,
    check_health_start_period,
)


@dataclass(frozen=True)
class HealthExtras:
    """
    The part of a health check the 4.0 settings add.

    Every field None is the gate's own behaviour: a GET over http to the
    application's port, a probe every two seconds, as many as fit in the wait,
    and no start period.

    Attributes:
        kind: ``http`` (a request) or ``command`` (a program run as the
            application, up when it exits 0).
        method: The request method, upper case.
        scheme: ``http`` or ``https``.
        host: The ``Host:`` header (and TLS server name) the request carries.
        port: The port to connect to on 127.0.0.1, when not the application's.
        command: The command line of a command check.
        service: The Compose service a command check runs in, for a stack.
        interval: Seconds between two probes.
        retries: The most probes after the start period.
        start_period: Seconds during which failures do not count.
    """

    kind: str = "http"
    method: str | None = None
    scheme: str | None = None
    host: str | None = None
    port: int | None = None
    command: str | None = None
    service: str | None = None
    interval: int | None = None
    retries: int | None = None
    start_period: int | None = None

    @property
    def is_default(self) -> bool:
        """Whether every field is the gate's own behaviour."""
        return self == HealthExtras()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields as JSON-ready data.
        """
        return asdict(self)


def validated(extras: HealthExtras) -> HealthExtras:
    """
    Check a health check's extras as a whole.

    Args:
        extras: What the caller asked for.

    Returns:
        The same settings, each value normalised (a method in capitals, a
        host in lower case).

    Raises:
        ValidationError: A value is not one the gate can use, a command check
            names a request setting or has no command, or a request check
            names a command. The error names the field.
    """

    def field(name: str, check: Any, value: Any) -> Any:
        if value is None:
            return None
        try:
            return check(value)
        except ValidationError as exc:
            exc.field = name
            raise

    kind = field("kind", check_health_kind, extras.kind or "http")
    request_only = {
        "method": extras.method,
        "scheme": extras.scheme,
        "host": extras.host,
        "port": extras.port,
    }
    if kind == "command":
        if not extras.command:
            raise ValidationError(
                "A command health check needs a command",
                details="Give the program and its arguments, such as: test -s /app/ready",
                field="command",
            )
        for name, value in request_only.items():
            if value is not None:
                raise ValidationError(
                    f"A command health check has no {name}",
                    details="Those settings belong to a request. Leave them out, or make "
                    "the check an HTTP one.",
                    field=name,
                )
    else:
        for name, value in (("command", extras.command), ("service", extras.service)):
            if value is not None:
                raise ValidationError(
                    f"An HTTP health check has no {name}",
                    details="Leave it out, or make the check a command one.",
                    field=name,
                )
    service = extras.service
    if service is not None:
        from noust.terminal.targets import validate_service

        service = validate_service(service)
    return HealthExtras(
        kind=kind,
        method=field("method", check_health_method, extras.method),
        scheme=field("scheme", check_health_scheme, extras.scheme),
        host=field("host", check_health_host, extras.host),
        port=field("port", check_health_port, extras.port),
        command=field("command", check_health_command, extras.command),
        service=service,
        interval=field("interval", check_health_interval, extras.interval),
        retries=field("retries", check_health_retries, extras.retries),
        start_period=field("start_period", check_health_start_period, extras.start_period),
    )


def get_extras(domain: str, *, store: NoustStore | None = None) -> HealthExtras:
    """
    Read the extras of an application's health check.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        What it set, or the defaults for an application that set nothing.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT h.kind, h.method, h.scheme, h.host, h.port, h.command, h.service, "
            "h.interval_seconds, h.retries, h.start_period "
            "FROM app_health h JOIN apps a ON a.id = h.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return HealthExtras()
    return HealthExtras(
        kind=str(row[0]),
        method=row[1],
        scheme=row[2],
        host=row[3],
        port=row[4],
        command=row[5],
        service=row[6],
        interval=row[7],
        retries=row[8],
        start_period=row[9],
    )


def set_extras(
    domain: str,
    extras: HealthExtras,
    *,
    updated_by: str | None = None,
    store: NoustStore | None = None,
) -> bool:
    """
    Store the extras of an application's health check, all at once.

    The one writer of the table. A check that is all defaults deletes the row,
    so nothing stands in for "never set".

    Args:
        domain: The application's domain.
        extras: The settings; validated here.
        updated_by: Who changed them, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists and the row was written.

    Raises:
        ValidationError: See :func:`validated`; nothing is written.
    """
    checked = validated(extras)
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        app_id = found[0]
        if checked.is_default:
            cursor.execute("DELETE FROM app_health WHERE app_id = ?", (app_id,))
            return True
        cursor.execute(
            "INSERT INTO app_health (app_id, kind, method, scheme, host, port, command, service, "
            "interval_seconds, retries, start_period, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET kind = excluded.kind, method = excluded.method, "
            "scheme = excluded.scheme, host = excluded.host, port = excluded.port, "
            "command = excluded.command, service = excluded.service, "
            "interval_seconds = excluded.interval_seconds, retries = excluded.retries, "
            "start_period = excluded.start_period, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (
                app_id,
                checked.kind,
                checked.method,
                checked.scheme,
                checked.host,
                checked.port,
                checked.command,
                checked.service,
                checked.interval,
                checked.retries,
                checked.start_period,
                updated_by,
                datetime.now().isoformat(),
            ),
        )
    return True
