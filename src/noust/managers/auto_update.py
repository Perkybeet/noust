# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust installing its own new releases when nobody asks, inside limits the operator set.

``Update Noust`` is a decision: a person reads the notes, picks the moment, and watches. Some
servers are nobody's daily concern, and a vulnerability fixed in a release does nothing for a
server that never installs it. This is the opt-in way for that (checklist N-28), and it is
deliberately the same update with a narrower door:

- **The same update.** It calls :class:`~noust.managers.self_update.SelfUpdate`, so it runs the
  one command of the installation's method, in a transient unit that outlives the console's
  restart, behind the same preflight (room on disk, a store the new version opens, a
  package manager that answers). Nothing here installs anything on its own.
- **Inside a window, on chosen days, for a channel** (:mod:`noust.core.update_policy`): a
  timer looks every ten minutes while the window is open, and every run judges the clock again
  itself, so a timer started by hand at noon changes nothing.
- **Never while work is in flight.** The console restarts when the package does; a build in
  progress would end with it. A run that finds a job working waits for the next look.
- **Never a new major version**, and a new minor only on the ``stable`` channel.
- **One decision a day.** Once a window has answered (updated, nothing to do, refused with a
  reason a person must read), it is not asked again until the next window; only "busy" and "the
  package is still being built" are tried again within it.
- **Off when the update check is off.** ``updates.check false`` means this server asks nobody
  for release information, which is also what decides what to install.

The outcome of every run is written to ``auto-update.json`` and to the audit trail
(``system.auto_update``); a failure is not announced anywhere else, so Settings > About shows
the last run and the command line prints it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from noust.core import audit, paths, update_policy
from noust.core.audit.actor import Actor
from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import get_store
from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress, VersionCheck
from noust.core.update_policy import Window
from noust.managers.backup_scheduler import BackupScheduler, validate_calendar
from noust.managers.self_update import SelfUpdate, SelfUpdateRefused

log = logging.getLogger(__name__)

#: What the timer's description says, which is how Noust knows the timer is its own.
TIMER_DESCRIPTION = "Noust automatic update timer"

#: Who the audit trail and the update's record name as having asked.
ACTOR = "auto-update"

#: Why a run did not update, as codes the console and the command line say in words.
REASON_DISABLED = "disabled"
REASON_CLOSED = "outside_window"
REASON_DONE = "done_today"
REASON_BUSY = "busy"
REASON_CHECK_OFF = "check_off"
REASON_UP_TO_DATE = "up_to_date"
REASON_ON_THE_WAY = "on_the_way"
REASON_CHANNEL = "outside_channel"
REASON_CHECKING = "checking"

#: What a refusal of the update itself is retried on at the next look; the rest need a person.
RETRY_REFUSALS = frozenset({"already_running", "refresh_failed", "unit_failed"})

#: Job statuses that mean the console is working on something an update would interrupt.
BUSY_STATUSES = ("pending", "running")


def _now() -> datetime:
    """Returns: The server's own clock, which is what a window is written in, with its offset."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class AutoUpdateSettings:
    """
    What the operator set.

    Attributes:
        enabled: The timer exists and runs.
        channel: Which releases are taken (:data:`~noust.core.update_policy.CHANNELS`).
        window: The hours an update may start in.
        days: The days the window opens on.
    """

    enabled: bool = False
    channel: str = update_policy.DEFAULT_CHANNEL
    window: Window = field(
        default_factory=lambda: update_policy.parse_window(update_policy.DEFAULT_WINDOW)
    )
    days: tuple[str, ...] = update_policy.WEEKDAYS

    def to_dict(self) -> dict[str, Any]:
        """Returns: The settings as the API and the configuration file write them."""
        return {
            "enabled": self.enabled,
            "channel": self.channel,
            "window": str(self.window),
            "days": update_policy.days_text(self.days),
        }

    @classmethod
    def from_config(cls, config: Config | None = None) -> AutoUpdateSettings:
        """
        Read the settings.

        A value the file holds that the rules refuse (a hand edit) is not guessed at: the
        automatic update is then off, because it never runs half of a policy.

        Args:
            config: The configuration; the process-wide one when omitted.

        Returns:
            The settings.
        """
        config = config or Config()
        try:
            enabled = config.get("updates.auto.enabled", False)
            return cls(
                enabled=enabled if isinstance(enabled, bool) else False,
                channel=update_policy.parse_channel(
                    config.get("updates.auto.channel", update_policy.DEFAULT_CHANNEL)
                ),
                window=update_policy.parse_window(
                    config.get("updates.auto.window", update_policy.DEFAULT_WINDOW)
                ),
                days=update_policy.parse_days(
                    config.get("updates.auto.days", update_policy.DEFAULT_DAYS)
                ),
            )
        except ValueError as exc:
            log.warning("The automatic update's settings are not valid, so it is off: %s", exc)
            return cls()


@dataclass
class AutoUpdateRun:
    """
    What one run decided.

    Attributes:
        outcome: ``updated``, ``failed`` or ``skipped``.
        reason: A code (``up_to_date``, ``outside_channel``, ``preflight_failed``...).
        detail: The sentence that says it.
        at: When, ISO 8601 on the server's clock.
        trigger: ``schedule`` (the timer) or ``manual``.
        from_version: The version that was running.
        target: What the package source offered, when it was read.
        to_version: The version running once the update was settled.
        settled: Whether the window has now answered, so later looks leave it alone.
        output: A tool's own words, when one refused or failed.
    """

    outcome: str
    reason: str
    detail: str
    at: str
    trigger: str
    from_version: str
    target: str | None = None
    to_version: str | None = None
    settled: bool = False
    output: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Returns: The run as JSON-ready data."""
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    """
    What the update check says to do under the settings.

    Attributes:
        action: ``update``, ``none`` (nothing to take) or ``wait`` (try again at the next look).
        reason: The code.
        detail: The sentence.
        target: The version it would install.
    """

    action: str
    reason: str
    detail: str
    target: str | None = None


def decide(check: VersionCheck, settings: AutoUpdateSettings) -> Decision:
    """
    Judge a check against the channel.

    Args:
        check: What the update check found.
        settings: The operator's settings.

    Returns:
        Whether to update, and why not when not.
    """
    state = check.state
    if state == "up_to_date":
        return Decision(
            "none",
            REASON_UP_TO_DATE,
            f"Noust {check.current} is the newest this server can install",
        )
    target = check.announced_version
    if state == "on_the_way":
        return Decision(
            "wait",
            REASON_ON_THE_WAY,
            f"{target} is published but this server's package source has not built it yet",
            target,
        )
    if target is None:
        return Decision(
            "wait", REASON_ON_THE_WAY, "The update check could not read what is offered", None
        )
    # A release is worth taking for the vulnerability it fixes when it includes that fix.
    covers = check.security_update and not update_policy_newer(check.security, target)
    allowed, why = update_policy.allows(settings.channel, check.current, target, security=covers)
    if not allowed:
        return Decision("none", REASON_CHANNEL, why, target)
    return Decision("update", "update", why, target)


def update_policy_newer(candidate: str | None, than: str) -> bool:
    """
    Returns:
        Whether ``candidate`` is a version above ``than`` (False when either is unreadable).
    """
    if candidate is None:
        return False
    one, other = update_policy.version_parts(candidate), update_policy.version_parts(than)
    return one is not None and other is not None and one > other


def next_window(settings: AutoUpdateSettings, now: datetime) -> datetime | None:
    """
    Say when the window next opens.

    Args:
        settings: The operator's settings.
        now: A time on the server's clock.

    Returns:
        When it opens next (now, rounded to the minute, when it is open); None when no day is chosen.
    """
    if update_policy.window_day(settings.window, settings.days, now) is not None:
        return now.replace(second=0, microsecond=0)
    for offset in range(0, 9):
        day = (now + timedelta(days=offset)).date()
        if update_policy.WEEKDAYS[day.weekday()] not in settings.days:
            continue
        opens = datetime.combine(day, settings.window.start, tzinfo=now.tzinfo)
        if opens > now:
            return opens
    return None


class AutoUpdate:
    """
    The automatic update: its settings, its timer, and the run the timer starts.

    Args:
        runner: Command runner; the process-wide one when omitted.
        fs: Filesystem seam; the process-wide one when omitted.
        config: The configuration; the process-wide one when omitted.
        scheduler: Writes the timer; one over the runner and the filesystem when omitted.
        self_update: Builds the manager that installs; the real one when omitted.
        check: Reads the update check; :meth:`UpdateChecker.check` when omitted.
        check_enabled: Whether ``updates.check`` is on; :meth:`UpdateChecker.enabled` when omitted.
        busy: Whether the console is working on a job; reads the store's jobs when omitted.
        clock: The server's own clock.
        state_path: Where the last run is kept; ``auto-update.json`` in the state directory.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        config: Config | None = None,
        scheduler: BackupScheduler | None = None,
        self_update: Callable[[], SelfUpdate] | None = None,
        check: Callable[[], VersionCheck] | None = None,
        check_enabled: Callable[[], bool] | None = None,
        busy: Callable[[], bool] | None = None,
        clock: Callable[[], datetime] = _now,
        state_path: Path | None = None,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._config = config
        self._scheduler = scheduler
        self._self_update = self_update
        self._check = check or UpdateChecker.check
        self._check_enabled = check_enabled or UpdateChecker.enabled
        self._busy = busy or self._jobs_are_working
        self._clock = clock
        self.state_path = state_path or (paths.state_dir() / "auto-update.json")

    # ------------------------------------------------------------- parts

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    @property
    def config(self) -> Config:
        """The configuration in force right now, read once: a change made in two steps is one write."""
        if self._config is None:
            self._config = Config()
        return self._config

    @property
    def scheduler(self) -> BackupScheduler:
        """The scheduler that owns the timer unit."""
        if self._scheduler is None:
            self._scheduler = BackupScheduler(runner=self._runner, fs=self._fs)
        return self._scheduler

    def updater(self) -> SelfUpdate:
        """Returns: The manager that installs."""
        return (
            self._self_update()
            if self._self_update
            else SelfUpdate(runner=self._runner, fs=self._fs)
        )

    @staticmethod
    def _jobs_are_working() -> bool:
        """Returns: Whether the console has a job queued or running."""
        store = get_store()
        return any(store.list_jobs(limit=1, status=status) for status in BUSY_STATUSES)

    # ---------------------------------------------------------- settings

    def settings(self) -> AutoUpdateSettings:
        """Returns: The settings in force."""
        return AutoUpdateSettings.from_config(self.config)

    def set_settings(
        self,
        *,
        enabled: bool | None = None,
        channel: str | None = None,
        window: str | None = None,
        days: str | None = None,
    ) -> AutoUpdateSettings:
        """
        Change the settings, and make the timer match them.

        Every value is judged by the rules the configuration file enforces; nothing is written
        when one is refused, and the timer is changed before the configuration so a timer that
        cannot be written leaves the old settings in force.

        Args:
            enabled: Turn the automatic update on or off.
            channel: Which releases are taken.
            window: ``HH:MM-HH:MM``.
            days: ``daily`` or weekdays.

        Returns:
            The settings now in force.

        Raises:
            ValidationError: A value is not one the rules accept.
            NoustError: This installation cannot update itself, or the timer cannot be written.
        """
        current = self.settings()
        changes = {"enabled": enabled, "channel": channel, "window": window, "days": days}
        wanted = {key: value for key, value in changes.items() if value is not None}
        try:
            candidate = AutoUpdateSettings(
                enabled=wanted.get("enabled", current.enabled),  # type: ignore[arg-type]
                channel=update_policy.parse_channel(wanted.get("channel", current.channel)),
                window=update_policy.parse_window(wanted.get("window", str(current.window))),
                days=update_policy.parse_days(
                    wanted.get("days", update_policy.days_text(current.days))
                ),
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        if candidate.enabled:
            refusal = self.updater().refusal()
            if refusal is not None:
                raise NoustError(refusal.message, details=refusal.hint)
            self._install_timer(candidate)
        else:
            self.scheduler.remove_unit_pair(paths.AUTO_UPDATE_UNIT, TIMER_DESCRIPTION)
        for key, value in candidate.to_dict().items():
            self.config.set(f"updates.auto.{key}", value)
        if not self.config.save():
            raise NoustError(
                "The settings could not be saved",
                details="The configuration file is not writable; the timer was changed to match it, so run the same command again once it is.",
            )
        return candidate

    def _install_timer(self, settings: AutoUpdateSettings) -> None:
        """
        Write and enable the timer that looks while the window is open.

        Args:
            settings: The settings it follows.
        """
        lines = [
            validate_calendar(line)
            for line in update_policy.calendars(settings.window, settings.days)
        ]
        self.scheduler.install_unit_pair(
            paths.AUTO_UPDATE_UNIT,
            TIMER_DESCRIPTION,
            "the automatic update",
            "auto-update-timer.j2",
            "auto-update-service.j2",
            lines[0],
            more="\n".join(lines[1:]),
            window=str(settings.window),
        )

    # ------------------------------------------------------------- state

    def last_run(self) -> tuple[AutoUpdateRun | None, date | None]:
        """
        Returns:
            The last run, and the day of the window it settled (None when it did not settle one).
        """
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
            run = AutoUpdateRun(**document["last"])
            settled = (
                date.fromisoformat(document["settled_day"]) if document.get("settled_day") else None
            )
        except (OSError, ValueError, KeyError, TypeError):
            # No record, or one this version did not write: the same as none.
            return None, None
        return run, settled

    def _remember(self, run: AutoUpdateRun, day: date | None) -> None:
        """
        Keep the last run, and the day it settled.

        Args:
            run: What the run decided.
            day: The window's day when the run settled it.
        """
        previous = self.last_run()[1]
        settled = day if run.settled and day is not None else previous
        document = {"last": run.to_dict(), "settled_day": settled.isoformat() if settled else None}
        self.fs.write_text(self.state_path, json.dumps(document, indent=2) + "\n")

    def status(self) -> dict[str, Any]:
        """
        Say what is set, whether it can work here, and what the last run did.

        Reads nothing from the network.

        Returns:
            ``settings``, ``supported`` (with ``reason`` and ``hint`` when not), ``check_enabled``,
            ``window_open``, ``next_window``, ``timer`` and ``last_run``.
        """
        settings = self.settings()
        now = self._clock()
        refusal = self.updater().refusal()
        last, _ = self.last_run()
        upcoming = next_window(settings, now) if settings.enabled else None
        return {
            "settings": settings.to_dict(),
            "supported": refusal is None,
            "reason": refusal.message if refusal else None,
            "hint": refusal.hint if refusal else None,
            "check_enabled": self._check_enabled(),
            "window_open": update_policy.window_day(settings.window, settings.days, now)
            is not None,
            "next_window": upcoming.isoformat() if upcoming else None,
            "timer": self.scheduler.unit_state(paths.AUTO_UPDATE_UNIT),
            "last_run": last.to_dict() if last else None,
        }

    # --------------------------------------------------------------- run

    def run(
        self,
        *,
        trigger: str = "schedule",
        force: bool = False,
        on_line: Callable[[str], None] = lambda line: None,
    ) -> AutoUpdateRun:
        """
        Decide, and update when the settings say so.

        Args:
            trigger: ``schedule`` from the timer, ``manual`` from an operator.
            force: Skip the window, the day and the one-decision-a-day rule: an operator who
                runs it now means it. The channel, a busy console and everything the update
                itself checks still apply.
            on_line: Receives what the update prints.

        Returns:
            What was decided, written to the record (and to the audit trail when it updated or failed).
        """
        settings = self.settings()
        now = self._clock()
        version = self.updater().version
        day = update_policy.window_day(settings.window, settings.days, now)

        def skipped(
            reason: str, detail: str, *, settled: bool = False, target: str | None = None
        ) -> AutoUpdateRun:
            run = AutoUpdateRun(
                outcome="skipped",
                reason=reason,
                detail=detail,
                at=now.isoformat(timespec="seconds"),
                trigger=trigger,
                from_version=version,
                target=target,
                settled=settled,
            )
            if reason not in (REASON_DISABLED, REASON_CLOSED, REASON_DONE):
                # Looks that decided nothing do not overwrite what the window's real answer was.
                self._remember(run, day)
            return run

        if not force:
            if not settings.enabled:
                return skipped(REASON_DISABLED, "The automatic update is off")
            if day is None:
                return skipped(REASON_CLOSED, f"The window ({settings.window}) is closed")
            if self.last_run()[1] == day:
                return skipped(REASON_DONE, "This window has already been answered")
        if not self._check_enabled():
            return skipped(
                REASON_CHECK_OFF,
                "The update check is off (updates.check), so this server does not know what to install",
                settled=True,
            )
        if self._busy():
            return skipped(
                REASON_BUSY, "The console is working on a job; the next look will try again"
            )
        try:
            check = self._check()
        except UpdateCheckInProgress:
            return skipped(
                REASON_CHECKING, "Another update check is reading; the next look will try again"
            )

        decision = decide(check, settings)
        if decision.action == "wait":
            return skipped(decision.reason, decision.detail, target=decision.target)
        if decision.action == "none":
            return skipped(decision.reason, decision.detail, settled=True, target=decision.target)
        return self._update(decision, version, trigger, now, day, on_line)

    def _update(
        self,
        decision: Decision,
        version: str,
        trigger: str,
        now: datetime,
        day: date | None,
        on_line: Callable[[str], None],
    ) -> AutoUpdateRun:
        """
        Install the release, follow it, and record how it ended.

        Args:
            decision: The decision to update.
            version: The version that is running.
            trigger: What started the run.
            now: When.
            day: The window's day.
            on_line: Receives what the update prints.

        Returns:
            The run.
        """
        updater = self.updater()
        run = AutoUpdateRun(
            outcome="updated",
            reason="updated",
            detail=decision.detail,
            at=now.isoformat(timespec="seconds"),
            trigger=trigger,
            from_version=version,
            target=decision.target,
            settled=True,
        )
        try:
            record = updater.start(target_version=decision.target, actor=ACTOR, on_line=on_line)
            record = updater.follow(record, on_line)
        except SelfUpdateRefused as exc:
            run.outcome = "failed" if exc.code not in RETRY_REFUSALS else "skipped"
            run.reason = exc.code
            run.detail = f"{exc.message}. {exc.hint}"
            run.output = exc.output
            run.settled = exc.code not in RETRY_REFUSALS
        else:
            run.to_version = record.to_version
            if record.status == "failed":
                run.outcome, run.reason = "failed", "install_failed"
                run.detail = record.error or "The installation failed"
                run.output = "\n".join(record.tail) or None
        self._remember(run, day)
        if run.outcome != "skipped":
            audit.record(
                "system.auto_update",
                actor=Actor(
                    kind="system", name=ACTOR, via="systemd" if trigger == "schedule" else trigger
                ),
                target="noust",
                outcome="ok" if run.outcome == "updated" else "failure",
                details={
                    "from_version": run.from_version,
                    "target": run.target,
                    "to_version": run.to_version,
                    "reason": run.reason,
                    "trigger": trigger,
                    "channel": self.settings().channel,
                },
            )
        return run
