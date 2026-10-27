# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the terminal is allowed to be on this server: the ``terminal.*`` settings, read once.

Every limit a session lives under is decided here and nowhere else, so the
ticket, the session, the recording and the console's header cannot disagree:

- how long a session may last and how long it may go with nothing typed, which
  the ENS profile caps (:mod:`noust.core.ens.profile`);
- how many sessions are open at once, in all and for one person;
- how much one session's recording may take;
- whether a root shell on the server is allowed at all, which is off until the
  operator turns it on and, under the profile, stays off unless the operator
  also forces it in so many words.

A value that cannot be read falls back to its default: the terminal's limits
must never be the thing that fails closed into "no limit".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from noust.core.compliance import explain
from noust.core.ens import profile

logger = logging.getLogger(__name__)

#: The defaults, which :data:`noust.core.config.DEFAULT_CONFIG` repeats and a
#: test holds equal to it.
DEFAULT_IDLE_MINUTES = 10
DEFAULT_MAX_MINUTES = 120
DEFAULT_MAX_SESSIONS = 8
DEFAULT_MAX_SESSIONS_PER_USER = 3
DEFAULT_RECORDING_MB = 8
DEFAULT_EXEC_MAX_SECONDS = 1800
DEFAULT_RETENTION_DAYS = 90

#: The command-line fix for a server shell that is off, shown verbatim.
SERVER_ENABLE_COMMAND = "noust config set terminal.server.enabled true"
SERVER_FORCE_COMMAND = "noust config set terminal.server.force_ens true"

#: What blocks the server's shell, as the console translates it: the switch is off, the control
#: ``server_terminal`` is refused by the operator's own setting, or by a framework.
BLOCK_OFF = "server_shell_off"
BLOCK_CONTROL = "server_shell_control"
BLOCK_FRAMEWORK = "server_shell_framework"


@dataclass(frozen=True)
class ServerBlock:
    """
    What keeps the server's shell from opening, and the exact command that changes it.

    Attributes:
        code: ``server_shell_off``, ``server_shell_control`` or ``server_shell_framework``.
        what: The sentence that says what blocks it.
        how: What to change, with the command in it.
        command: The command alone, verbatim.
        control: The compliance control that blocks it, when one does (``server_terminal``).
        forced_by: The frameworks that hold that control, when any do.
    """

    code: str
    what: str
    how: str
    command: str
    control: str | None = None
    forced_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class TerminalSettings:
    """
    The terminal's limits and switches, with the profile already applied.

    Attributes:
        enabled: Whether application terminals may be opened.
        idle_seconds: A session with nothing typed for this long is ended.
        max_seconds: A session lasts at most this long.
        max_sessions: Sessions open at once on this server.
        max_sessions_per_user: Sessions one person may have open at once.
        recording_max_bytes: The most one session's recording may take.
        server_enabled: ``terminal.server.enabled``.
        server_forced: ``terminal.server.force_ens``.
        server_forced_by: The frameworks that hold the control ``server_terminal`` refused.
        server_allow_command: The command that sets that control to allowed, or turns off the
            frameworks that hold it.
        reason_suffix: What a command that changes something ends with on a server that asks for a
            reason for every change, so that what is printed runs as printed.
        exec_max_seconds: The longest a one-off command may run.
        retention_days: How long recordings are kept.
        ens: Whether the ENS profile is in force.
    """

    enabled: bool = True
    idle_seconds: int = DEFAULT_IDLE_MINUTES * 60
    max_seconds: int = DEFAULT_MAX_MINUTES * 60
    max_sessions: int = DEFAULT_MAX_SESSIONS
    max_sessions_per_user: int = DEFAULT_MAX_SESSIONS_PER_USER
    recording_max_bytes: int = DEFAULT_RECORDING_MB * 1024 * 1024
    server_enabled: bool = False
    server_forced: bool = False
    server_forced_by: tuple[str, ...] = ()
    server_allow_command: str = "noust compliance set server_terminal allowed"
    reason_suffix: str = ""
    exec_max_seconds: int = DEFAULT_EXEC_MAX_SECONDS
    retention_days: int = DEFAULT_RETENTION_DAYS
    ens: bool = False

    @property
    def server_allowed(self) -> bool:
        """
        Whether a root shell on the server may be opened right now.

        Returns:
            True when it is switched on, and, where the compliance controls
            keep the shell off, forced.
        """
        return self.server_enabled and (not self.ens or self.server_forced)

    def server_refusal(self) -> tuple[str, str] | None:
        """
        Say why the server's shell cannot be opened, and how to change that.

        Returns:
            ``(what, how to fix it)``, or None when it may be opened.
        """
        block = self.server_block()
        return None if block is None else (block.what, block.how)

    def server_block(self) -> ServerBlock | None:
        """
        Say what keeps the server's shell from opening, and the exact command that changes it.

        The switch comes first: a shell that is off is off whatever the controls say. Then the
        control ``server_terminal``, named for what it is: the operator's own setting, or the
        frameworks that hold it. It is never put down to the ENS profile unless that is what is on.

        Returns:
            The block, or None when it may be opened.
        """
        enable = SERVER_ENABLE_COMMAND + self.reason_suffix
        force = SERVER_FORCE_COMMAND + self.reason_suffix
        if not self.server_enabled:
            return ServerBlock(
                BLOCK_OFF,
                "The terminal on the server itself is off (terminal.server.enabled)",
                f"It is a root shell, so it stays off until an administrator turns it on: {enable}",
                enable,
            )
        if not self.ens or self.server_forced:
            return None
        if self.server_forced_by:
            names = ", ".join(self.server_forced_by)
            many = len(self.server_forced_by) > 1
            return ServerBlock(
                BLOCK_FRAMEWORK,
                f"{names} {'keep' if many else 'keeps'} the terminal on the server itself off "
                "(the control server_terminal is refused)",
                "A root shell in the browser is the console at its most root-equivalent. "
                f"To allow it anyway, which is put on record: {force}. "
                f"Or turn the framework off: {self.server_allow_command}",
                force,
                "server_terminal",
                self.server_forced_by,
            )
        return ServerBlock(
            BLOCK_CONTROL,
            "The control server_terminal is set to refused on this server, so the terminal on "
            "the server itself stays off",
            "No framework asks for it: it was set in Settings > Controls or with the command "
            f"line. To allow it: {self.server_allow_command}",
            self.server_allow_command,
            "server_terminal",
        )


def _integer(raw: Any, default: int, low: int, high: int) -> int:
    """
    Read a whole number inside a range.

    Args:
        raw: What the configuration holds.
        default: What to use when it is not a number in the range.
        low: Smallest accepted.
        high: Largest accepted.

    Returns:
        The number, or the default.
    """
    if isinstance(raw, bool):
        return default
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _flag(raw: Any, default: bool) -> bool:
    """
    Read a yes/no setting.

    Args:
        raw: What the configuration holds.
        default: What to use when it is neither.

    Returns:
        The flag.
    """
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    return default


def load_settings(config: Any | None = None) -> TerminalSettings:
    """
    Read ``terminal.*`` and apply the security profile.

    Args:
        config: A :class:`~noust.core.config.Config`, or a stand-in with
            ``get``; the process-wide one by default.

    Returns:
        The settings. Under the ENS profile the times are capped at its values
        and the retention is at least the audit trail's.
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    get = getattr(config, "get", None)

    def read(key: str, default: Any) -> Any:
        return get(key, default) if callable(get) else default

    defaults = profile.active_defaults(config)
    idle = _integer(
        read("terminal.idle_minutes", DEFAULT_IDLE_MINUTES), DEFAULT_IDLE_MINUTES, 1, 120
    )
    longest = _integer(
        read("terminal.max_minutes", DEFAULT_MAX_MINUTES), DEFAULT_MAX_MINUTES, 1, 720
    )
    retention = _integer(
        read("retention.terminal_days", DEFAULT_RETENTION_DAYS), DEFAULT_RETENTION_DAYS, 1, 3650
    )
    if defaults.audit_retention_days > profile.STANDARD.audit_retention_days:
        # A recording is evidence: kept as long as the audit trail is, where a
        # framework has the trail kept for longer than the default.
        retention = max(retention, defaults.audit_retention_days)
    idle = min(idle, defaults.terminal_idle_minutes)
    longest = min(longest, defaults.terminal_max_minutes)
    why = explain.why_on("server_terminal", config)
    return TerminalSettings(
        enabled=_flag(read("terminal.enabled", True), True),
        idle_seconds=idle * 60,
        max_seconds=longest * 60,
        max_sessions=_integer(
            read("terminal.max_sessions", DEFAULT_MAX_SESSIONS), DEFAULT_MAX_SESSIONS, 1, 64
        ),
        max_sessions_per_user=_integer(
            read("terminal.max_sessions_per_user", DEFAULT_MAX_SESSIONS_PER_USER),
            DEFAULT_MAX_SESSIONS_PER_USER,
            1,
            16,
        ),
        recording_max_bytes=_integer(
            read("terminal.recording.max_mb", DEFAULT_RECORDING_MB), DEFAULT_RECORDING_MB, 1, 1024
        )
        * 1024
        * 1024,
        server_enabled=_flag(read("terminal.server.enabled", False), False),
        server_forced=_flag(read("terminal.server.force_ens", False), False),
        server_forced_by=why.forced_by,
        server_allow_command=why.command,
        reason_suffix=explain.reason_suffix(config),
        exec_max_seconds=_integer(
            read("terminal.exec.max_seconds", DEFAULT_EXEC_MAX_SECONDS),
            DEFAULT_EXEC_MAX_SECONDS,
            1,
            86400,
        ),
        retention_days=retention,
        ens=not profile.server_terminal_allowed(config),
    )
