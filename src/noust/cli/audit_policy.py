# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every command that changes something is on record (ENS G05, op.exp.8).

Before 3.1 only ``noust fleet`` and ``noust node`` wrote to the audit log; a
``noust delete`` at the terminal, as root, left nothing. The CLI is root and
is not subject to the console's roles (it is the emergency channel), which is
exactly why what it does must be recorded. The hook in
:class:`noust.cli.app.NoustCommand` wraps every leaf command:

1. It records ``cli.command.start`` before the command runs (the intent: a
   command killed halfway still leaves a trace) and ``cli.command`` after,
   with the outcome, the exit code and the duration, both under one
   correlation id that the host action ledger also uses, so every process
   and file the command changed is linked to it.
2. The actor is the operating system identity (:func:`~noust.core.audit.cli_actor`):
   the login uid the kernel keeps through ``sudo``, then ``SUDO_USER``.
3. Arguments are recorded sanitised (:func:`sanitize_arguments`): never a
   secret, whatever the option is called.
4. ``--reason`` (before or after the command name) is recorded with it, and
   is required under ``security.profile: ens-medium`` (op.exp.5.1, change
   reference); without it the command exits 2 before doing anything.
5. The entry points a unit, a timer or a package script runs
   (:data:`SYSTEM_ENTRY_POINTS`) are audited like any other change but need
   no reason: nobody is there to type one, and a timer 3.0 wrote cannot be
   given one. Refusing them would stop the console, the monitor and every
   scheduled backup the moment the profile is turned on.

**Read-only commands** record nothing and never need a reason. A command
declares itself read-only with ``@group.command(..., read_only=True)``;
commands that predate the flag are listed in :data:`READ_ONLY_COMMANDS` until
their modules adopt it. Anything not declared is audited: the policy fails
closed. A rehearsal (``--dry-run``) changes nothing, so it records nothing
and needs no reason either.
"""

from __future__ import annotations

import os
import time
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

import click

from noust.core import audit
from noust.core.audit.ledger import install_ledger
from noust.core.audit.sanitize import clean_details, secret_name
from noust.core.config import REDACTED
from noust.core.ens import profile
from noust.core.ens.profile import PROFILE_ENS_MEDIUM
from noust.core.exceptions import NoustError

#: The profile under which every audited command needs ``--reason``; its
#: name and its rules are :mod:`noust.core.ens.profile`'s.
ENS_PROFILE = PROFILE_ENS_MEDIUM

#: Commands, by path, that only read. Prefer ``read_only=True`` on the
#: command itself; this list is for commands declared before that existed.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "2fa status",
        "app health",
        "backup destination list",
        "backup info",
        "backup list",
        "backup remote-list",
        "backup schedule list",
        "backup storage",
        "backup verify",
        "central status",
        "cert info",
        "cert list",
        "config get",
        "config path",
        "config show",
        "cron list",
        "cron runs",
        "db backups",
        "db engines",
        "db info",
        "db list",
        "db status",
        "db user-list",
        "diagnose",
        "domain list",
        "fleet status",
        "github repos",
        "github status",
        "health",
        "list",
        "logs",
        "monitor status",
        "node key",
        "node list",
        "node show",
        "node test",
        "preview list",
        "recipe list",
        "recipe show",
        "releases list",
        "service list",
        "service logs",
        "service status",
        "sessions list",
        "setup completions",
        "setup doctor",
        "site list",
        "site show",
        "status",
        "store path",
        "store stats",
        "token list",
        "web status",
    }
)

#: Commands that run for the life of a service. They are audited like any
#: other, but bind no correlation id: the console binds one per request and
#: per job, and a process-wide one would be inherited by all of them.
DAEMON_COMMANDS: frozenset[str] = frozenset({"web start", "monitor run", "central run"})

#: What systemd units, timers, the container and the package scripts run.
#: They need no ``--reason`` under the ENS profile (see the module's point
#: 5) and are recorded with ``entry_point`` in their details; under a unit
#: the actor is ``system`` (:func:`~noust.core.audit.cli_actor`). The package
#: scripts pass ``--reason "package upgrade"`` anyway, so that what an
#: upgrade changed says so. ``tests/test_audit_entry_points.py`` reads every
#: unit template, rendered unit and package script and fails when one runs a
#: change that is not listed here.
SYSTEM_ENTRY_POINTS: frozenset[str] = DAEMON_COMMANDS | frozenset(
    {
        # Timers and oneshot units. 'db backup-run' is not here: 3.1 wrote
        # its timers with --reason in the line, and by hand it needs one.
        "backup run-schedule",
        "preview sweep",
        # Debian postinst/prerm, RPM %posttrans/%preun.
        "config clean",
        "config upgrade",
        "migrate-from-wasm",
        "monitor autoenable",
        "monitor install",
        "web stop",
    }
)


def command_path(ctx: click.Context) -> str:
    """
    The canonical path of the command a context runs, without the program.

    Args:
        ctx: The leaf command's context.

    Returns:
        Such as ``backup schedule create``; aliases resolve to the name they
        stand for.
    """
    names: list[str] = []
    current: click.Context | None = ctx
    while current is not None and current.parent is not None:
        names.append(current.command.name or current.info_name or "?")
        current = current.parent
    return " ".join(reversed(names))


def is_read_only(command: click.Command, path: str) -> bool:
    """
    Whether a command only reads.

    Args:
        command: The command.
        path: Its path, as :func:`command_path` gives it.

    Returns:
        True when it declares ``read_only=True`` or is listed in
        :data:`READ_ONLY_COMMANDS`.
    """
    return bool(getattr(command, "read_only", False)) or path in READ_ONLY_COMMANDS


def security_profile() -> str:
    """
    The configured security profile.

    Returns:
        ``standard`` or ``ens-medium``, read the way every other area reads
        it (:func:`noust.core.ens.profile.current_profile`): any spelling of
        the ENS profile, or a typo, is the ENS profile.
    """
    return profile.current_profile()


def _masked_assignment(value: str) -> str:
    name, equals, _ = value.partition("=")
    if equals and name and secret_name(name.strip()):
        return f"{name}={REDACTED}"
    return value


def _argument(value: Any) -> Any:
    if isinstance(value, str):
        return _masked_assignment(value)
    if isinstance(value, (list, tuple)):
        return [_argument(item) for item in value]
    if hasattr(value, "read") or hasattr(value, "write"):
        return f"<{getattr(value, 'name', 'stream')}>"
    return value


def sanitize_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """
    What a command was given, safe to keep in the audit log.

    An option whose name looks secret is masked whatever it holds; a
    ``KEY=VALUE`` argument with a secret-looking key has its value masked;
    ``config set <key> <value>`` masks the value of a secret key; and every
    value then goes through the audit's own scrubbing (URL passwords, sizes).
    Unset options are left out.

    Args:
        params: The command's parsed parameters.

    Returns:
        The parameters to record.
    """
    recorded: dict[str, Any] = {}
    secret_value = isinstance(params.get("key"), str) and secret_name(
        str(params["key"]).rsplit(".", 1)[-1]
    )
    for name, value in params.items():
        if value is None or value is False or value == () or value == []:
            continue
        if secret_name(name) or (name == "value" and secret_value):
            recorded[name] = REDACTED
            continue
        recorded[name] = _argument(value)
    return clean_details(recorded)


def _outcome(exc: BaseException | None) -> tuple[str, int, str | None]:
    """
    How a command ended.

    Args:
        exc: What it raised, or None.

    Returns:
        The outcome, the exit code and the error message, if any.
    """
    if exc is None:
        return "ok", 0, None
    if isinstance(exc, click.exceptions.Exit):
        return ("ok" if exc.exit_code == 0 else "failure"), exc.exit_code, None
    if isinstance(exc, SystemExit):
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return ("ok" if code == 0 else "failure"), code, None
    if isinstance(exc, (click.Abort, KeyboardInterrupt)):
        return "interrupted", 130, None
    if isinstance(exc, click.ClickException):
        return "failure", exc.exit_code, exc.format_message()
    if isinstance(exc, NoustError):
        return "failure", 1, exc.message
    return "failure", 1, f"{type(exc).__name__}: {exc}"


class AuditedInvocation(AbstractContextManager["AuditedInvocation"]):
    """Record one command's intent and outcome around its execution."""

    def __init__(self, ctx: click.Context, command: click.Command) -> None:
        """
        Args:
            ctx: The command's context.
            command: The command.
        """
        self.ctx = ctx
        self.command = command
        self.path = command_path(ctx)
        self.active = False
        self._binding: Any = None
        self._started = 0.0
        self._actor: audit.Actor | None = None
        self._reason: str | None = None
        self._entry_point = False

    def _state(self) -> Any:
        from noust.cli.app import Context

        return self.ctx.find_object(Context)

    def __enter__(self) -> AuditedInvocation:
        """
        Check the reason and record the intent.

        Returns:
            Itself.

        Raises:
            click.UsageError: The ENS profile is on and no ``--reason`` was
                given (exit 2, before the command does anything).
        """
        if is_read_only(self.command, self.path):
            return self
        state = self._state()
        if state is not None and state.dry_run_active:
            return self
        own = self.ctx.params.get("reason")
        self._reason = (own if isinstance(own, str) and own else None) or (
            state.reason if state is not None else None
        )
        entry_point = self.path in SYSTEM_ENTRY_POINTS
        if (
            not self._reason
            and not entry_point
            and profile.defaults_for(security_profile()).cli_reason_required
        ):
            raise click.UsageError(
                f"'noust {self.path}' changes this server, and the {ENS_PROFILE} security "
                'profile requires a reason for every change. Add --reason "<change reference '
                'or why>"; it is recorded in the audit log with the command.'
            )
        self.active = True
        self._actor = audit.cli_actor()
        install_ledger()
        if self.path not in DAEMON_COMMANDS:
            self._binding = audit.bind(actor=self._actor)
            self._binding.__enter__()
        details: dict[str, Any] = {
            "arguments": sanitize_arguments(dict(self.ctx.params)),
            "pid": os.getpid(),
        }
        if self._reason:
            details["reason"] = self._reason
        if entry_point:
            details["entry_point"] = True
        self._entry_point = entry_point
        audit.record(
            "cli.command.start",
            actor=self._actor,
            target=self.path,
            outcome="started",
            details=details,
        )
        self._started = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Record the outcome; never swallow what the command raised.

        Args:
            exc_type: The exception type, if the command raised.
            exc: The exception.
            traceback: Its traceback.
        """
        if not self.active:
            return None
        outcome, exit_code, error = _outcome(exc)
        details: dict[str, Any] = {
            "exit_code": exit_code,
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }
        if error:
            details["error"] = error
        if self._reason:
            details["reason"] = self._reason
        if self._entry_point:
            details["entry_point"] = True
        try:
            audit.record(
                "cli.command", actor=self._actor, target=self.path, outcome=outcome, details=details
            )
        finally:
            if self._binding is not None:
                self._binding.__exit__(exc_type, exc, traceback)
        return None


def audited(ctx: click.Context, command: click.Command) -> AuditedInvocation:
    """
    Wrap a command's execution in its audit record.

    Args:
        ctx: The command's context.
        command: The command.

    Returns:
        The context manager.
    """
    return AuditedInvocation(ctx, command)
