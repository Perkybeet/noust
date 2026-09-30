# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit trail: one way to record an event, and everything that keeps it.

Everything that should be on record goes through :func:`record`, with a name
from the closed catalog (:mod:`noust.core.audit.catalog`) and an
:class:`Actor`::

    from noust.core.audit import Actor, record

    record("apps.delete", actor=actor, target="app:shop.example.com")
    record("apps.env.reveal", target="app:shop.example.com", details={"keys": 3})

When no actor is given, the one the console bound for the request, the CLI for
the command or the job manager for the job is used (:func:`bind`), and so is
its correlation id, which links an event to the host actions it caused
(:mod:`noust.core.audit.ledger`).

The pieces:

- :mod:`~noust.core.audit.log`: the chained file, its rotation and retention.
- :mod:`~noust.core.audit.chain`: the HMAC chain and ``noust audit verify``,
  including what it cannot promise (root can rewrite a local chain).
- :mod:`~noust.core.audit.sinks` and :mod:`~noust.core.audit.shipper`:
  journald, RFC 5424 syslog (UNIX, UDP, TCP, TLS) and stdout, from a cursor.
- :mod:`~noust.core.audit.flood`: anonymous floods counted, never dropped
  silently.
- :func:`health`: whether any of it is failing, for ``noust health`` and the
  console.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.audit import status
from noust.core.audit.actor import ACTOR_KINDS, Actor, ActorKind, cli_actor
from noust.core.audit.catalog import EVENTS, EventSpec, UnknownAuditEvent, is_known, spec_for
from noust.core.audit.context import (
    bind,
    current_actor,
    current_correlation_id,
    new_correlation_id,
    run_with,
)
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings, load_settings

__all__ = [
    "ACTOR_KINDS",
    "EVENTS",
    "Actor",
    "ActorKind",
    "AuditHealth",
    "AuditLog",
    "AuditSettings",
    "EventSpec",
    "UnknownAuditEvent",
    "bind",
    "cli_actor",
    "current_actor",
    "current_correlation_id",
    "default_log_path",
    "get_log",
    "health",
    "install",
    "is_known",
    "load_settings",
    "new_correlation_id",
    "record",
    "reset",
    "run_with",
    "spec_for",
]

#: File name of the log in the console's state directory; 3.0's name, kept.
LOG_NAME = "web-audit.log"

_installed: AuditLog | None = None
_default: AuditLog | None = None


def default_log_path() -> Path:
    """
    Where the audit log lives when nothing installed another one.

    The console's state directory, resolved the way the console resolves it:
    ``NOUST_WEB_STATE_DIR`` (or ``WASM_WEB_STATE_DIR``), else the
    configuration directory. A CLI command and the console therefore write
    to the same chain.

    Returns:
        The log file path.
    """
    state = paths.getenv("WEB_STATE_DIR")
    return Path(state) / LOG_NAME if state else paths.config_dir() / LOG_NAME


def install(log: AuditLog | None) -> None:
    """
    Make one log the process's audit log.

    The console installs the log it opened at start; None returns to the
    default location.

    Args:
        log: The log, or None.
    """
    global _installed
    _installed = log


def get_log() -> AuditLog:
    """
    The process's audit log.

    Returns:
        The installed log, else one at :func:`default_log_path`, opened on
        first use and reopened when that path changes.
    """
    global _default
    if _installed is not None:
        return _installed
    path = default_log_path()
    if _default is None or _default.path != path:
        _default = AuditLog(path)
    return _default


def reset() -> None:
    """Forget the installed and default logs and the failure state; for tests."""
    global _installed, _default
    _installed = None
    _default = None
    status.reset()


def record(
    event: str,
    *,
    actor: Actor | None = None,
    target: str | None = None,
    outcome: str = "ok",
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> None:
    """
    Record one audit event.

    Never raises for a failing log: a failure is logged and kept for
    :func:`health`, because an action must not fail for being recorded.

    Args:
        event: A name from :data:`~noust.core.audit.catalog.EVENTS`. An
            unknown one is recorded as ``audit.unknown_event``.
        actor: Who acted; the bound actor, else ``system``, when None.
        target: What it was done to, such as ``app:shop.example.com``.
        outcome: ``ok``, ``failure``, ``denied``... A failure or a refusal is
            shipped at warning severity at least.
        details: Structured context. Values under secret-looking names are
            replaced and sizes are bounded before anything is written.
        correlation_id: Links the events of one request, command or job; the
            bound one when None.
    """
    get_log().append(
        event,
        actor=actor,
        target=target,
        outcome=outcome,
        details=details,
        correlation_id=correlation_id,
    )


@dataclass
class AuditHealth:
    """
    Whether the audit trail works, and what to do when it does not.

    Attributes:
        status: ``ok``, ``warning`` or ``error``.
        problems: One actionable sentence per problem, worst first.
        log_path: The log file.
        total_bytes: Size of the whole log.
        failing: Writes are failing right now.
        failures: Failed writes since this process started.
        last_failure: The last write error, verbatim.
        last_failure_at: When it happened.
        sinks: Each shipping destination's state.
    """

    status: str
    problems: list[str] = field(default_factory=list)
    log_path: str = ""
    total_bytes: int = 0
    failing: bool = False
    failures: int = 0
    last_failure: str | None = None
    last_failure_at: str | None = None
    sinks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        The report as JSON-ready data.

        Returns:
            Every field.
        """
        return asdict(self)


def health(log: AuditLog | None = None) -> AuditHealth:
    """
    Report whether the audit trail works.

    This is the flag ``noust health``, ``noust audit status`` and the console
    read: failed writes in this process, a log directory that cannot be
    written, a key others can read, a log over its size limit, destinations
    that stopped receiving, and configuration values that were replaced.

    Args:
        log: The log to check; the process's own by default.

    Returns:
        The report.
    """
    from noust.core.audit.shipper import sink_report

    log = log or get_log()
    errors: list[str] = []
    warnings: list[str] = []
    failing, failures, last_failure, last_failure_at = status.failure()
    if failing:
        errors.append(
            f"Audit events are not being written: {last_failure}. Fix the cause (disk space, "
            "permissions on the state directory) and the next event will say so."
        )
    directory = log.path.parent
    if not directory.is_dir():
        warnings.append(f"The audit log directory {directory} does not exist yet.")
    elif not os.access(directory, os.W_OK):
        errors.append(
            f"The audit log directory {directory} is not writable by this process; run as root."
        )
    try:
        mode = log.key_path.stat().st_mode & 0o777
    except OSError:
        mode = None
    if mode is not None and mode & 0o077:
        errors.append(
            f"The audit key {log.key_path} is readable by others (mode {mode:o}): "
            f"chmod 600 {log.key_path}."
        )
    total = log.total_bytes()
    settings = log.settings
    if total > settings.max_total_bytes:
        warnings.append(
            f"The audit log is {total // (1024 * 1024)} MiB, over audit.max_total_mb "
            f"({settings.max_total_mb}); anonymous events are being counted instead of written. "
            "Ship it off the machine and lower audit.retention_days, or raise the limit."
        )
    sinks = sink_report(log)
    for sink in sinks:
        if sink.get("degraded"):
            errors.append(
                f"The audit destination {sink['sink_id']} has not received events for "
                f"{int(sink.get('lag_seconds') or 0) // 60} minutes: {sink.get('error') or 'no error'}."
            )
        elif sink.get("error"):
            warnings.append(f"The audit destination {sink['sink_id']} failed: {sink['error']}.")
    warnings.extend(settings.problems)
    verdict = "error" if errors else "warning" if warnings else "ok"
    return AuditHealth(
        status=verdict,
        problems=errors + warnings,
        log_path=str(log.path),
        total_bytes=total,
        failing=failing,
        failures=failures,
        last_failure=last_failure,
        last_failure_at=last_failure_at,
        sinks=sinks,
    )


# The noust.audit logger had no handler (finding H2): its lines went nowhere.
# Importing the package routes them into the trail.
from noust.core.audit.bridge import install_bridge  # noqa: E402

install_bridge()
