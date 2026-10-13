# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust updating itself, on the server it runs on.

``POST /api/system/update`` is how a central updates the Noust of its nodes
(the fleet action ``noust_update``), and how a console updates its own. It is
remote execution of an installation, so it is as narrow as it can be:

- **One command per installation method, fixed here** (:data:`COMMANDS`):
  nothing from a request reaches an argv. The method is the one the update
  check already detects (:class:`noust.core.update_checker.UpdateChecker`), so
  the command run is the command the console has always shown. A source
  checkout, an unknown installation and a container image are refused with the
  command to run instead.
- **In its own transient systemd unit.** The package's post-install script
  restarts ``noust-web``: a package manager running inside the console would be
  killed halfway through by its own upgrade. A ``systemd-run`` unit lives in its
  own cgroup and finishes whatever happens to the console (the same reason
  operating system updates run in one, :mod:`noust.managers.server.updates_unit`).
  A pip or pipx installation restarts nothing, so the console is restarted
  afterwards, by a timer unit, once the result is written.
- **A record that outlives the console** (``self-update.json`` in the state
  directory): what ran, from which version, and how it ended. A console that
  was restarted by the update finds the record ``running`` and settles it by
  asking systemd and comparing the version it now runs: an update succeeded
  exactly when the running version changed. That is what ``GET
  /api/system/update`` answers, and what a central polls.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust import __version__
from noust.core import paths
from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.update_checker import web_installed
from noust.managers.transient_unit import (
    JobVerdict,
    UnitEnding,
    UnitJobKind,
    default_verdict,
    ending_of,
    entries_from_lines,
    unit_state,
    with_output,
)
from noust.managers.upgrade_preflight import PreflightReport, UpgradePreflight

if TYPE_CHECKING:
    from noust.core.store import JobRecord

log = logging.getLogger(__name__)

#: Every transient unit of a self-update starts with this, then the run's id.
UNIT_PREFIX = "noust-self-update-"

#: The unit that restarts the console after an installation that does not.
RESTART_UNIT_PREFIX = "noust-self-restart-"

#: Seconds the installation may take; the unit is stopped a little later.
INSTALL_TIMEOUT = 1800

#: Seconds refreshing the package lists may take.
REFRESH_TIMEOUT = 600

#: Seconds between looks at the unit while following it.
FOLLOW_POLL_SECONDS = 2.0

#: Lines of the unit's journal kept in the record.
TAIL_LINES = 40

#: Seconds after the record is written that the console is restarted, for an
#: installation whose package does not restart it.
RESTART_DELAY_SECONDS = 3


@dataclass(frozen=True)
class SelfUpdateCommand:
    """
    How one installation method updates Noust.

    Attributes:
        install: The argv that installs the new version.
        refresh: The argv that refreshes the package lists first, if any. It
            restarts nothing, so it runs directly rather than in the unit.
        env: Environment for both.
        restarts_console: The package's own scripts restart ``noust-web``.
    """

    install: tuple[str, ...]
    refresh: tuple[str, ...] | None = None
    env: tuple[tuple[str, str], ...] = (("LC_ALL", "C"),)
    restarts_console: bool = True


#: The one command per installation method. Upgrading, never installing
#: something else: ``--only-upgrade`` and ``update``/``upgrade`` of ``noust``
#: alone, with dpkg's questions answered (keep a changed configuration file).
COMMANDS: dict[str, SelfUpdateCommand] = {
    "apt": SelfUpdateCommand(
        refresh=("apt-get", "update", "-q", "-o", "APT::Color=0"),
        install=(
            "apt-get",
            "-y",
            "-q",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            "Dpkg::Options::=--force-confold",
            "-o",
            "Dpkg::Use-Pty=0",
            "install",
            "--only-upgrade",
            "noust",
        ),
        env=(("LC_ALL", "C"), ("DEBIAN_FRONTEND", "noninteractive")),
    ),
    "dnf": SelfUpdateCommand(install=("dnf", "-y", "upgrade", "--refresh", "noust")),
    "yum": SelfUpdateCommand(install=("yum", "-y", "update", "noust")),
    "zypper": SelfUpdateCommand(
        refresh=("zypper", "--non-interactive", "refresh"),
        install=("zypper", "--non-interactive", "update", "noust"),
    ),
    "pip": SelfUpdateCommand(
        install=(sys.executable, "-m", "pip", "install", "--upgrade", "noust"),
        restarts_console=False,
    ),
    "pipx": SelfUpdateCommand(install=("pipx", "upgrade", "noust"), restarts_console=False),
}

#: The same methods when the console is installed: the ``web`` extra is named,
#: so what a new release adds to it is installed too. A plain ``pip install
#: --upgrade noust`` upgrades noust and nothing new it needs for the console;
#: pipx's own ``upgrade`` keeps the spec it was installed with, which for a
#: console added by ``pipx inject`` has no extra, so pip runs inside pipx's
#: environment instead.
WEB_COMMANDS: dict[str, SelfUpdateCommand] = {
    "pip": SelfUpdateCommand(
        install=(sys.executable, "-m", "pip", "install", "--upgrade", "noust[web]"),
        restarts_console=False,
    ),
    "pipx": SelfUpdateCommand(
        install=("pipx", "runpip", "noust", "install", "--upgrade", "noust[web]"),
        restarts_console=False,
    ),
}


def command_for(method: str) -> SelfUpdateCommand | None:
    """
    The one command that updates an installation.

    Args:
        method: The installation method.

    Returns:
        The command, or None for a method this module does not update.
    """
    if method in WEB_COMMANDS and web_installed():
        return WEB_COMMANDS[method]
    return COMMANDS.get(method)


#: The transitional package WASM's name left behind in each family (docs/RENAME.md): an
#: empty package that only depends on ``noust``. Left alone it stays on its old version for
#: ever, and the operating system's Updates page lists it as pending (owner item 80), so an
#: update that finds it installed upgrades it with Noust.
TRANSITIONAL_PACKAGES: dict[str, str] = {
    "apt": "wasm",
    "dnf": "wasm-cli",
    "yum": "wasm-cli",
    "zypper": "wasm-cli",
    "pip": "wasm-cli",
}

#: The package names an installation may name: the product and the transitional packages.
_PACKAGE_NAMES = frozenset({"noust", *TRANSITIONAL_PACKAGES.values()})

#: What an image-based installation runs instead (docs/CENTRAL.md).
CONTAINER_COMMAND = "docker compose pull && docker compose up -d"


class SelfUpdateRefused(Exception):
    """
    This server cannot update its own Noust from here.

    Attributes:
        code: ``unsupported_installation``, ``container_image``,
            ``already_running`` or ``unit_failed``.
        message: The sentence.
        hint: What to run instead, or what to do.
        output: A tool's own words, when one refused.
    """

    def __init__(self, code: str, message: str, hint: str, output: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.output = output


@dataclass
class SelfUpdateRecord:
    """
    One self-update, as written to ``self-update.json``.

    Attributes:
        id: The run's identifier.
        method: The installation method.
        from_version: The Noust that asked for it.
        target_version: What the package source offered, when known.
        argv: The installing command, exactly.
        unit: The transient unit it ran in; None when it ran in the process.
        status: ``running``; ``installed`` once the unit ended well and the
            console has not restarted on the new version yet; ``succeeded``
            or ``failed``.
        started_at: When it started, ISO 8601 UTC.
        finished_at: When it was settled.
        to_version: The Noust running once it was settled.
        error: Why it failed, in a sentence.
        tail: The last lines it wrote, verbatim.
        job_id: The console job that followed it.
        actor: Who asked.
    """

    id: str
    method: str
    from_version: str
    argv: list[str]
    target_version: str | None = None
    unit: str | None = None
    status: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    to_version: str | None = None
    error: str | None = None
    tail: list[str] = field(default_factory=list)
    job_id: str | None = None
    actor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Returns: The record as JSON-ready data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> SelfUpdateRecord | None:
        """
        Read a record back.

        Args:
            data: What the file held.

        Returns:
            The record, or None when it is not one this version wrote.
        """
        if not isinstance(data, dict):
            return None
        try:
            return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
        except TypeError:
            return None


def _now() -> str:
    """Returns: Now, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


def _process_started() -> float:
    """
    Returns:
        When this process started, as a Unix time: what tells a console that
        was restarted by its own update from the one that started it.
    """
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except (ImportError, OSError):
        return _IMPORTED_AT


_IMPORTED_AT = time.time()

#: What this process learnt about its own installation: how it was installed
#: and whether it is a container. Neither changes while it runs.
_DETECTED: dict[str, str | bool] = {}


class SelfUpdate:
    """
    Update this server's Noust, follow it, and tell how it ended.

    Args:
        runner: Command runner; the process-wide one when omitted.
        fs: Filesystem seam; the process-wide one when omitted.
        record_path: Where the record lives; ``self-update.json`` in the state
            directory by default.
        method: The installation method, instead of detecting it (tests).
        container: Whether this runs in a container image, instead of asking
            systemd (tests).
        systemd: Whether a transient unit can be started, instead of looking
            (tests).
        version: The running version, instead of this process's (tests).
        process_started: When this console started, as a Unix time, instead
            of asking (tests).
        sleep: Waits between looks at the unit.
        preflight: Runs the checks before the installation, given the method and the
            package names; the real ones (:mod:`noust.managers.upgrade_preflight`) when omitted.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        record_path: Path | None = None,
        method: str | None = None,
        container: bool | None = None,
        systemd: bool | None = None,
        version: str | None = None,
        process_started: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        preflight: Callable[[str, Sequence[str]], PreflightReport] | None = None,
    ) -> None:
        self._runner = runner
        self.process_started = (
            process_started if process_started is not None else _process_started()
        )
        self._fs = fs
        self.record_path = record_path or (paths.state_dir() / "self-update.json")
        self._method = method
        self._container = container
        self._systemd = systemd
        self.version = version or __version__
        self._sleep = sleep
        self._preflight = preflight or self._real_preflight

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    # ------------------------------------------------------------- facts

    def method(self) -> str:
        """
        Name how this Noust was installed.

        Returns:
            ``apt``, ``dnf``, ``yum``, ``zypper``, ``pip``, ``pipx``,
            ``source`` or ``unknown``.
        """
        if self._method is None:
            if "method" not in _DETECTED:
                from noust.core.update_checker import UpdateChecker

                # The update check's own detection, so the command run is the
                # one the console has always shown; it does not change while
                # this process runs, and it costs package manager probes.
                _DETECTED["method"] = UpdateChecker._detect_installation_method()
            self._method = str(_DETECTED["method"])
        return self._method

    def in_container(self) -> bool:
        """
        Report whether this Noust runs in a container image.

        Returns:
            True when ``systemd-detect-virt -c`` names a container.
        """
        if self._container is None:
            if "container" not in _DETECTED:
                result = self.runner.run(["systemd-detect-virt", "-c"], timeout=10)
                kind = result.stdout.strip()
                _DETECTED["container"] = result.success and kind not in ("", "none")
            self._container = bool(_DETECTED["container"])
        return self._container

    def transitional_installed(self, method: str) -> str | None:
        """
        Name the transitional package installed beside Noust, if any.

        ``apt-get install --only-upgrade`` skips a name that is not installed, but
        ``dnf upgrade`` and ``zypper update`` fail on one, so every family is asked
        first rather than given a name that may not be there.

        Args:
            method: The installation method.

        Returns:
            ``wasm`` (apt) or ``wasm-cli`` (rpm, pip) when it is installed; None
            when it is not, or for a method without one.
        """
        name = TRANSITIONAL_PACKAGES.get(method)
        if name is None:
            return None
        if method == "apt":
            result = self.runner.run(
                ["dpkg-query", "--show", "--showformat=${db:Status-Status}", name], timeout=10
            )
            return name if result.success and result.stdout.strip() == "installed" else None
        if method == "pip":
            try:
                importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                return None
            return name
        result = self.runner.run(["rpm", "--query", "--queryformat=%{NAME}", name], timeout=10)
        return name if result.success and result.stdout.strip() == name else None

    def command(self, method: str) -> SelfUpdateCommand | None:
        """
        The command that updates this installation, transitional package included.

        Args:
            method: The installation method.

        Returns:
            :func:`command_for`'s command with the installed transitional package
            appended to the names it upgrades; None for a method not updated here.
        """
        command = command_for(method)
        if command is None:
            return None
        companion = self.transitional_installed(method)
        if companion is None:
            return command
        return replace(command, install=(*command.install, companion))

    def _real_preflight(self, method: str, names: Sequence[str]) -> PreflightReport:
        """
        Run the checks before the installation against this machine.

        Args:
            method: The installation method.
            names: The package names the installation names.

        Returns:
            The report.
        """
        from noust.core.store import get_store

        try:
            store_path: Path | None = get_store().db_path
        except (NoustError, OSError) as exc:
            # No readable store is the store check's own business, not a reason to skip the rest.
            log.debug("The store could not be located for the preflight: %s", exc)
            store_path = None
        return UpgradePreflight(
            method=method,
            names=names,
            runner=self.runner,
            state_dir=paths.state_dir(),
            store_path=store_path,
        ).run()

    def systemd(self) -> bool:
        """Returns: Whether a transient unit can be started here."""
        if self._systemd is None:
            self._systemd = (
                self.runner.exists("systemd-run") and Path("/run/systemd/system").is_dir()
            )
        return self._systemd

    def refusal(self) -> SelfUpdateRefused | None:
        """
        Say why this server cannot update its own Noust, if it cannot.

        Returns:
            The refusal, or None when it can.
        """
        if self.in_container():
            return SelfUpdateRefused(
                "container_image",
                "This Noust runs from a container image; it is updated by replacing the image",
                f"On the Docker host: {CONTAINER_COMMAND}",
            )
        method = self.method()
        if method not in COMMANDS:
            from noust.core.update_checker import UpdateChecker

            return SelfUpdateRefused(
                "unsupported_installation",
                f"This Noust was installed from {method}, which it does not update by itself",
                f"Update it on the server: {UpdateChecker._get_update_command(method)}",
            )
        return None

    # ------------------------------------------------------------ record

    def read(self) -> SelfUpdateRecord | None:
        """
        Read the last self-update's record.

        Returns:
            It, or None when there never was one.
        """
        try:
            text = self.record_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            return SelfUpdateRecord.from_dict(json.loads(text))
        except ValueError:
            return None

    def write(self, record: SelfUpdateRecord) -> None:
        """
        Write the record, replacing the previous one.

        Args:
            record: The record.
        """
        self.fs.write_text(
            self.record_path, json.dumps(record.to_dict(), indent=2) + "\n", mode=SECRET_MODE
        )

    def _unit_active(self, unit: str) -> bool:
        """
        Ask systemd whether a unit is still running.

        Args:
            unit: The unit, without ``.service``.

        Returns:
            True while it is active or starting, and while systemd does not
            answer: an unanswered question is not an ended update.
        """
        state = unit_state(self.runner, unit)
        return state.active or not state.known

    def _ending(self, unit: str, tail: list[str]) -> UnitEnding:
        """
        Read how the unit ended, the one way every transient unit's ending is read.

        Args:
            unit: The unit, without ``.service``.
            tail: Its last lines, systemd's own among them.

        Returns:
            The ending (:func:`noust.managers.transient_unit.ending_of`).
        """
        return ending_of(unit, unit_state(self.runner, unit), entries_from_lines(tail))

    def _tail(self, unit: str) -> list[str]:
        """
        Read the last lines a unit wrote, systemd's own included.

        Args:
            unit: The unit, without ``.service``.

        Returns:
            Up to :data:`TAIL_LINES` lines, verbatim.
        """
        result = self.runner.run(
            [
                "journalctl",
                f"--unit={unit}.service",
                f"--lines={TAIL_LINES}",
                "--output=cat",
                "--no-pager",
            ],
            timeout=30,
        )
        return result.stdout.splitlines()

    def _restarted_since(self, started_at: str) -> bool:
        """
        Report whether this console started after a moment.

        Args:
            started_at: The moment, ISO 8601.

        Returns:
            True when this process is younger: the console was restarted since.
        """
        try:
            moment = datetime.fromisoformat(started_at).timestamp()
        except ValueError:
            return False
        return self.process_started > moment

    def settle(self, record: SelfUpdateRecord) -> SelfUpdateRecord:
        """
        Decide how an update ended, once its unit is gone.

        A unit that failed, as systemd tells it (read the one way every
        transient unit is, :mod:`noust.managers.transient_unit`), is a
        failure. Otherwise the version this process runs is the verdict: a
        console running a new version means the installation took; a console
        restarted since the update began and still on the old version means it
        did not; the console that started it, not restarted yet, can only say
        it is ``installed``, waiting for that restart.

        Args:
            record: The record.

        Returns:
            The record, settled and written when anything changed.
        """
        if record.status not in ("running", "installed"):
            return record
        if record.unit is not None and self._unit_active(record.unit):
            return record
        if record.unit is not None and not record.tail:
            record.tail = self._tail(record.unit)
        before = record.status
        failed = record.unit is not None and (
            self._ending(record.unit, record.tail).status == "failed"
        )
        if failed:
            record.status = "failed"
            record.error = record.error or f"{record.argv[0]} failed; its words are below"
        elif self.version != record.from_version:
            record.status = "succeeded"
            record.error = None
        elif self._restarted_since(record.started_at):
            record.status = "failed"
            record.error = (
                f"The console restarted and still runs Noust {self.version}: "
                "the package source offered nothing newer"
            )
        else:
            record.status = "installed"
        if record.status != before or record.status in ("succeeded", "failed"):
            if record.status in ("succeeded", "failed"):
                record.finished_at = _now()
                record.to_version = self.version
            self.write(record)
        return record

    def status(self) -> dict[str, Any]:
        """
        Describe what this server can do about its Noust, and the last update.

        Returns:
            ``current_version``, ``method``, ``supported``, ``code``,
            ``reason``, ``command`` (the argv that would run) and ``last_run``
            (the settled record, or None).
        """
        refusal = self.refusal()
        record = self.read()
        if record is not None:
            record = self.settle(record)
        command = self.command(self.method()) if refusal is None else None
        return {
            "current_version": self.version,
            "method": self.method(),
            "supported": refusal is None,
            "code": refusal.code if refusal else None,
            "reason": refusal.message if refusal else None,
            "hint": refusal.hint if refusal else None,
            "command": list(command.install) if command else None,
            "last_run": record.to_dict() if record else None,
        }

    # ------------------------------------------------------------- update

    def start(
        self,
        *,
        target_version: str | None = None,
        job_id: str | None = None,
        actor: str | None = None,
        on_line: Callable[[str], None] = lambda line: None,
    ) -> SelfUpdateRecord:
        """
        Start the update and return at once, with its record written.

        Args:
            target_version: What the package source offers, for the record.
            job_id: The console job that follows it.
            actor: Who asked.
            on_line: Receives the refresh's output.

        Returns:
            The ``running`` record.

        Raises:
            SelfUpdateRefused: This server cannot update itself, an update is
                already running, or systemd refused the unit.
        """
        refusal = self.refusal()
        if refusal is not None:
            raise refusal
        previous = self.read()
        if previous is not None and self.settle(previous).status == "running":
            raise SelfUpdateRefused(
                "already_running",
                "Noust is already being updated on this server",
                f"Follow it with: journalctl -fu {previous.unit}.service",
            )
        method = self.method()
        command = self.command(method)
        if command is None:
            raise SelfUpdateRefused(
                "unsupported_installation",
                f"Noust cannot update an installation made with {method}",
                "Update it the way it was installed.",
            )
        env = dict(command.env)
        if command.refresh is not None:
            refreshed = self.runner.stream(
                list(command.refresh), on_line=on_line, env=env, timeout=REFRESH_TIMEOUT
            )
            if not refreshed.success:
                raise SelfUpdateRefused(
                    "refresh_failed",
                    "The package lists could not be refreshed; nothing was installed",
                    "Read the output, fix the repository, and try again.",
                    output=(refreshed.stdout + refreshed.stderr).strip() or None,
                )
        names = [name for name in command.install if name in _PACKAGE_NAMES]
        report = self._preflight(method, names)
        for check in report.checks:
            if check.status != "ok":
                on_line(f"before updating ({check.status}): {check.text}")
        if not report.ready:
            first = report.blocking[0]
            raise SelfUpdateRefused(
                "preflight_failed",
                f"{first.text}; nothing was installed",
                first.fix or "Fix what it names, then update again.",
                output=first.evidence or None,
            )
        update_id = uuid.uuid4().hex[:8]
        record = SelfUpdateRecord(
            id=update_id,
            method=method,
            from_version=self.version,
            target_version=target_version,
            argv=list(command.install),
            started_at=_now(),
            job_id=job_id,
            actor=actor,
        )
        if not self.systemd():
            # No systemd, nothing restarts the console either: the process that
            # asks is safe to run it, and waits for it.
            record.unit = None
            self.write(record)
            result = self.runner.stream(
                list(command.install), on_line=on_line, env=env, timeout=INSTALL_TIMEOUT
            )
            record.tail = (result.stdout + result.stderr).splitlines()[-TAIL_LINES:]
            record.status = "succeeded" if result.success else "failed"
            record.error = None if result.success else f"{command.install[0]} failed"
            record.finished_at = _now()
            self.write(record)
            return record

        record.unit = f"{UNIT_PREFIX}{update_id}"
        self.write(record)
        argv = [
            "systemd-run",
            f"--unit={record.unit}",
            "--collect",
            "--description=Noust updating itself",
            "--property=StandardOutput=journal",
            "--property=StandardError=journal",
            f"--property=RuntimeMaxSec={INSTALL_TIMEOUT + 120}",
        ]
        argv += [f"--setenv={name}={value}" for name, value in command.env]
        if paths.DATA_DIR_ENV in os.environ:
            argv.append(f"--setenv={paths.DATA_DIR_ENV}={os.environ[paths.DATA_DIR_ENV]}")
        argv += ["--", *command.install]
        started = self.runner.run(argv, timeout=60)
        if not started.success:
            record.status = "failed"
            record.error = "systemd-run refused to start the update"
            record.tail = (started.stdout + started.stderr).splitlines()[-TAIL_LINES:]
            record.finished_at = _now()
            self.write(record)
            raise SelfUpdateRefused(
                "unit_failed",
                "The update could not be started in its own unit; nothing was installed",
                "Read systemd's words below.",
                output="\n".join(record.tail) or None,
            )
        return record

    def follow(
        self,
        record: SelfUpdateRecord,
        on_line: Callable[[str], None],
        *,
        max_seconds: float = INSTALL_TIMEOUT + 300,
    ) -> SelfUpdateRecord:
        """
        Relay the unit's output until it ends, then settle and restart if needed.

        When the package restarts the console, the process following dies
        here; the record is settled by the next console that reads it.

        Args:
            record: The ``running`` record :meth:`start` returned.
            on_line: Receives each line the unit writes.
            max_seconds: When to stop waiting for a unit that never ends.

        Returns:
            The settled record; still ``running`` when the unit outlived
            ``max_seconds``.
        """
        if record.unit is None or record.status != "running":
            return record
        seen = 0
        deadline = time.monotonic() + max_seconds
        while True:
            lines = self._tail(record.unit)
            # The tail is bounded: whatever scrolled past between two looks
            # is in the journal, and only the new end is relayed.
            fresh = lines[seen:] if len(lines) >= seen else lines
            for line in fresh:
                on_line(line)
            seen = len(lines)
            if not self._unit_active(record.unit):
                break
            if time.monotonic() > deadline:
                return record
            self._sleep(FOLLOW_POLL_SECONDS)
        record.tail = self._tail(record.unit)
        record = self.settle(record)
        method = COMMANDS.get(record.method)
        if record.status == "installed" and method is not None and not method.restarts_console:
            self._schedule_restart(record)
        return record

    def _schedule_restart(self, record: SelfUpdateRecord) -> None:
        """
        Restart the console shortly, for an installation that does not.

        A timer unit, so the job that asks can answer before its own process
        is replaced.

        Args:
            record: The record, for the unit's name.
        """
        self.runner.run(
            [
                "systemd-run",
                f"--unit={RESTART_UNIT_PREFIX}{record.id}",
                "--collect",
                f"--on-active={RESTART_DELAY_SECONDS}",
                "--description=Restart the Noust console on its new version",
                "systemctl",
                "try-restart",
                "noust-web.service",
            ],
            timeout=60,
        )


def job_kind(manager: Callable[[], SelfUpdate] | None = None) -> UnitJobKind:
    """
    Say how a console job that updated Noust is reconciled after a restart.

    The update's record decides, settled as the console that comes back
    settles it: the version this process runs is the verdict, and the unit's
    failure is a failure.

    Args:
        manager: Builds the manager; :class:`SelfUpdate` by default.

    Returns:
        The kind, for :mod:`noust.web.job_reconcile`.
    """

    def build() -> SelfUpdate:
        return manager() if manager is not None else SelfUpdate()

    def find_unit(job: JobRecord) -> str | None:
        record = build().read()
        if record is None or record.job_id != job.id:
            return None
        return record.unit

    def verdict(job: JobRecord, unit: str, ending: UnitEnding, lines: list[str]) -> JobVerdict:
        updater = build()
        record = updater.read()
        if record is None or record.job_id != job.id:
            return default_verdict(unit, ending, lines)
        if not record.tail:
            record.tail = lines[-TAIL_LINES:]
        record = updater.settle(record)
        if record.status == "failed":
            return JobVerdict(
                False, error=with_output(record.error or "The update failed", record.tail)
            )
        return JobVerdict(True, result=record.to_dict())

    return UnitJobKind(
        find_unit=find_unit,
        verdict=verdict,
        max_seconds=INSTALL_TIMEOUT + 300,
        # The package restarts the console that queued the job, so this is
        # where nearly every self-update's outcome is seen: it is audited here.
        audit_event="system.update",
        audit_target="noust",
        audit_details=lambda job, unit: {"unit": unit},
    )
