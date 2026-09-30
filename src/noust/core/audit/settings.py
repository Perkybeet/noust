# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``audit.*`` and ``retention.*`` settings, read and checked in one place.

A wrong value never switches auditing off: it is reported (``noust audit
status``, ``noust health``, the console) and the default is used instead,
because an audit trail that stops over a typo in ``config.yaml`` is the
failure this module exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core import paths

#: RFC 5612's documentation number, which RFC 5424 uses in its own examples.
#: Valid on the wire, but it names nobody: an operator shipping to a SIEM
#: should set the Private Enterprise Number of their organisation.
DOCUMENTATION_ENTERPRISE_ID = 32473

#: The shortest retention accepted. ENS asks the operator to decide; below
#: three months an investigation usually starts after its evidence is gone.
MIN_RETENTION_DAYS = 90

HOST_ACTIVITY_LEVELS = ("off", "mutations", "all")
SWITCH_VALUES = ("auto", "on", "off")
SYSLOG_TRANSPORTS = ("unix", "udp", "tcp", "tls")

#: Syslog facility "log audit" (RFC 5424, table 1).
AUDIT_FACILITY = 13

_DEFAULT_PORTS = {"udp": 514, "tcp": 514, "tls": 6514}
_SINK_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SyslogDestination:
    """
    One RFC 5424 receiver.

    Attributes:
        transport: ``unix``, ``udp``, ``tcp`` (octet counting, RFC 6587) or
            ``tls`` (RFC 5425).
        host: Receiver host for the network transports.
        port: Receiver port for the network transports.
        path: Socket path for ``unix``.
        facility: Syslog facility of every message.
        ca: CA bundle the receiver's certificate must chain to; the only
            trust anchor when set (the system store is not consulted).
        client_cert: Client certificate for mutual TLS.
        client_key: Its private key.
        pin_sha256: Hex SHA-256 of the receiver's certificate, checked on
            top of the chain when set.
        server_name: Name the certificate must carry; the host by default.
        backfill: Send the retained history on first contact, rather than
            starting from the events written from now on.
    """

    transport: str
    host: str | None = None
    port: int | None = None
    path: str | None = None
    facility: int = AUDIT_FACILITY
    ca: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    pin_sha256: str | None = None
    server_name: str | None = None
    backfill: bool = False

    @property
    def sink_id(self) -> str:
        """A stable name for this destination's shipping cursor."""
        where = self.path if self.transport == "unix" else f"{self.host}:{self.port}"
        return _SINK_ID_UNSAFE.sub("_", f"syslog-{self.transport}-{where}")


@dataclass(frozen=True)
class AuditSettings:
    """
    How the audit trail is kept and shipped.

    Attributes:
        retention_days: How long events are kept.
        max_total_mb: Size above which the trail is reported as degraded and
            anonymous informational events stop being written. Nothing
            within the retention period is ever deleted to make room.
        rotate_mb: Size at which the current file is closed; files are also
            closed every day.
        host_activity: What the host action ledger records: ``off``,
            ``mutations`` (processes and files that change the system) or
            ``all`` (read-only probes too).
        flood_window_seconds: Window in which repeated anonymous events with
            the same event, source and target are counted.
        flood_burst: Occurrences written in full within one window before the
            rest are counted.
        journald: ``auto`` ships to journald when its socket exists.
        stdout: ``auto`` ships to the console's standard output inside the
            central's container.
        syslog: RFC 5424 receivers.
        enterprise_id: The IANA Private Enterprise Number in the structured
            data ID ``noust@<enterprise_id>``.
        checkpoint_minutes: How often the chain's head is written as an
            ``audit.checkpoint`` event, when something was written since.
        sink_lag_minutes: How far behind a destination may fall before it is
            reported as degraded.
        problems: What was wrong in the configuration, and what was used
            instead.
    """

    retention_days: int = 365
    max_total_mb: int = 2048
    rotate_mb: int = 64
    host_activity: str = "mutations"
    flood_window_seconds: int = 60
    flood_burst: int = 10
    journald: str = "auto"
    stdout: str = "auto"
    syslog: tuple[SyslogDestination, ...] = ()
    enterprise_id: int = DOCUMENTATION_ENTERPRISE_ID
    checkpoint_minutes: int = 5
    sink_lag_minutes: int = 15
    problems: tuple[str, ...] = field(default=(), compare=False)

    @property
    def rotate_bytes(self) -> int:
        """:attr:`rotate_mb` in bytes."""
        return self.rotate_mb * 1024 * 1024

    @property
    def max_total_bytes(self) -> int:
        """:attr:`max_total_mb` in bytes."""
        return self.max_total_mb * 1024 * 1024

    def stdout_enabled(self) -> bool:
        """
        Whether events go to standard output.

        Returns:
            True when switched on, or on ``auto`` inside the central's
            container (``NOUST_DATA_DIR`` set), where there is no journald
            and the container log is the one the operator collects.
        """
        if self.stdout == "auto":
            return paths.DATA_DIR is not None
        return self.stdout == "on"


@dataclass(frozen=True)
class RetentionSettings:
    """
    How long Noust keeps its other records (ENS G19).

    Attributes:
        jobs_days: Finished background jobs and their logs.
        deployments_days: Deployment history rows and their logs.
        sessions_days: Ended console sessions, where the session store keeps
            them at all.
        observations_days: The monitor's observations; this is
            ``monitor.retention_days``, read here so there is one setting.
    """

    jobs_days: int = 90
    deployments_days: int = 365
    sessions_days: int = 30
    observations_days: int = 30


def read_setting(config: Any, key: str, default: Any) -> Any:
    """
    Read one key, tolerating a configuration object that cannot answer.

    Every command now reads the audit settings, including commands under test
    with a stand-in configuration that only knows how to save; an audit
    setting it cannot answer is its default, never a crash of the command.

    Args:
        config: A :class:`~noust.core.config.Config` or a stand-in.
        key: Dotted key.
        default: What to return when it cannot be read.

    Returns:
        The value, or ``default``.
    """
    get = getattr(config, "get", None)
    return get(key, default) if callable(get) else default


def _integer(
    raw: dict[str, Any], key: str, default: int, minimum: int, problems: list[str], prefix: str
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        problems.append(f"{prefix}.{key} must be a whole number; using {default}.")
        return default
    try:
        number = int(value)
    except ValueError:
        problems.append(f"{prefix}.{key} must be a whole number, not {value!r}; using {default}.")
        return default
    if number < minimum:
        problems.append(
            f"{prefix}.{key} is {number}, below the minimum {minimum}; using {minimum}."
        )
        return minimum
    return number


def _choice(
    raw: dict[str, Any], key: str, default: str, allowed: tuple[str, ...], problems: list[str]
) -> str:
    value = str(raw.get(key, default)).strip().lower()
    if value in ("true", "yes"):
        value = "on"
    if value in ("false", "no"):
        value = "off"
    if value not in allowed:
        problems.append(
            f"audit.{key} must be one of {', '.join(allowed)}, not {raw.get(key)!r}; "
            f"using {default}."
        )
        return default
    return value


def _split_address(address: str) -> tuple[str | None, str]:
    """
    Split ``host:port``, ``[v6]:port``, ``[v6]`` or a bare host.

    Args:
        address: The configured address.

    Returns:
        The host (None when empty) and the port text (empty when absent).
    """
    if address.startswith("["):
        host, _, rest = address[1:].partition("]")
        return host or None, rest[1:] if rest.startswith(":") else ""
    if address.count(":") == 1:
        host, _, port = address.partition(":")
        return host or None, port
    return address or None, ""


def _destination(entry: Any, index: int, problems: list[str]) -> SyslogDestination | None:
    label = f"audit.syslog[{index}]"
    if not isinstance(entry, dict):
        problems.append(f"{label} must be a mapping with a transport and an address; ignored.")
        return None
    transport = str(entry.get("transport", "")).strip().lower()
    if transport not in SYSLOG_TRANSPORTS:
        problems.append(
            f"{label}.transport must be one of {', '.join(SYSLOG_TRANSPORTS)}; ignored."
        )
        return None
    address = str(entry.get("address", "")).strip()
    host: str | None = None
    port: int | None = None
    path: str | None = None
    if transport == "unix":
        path = address or "/dev/log"
    else:
        host, port_text = _split_address(address)
        if host is None:
            problems.append(f"{label}.address must be host:port; ignored.")
            return None
        try:
            port = int(port_text) if port_text else _DEFAULT_PORTS[transport]
        except ValueError:
            problems.append(f"{label}.address has an invalid port {port_text!r}; ignored.")
            return None
    facility = entry.get("facility", AUDIT_FACILITY)
    if not isinstance(facility, int) or not 0 <= facility <= 23:
        problems.append(f"{label}.facility must be 0 to 23; using {AUDIT_FACILITY}.")
        facility = AUDIT_FACILITY

    def text(key: str) -> str | None:
        value = entry.get(key)
        return str(value).strip() or None if value is not None else None

    if transport != "tls" and any(text(key) for key in ("ca", "client_cert", "client_key")):
        problems.append(f"{label}: ca, client_cert and client_key only apply to transport tls.")
    return SyslogDestination(
        transport=transport,
        host=host,
        port=port,
        path=path,
        facility=facility,
        ca=text("ca"),
        client_cert=text("client_cert"),
        client_key=text("client_key"),
        pin_sha256=(text("pin_sha256") or "").replace(":", "").lower() or None,
        server_name=text("server_name"),
        backfill=bool(entry.get("backfill", False)),
    )


def load_settings(config: Any | None = None) -> AuditSettings:
    """
    Read ``audit.*`` from the configuration.

    Args:
        config: A :class:`~noust.core.config.Config`; the process-wide one by
            default.

    Returns:
        The settings, with every invalid value replaced by its default and
        described in :attr:`AuditSettings.problems`.
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    raw = read_setting(config, "audit", {}) or {}
    if not isinstance(raw, dict):
        return AuditSettings(problems=("audit must be a mapping; using the defaults.",))
    problems: list[str] = []
    defaults = AuditSettings()
    destinations = raw.get("syslog") or []
    if not isinstance(destinations, list):
        problems.append("audit.syslog must be a list of receivers; ignored.")
        destinations = []
    syslog = tuple(
        destination
        for index, entry in enumerate(destinations)
        if (destination := _destination(entry, index, problems)) is not None
    )
    settings = AuditSettings(
        retention_days=_integer(
            raw, "retention_days", defaults.retention_days, MIN_RETENTION_DAYS, problems, "audit"
        ),
        max_total_mb=_integer(raw, "max_total_mb", defaults.max_total_mb, 64, problems, "audit"),
        rotate_mb=_integer(raw, "rotate_mb", defaults.rotate_mb, 1, problems, "audit"),
        host_activity=_choice(
            raw, "host_activity", defaults.host_activity, HOST_ACTIVITY_LEVELS, problems
        ),
        flood_window_seconds=_integer(
            raw, "flood_window_seconds", defaults.flood_window_seconds, 1, problems, "audit"
        ),
        flood_burst=_integer(raw, "flood_burst", defaults.flood_burst, 1, problems, "audit"),
        journald=_choice(raw, "journald", defaults.journald, SWITCH_VALUES, problems),
        stdout=_choice(raw, "stdout", defaults.stdout, SWITCH_VALUES, problems),
        syslog=syslog,
        enterprise_id=_integer(raw, "enterprise_id", defaults.enterprise_id, 1, problems, "audit"),
        checkpoint_minutes=_integer(
            raw, "checkpoint_minutes", defaults.checkpoint_minutes, 1, problems, "audit"
        ),
        sink_lag_minutes=_integer(
            raw, "sink_lag_minutes", defaults.sink_lag_minutes, 1, problems, "audit"
        ),
    )
    # The security profile's floor (noust.core.ens.profile): under ens-medium
    # events are kept a year at least, whatever the key says.
    from noust.core.ens.profile import defaults_for

    floor = defaults_for(read_setting(config, "security.profile", None))
    if floor.ens and settings.retention_days < floor.audit_retention_days:
        problems.append(
            f"audit.retention_days is {settings.retention_days}; the {floor.name} security "
            f"profile keeps events {floor.audit_retention_days} days at least, which applies."
        )
        settings = replace(settings, retention_days=floor.audit_retention_days)
    if syslog and settings.enterprise_id == DOCUMENTATION_ENTERPRISE_ID:
        problems.append(
            f"audit.enterprise_id is {DOCUMENTATION_ENTERPRISE_ID}, the number reserved for "
            "documentation: set your organisation's IANA Private Enterprise Number so a SIEM "
            "can tell Noust's structured data from anyone else's."
        )
    if problems:
        return AuditSettings(**{**settings.__dict__, "problems": tuple(problems)})
    return settings


def load_retention(config: Any | None = None) -> RetentionSettings:
    """
    Read ``retention.*`` (and ``monitor.retention_days``) from the configuration.

    Args:
        config: A :class:`~noust.core.config.Config`; the process-wide one by
            default.

    Returns:
        The settings; an invalid value falls back to its default.
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    raw = read_setting(config, "retention", {}) or {}
    raw = raw if isinstance(raw, dict) else {}
    problems: list[str] = []
    defaults = RetentionSettings()
    observations = read_setting(config, "monitor.retention_days", defaults.observations_days)
    return RetentionSettings(
        jobs_days=_integer(raw, "jobs_days", defaults.jobs_days, 1, problems, "retention"),
        deployments_days=_integer(
            raw, "deployments_days", defaults.deployments_days, 1, problems, "retention"
        ),
        sessions_days=_integer(
            raw, "sessions_days", defaults.sessions_days, 1, problems, "retention"
        ),
        observations_days=_integer(
            {"observations_days": observations},
            "observations_days",
            defaults.observations_days,
            1,
            problems,
            "monitor",
        ),
    )
