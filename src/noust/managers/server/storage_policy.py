# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Scheduled cleaning of a server's disk, and the history of every cleanup that ran.

:class:`~noust.managers.server.storage.StorageManager` cleans from a closed list of
actions, one at a time, when asked. This is the same list run by a timer, with the
two things a timer needs that a person at a terminal does not:

- **A threshold.** The policy runs only when the fullest writable filesystem is at
  least that full (80% by default); below it the run is recorded as skipped, with
  the percentage it found, and nothing is touched. ``0`` runs it every time.
- **A history.** Every cleanup, scheduled or by hand, leaves one row: when, what ran
  and why, the filesystem and how full it was before and after, what each action
  freed or removed, and what failed. A cleanup that deleted something an operator
  missed (Coolify's #11582 took other people's images and the whole build cache) can
  then be reconstructed, not guessed at.

What a policy may run is narrower than the list: ``docker-image`` needs a target and
is never scheduled; the two Docker actions take something the operator may want back,
so they run only when the policy says it accepts that (``allow_docker``), as a yes
given once for the policy. Docker volumes, which hold the databases, are not an action
at all, and no action removes an image Noust did not create.

The timer is ``noust-storage-clean.timer``, written like the other scheduled units of
Noust (:class:`~noust.managers.backup_scheduler.BackupScheduler` writes it) and run
as ``noust server cleanup-schedule run --scheduled``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core import paths
from noust.core.exceptions import NoustError, ValidationError
from noust.core.notifications.composers_watch import compose_cleanup
from noust.core.store import NoustStore, get_store
from noust.managers.server.errors import ServerError
from noust.managers.server.storage import CLEANUP_ACTIONS, StorageManager

#: The actions a policy may run: the closed list without the one that needs a target.
SCHEDULABLE = tuple(action for action in CLEANUP_ACTIONS if action != "docker-image")

#: The actions that take something the operator may want back.
DOCKER_ACTIONS = frozenset({"docker-build-cache", "docker-dangling-images"})

#: What a new policy cleans: the three that give space back and take nothing wanted.
DEFAULT_ACTIONS = ("journal", "pkg-cache", "releases")

#: A run starts when the fullest writable filesystem is this full.
DEFAULT_THRESHOLD = 80

#: How often the timer fires unless the policy says another schedule.
DEFAULT_SCHEDULE = "daily"

#: Runs kept in the history; the oldest go when a new one is written.
RUNS_KEPT = 200

#: What a run's ``trigger`` can be: the timer, an operator's single action, or an
#: operator running the policy now.
TRIGGERS = ("schedule", "manual", "policy")

#: What a run's ``status`` can be.
STATUSES = ("ok", "partial", "failed", "skipped")

#: The unit pair that runs the policy.
TIMER_DESCRIPTION = "Noust storage cleanup timer"


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CleanupPolicy:
    """
    What a server cleans by itself, and when.

    Attributes:
        enabled: The timer exists and runs the policy.
        schedule: An alias of the backup scheduler's (``daily``, ``weekly``) or a
            systemd calendar expression.
        threshold_percent: Run only when the fullest writable filesystem is at least
            this full; 0 always runs.
        actions: The actions to run, in order, from :data:`SCHEDULABLE`.
        journal_mb: Vacuum the journal to this size (the journal action's default when None).
        allow_docker: Also run the Docker actions among ``actions``: the operator
            accepts, once, that they delete build cache and unnamed images.
    """

    enabled: bool = False
    schedule: str = DEFAULT_SCHEDULE
    threshold_percent: int = DEFAULT_THRESHOLD
    actions: tuple[str, ...] = DEFAULT_ACTIONS
    journal_mb: int | None = None
    allow_docker: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The policy, JSON-ready.
        """
        return {
            "enabled": self.enabled,
            "schedule": self.schedule,
            "threshold_percent": self.threshold_percent,
            "actions": list(self.actions),
            "journal_mb": self.journal_mb,
            "allow_docker": self.allow_docker,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CleanupPolicy:
        """
        Read a policy, checking every part of it.

        Args:
            data: Its fields; missing ones take the defaults.

        Returns:
            The policy.

        Raises:
            ValidationError: When a field is out of range, names an action that
                cannot be scheduled, or asks for a Docker action without accepting it.
        """
        from noust.managers.backup_scheduler import validate_calendar

        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValidationError(f"Unknown field(s): {', '.join(unknown)}", field="policy")
        schedule = str(data.get("schedule", DEFAULT_SCHEDULE))
        try:
            validate_calendar(schedule)
        except NoustError as exc:
            raise ValidationError(exc.message, details=exc.details, field="schedule") from exc
        threshold = data.get("threshold_percent", DEFAULT_THRESHOLD)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, int)
            or not 0 <= threshold <= 99
        ):
            raise ValidationError(
                "The threshold is a percentage from 0 to 99",
                details="0 runs the policy every time; 80 runs it when the fullest disk is 80% full.",
                field="threshold_percent",
            )
        actions = data.get("actions", DEFAULT_ACTIONS)
        if (
            not isinstance(actions, (list, tuple))
            or not actions
            or not all(isinstance(action, str) for action in actions)
        ):
            raise ValidationError("Choose at least one action", field="actions")
        bad = [action for action in actions if action not in SCHEDULABLE]
        if bad:
            raise ValidationError(
                f"These cannot be scheduled: {', '.join(bad)}",
                details=f"A policy runs some of: {', '.join(SCHEDULABLE)}. Removing one Docker "
                "image needs a target and is done by hand.",
                field="actions",
            )
        if len(set(actions)) != len(actions):
            raise ValidationError("An action is listed twice", field="actions")
        allow_docker = bool(data.get("allow_docker", False))
        docker = sorted(DOCKER_ACTIONS & set(actions))
        if docker and not allow_docker:
            raise ValidationError(
                f"{', '.join(docker)} delete something you may want back",
                details="Accept that once for the policy (allow_docker): the build cache older "
                "than a week and the images that have no name and no container.",
                field="allow_docker",
            )
        journal = data.get("journal_mb")
        if journal is not None and (
            isinstance(journal, bool) or not isinstance(journal, int) or not 16 <= journal <= 65536
        ):
            raise ValidationError("The journal size is between 16 and 65536 MB", field="journal_mb")
        return cls(
            enabled=bool(data.get("enabled", False)),
            schedule=schedule,
            threshold_percent=threshold,
            actions=tuple(actions),
            journal_mb=journal,
            allow_docker=allow_docker,
        )


@dataclass
class ActionOutcome:
    """
    What one action of a run did.

    Attributes:
        action: The action.
        status: ``ok`` or ``failed``.
        freed_bytes: What it freed, when the tool said.
        removed: What it removed, when it can name it.
        error: Why it failed, in the tool's words.
    """

    action: str
    status: str = "ok"
    freed_bytes: int | None = None
    removed: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome, JSON-ready.
        """
        return {
            "action": self.action,
            "status": self.status,
            "freed_bytes": self.freed_bytes,
            "removed": list(self.removed),
            "error": self.error,
        }


@dataclass
class CleanupRun:
    """
    One execution of cleaning, as the history keeps it.

    Attributes:
        id: Its number; 0 until it is written.
        trigger: One of :data:`TRIGGERS`.
        status: One of :data:`STATUSES`.
        started_at: When it began, ISO 8601 UTC.
        ended_at: When it ended.
        mount: The fullest writable filesystem's mount point, when read.
        percent_before: How full it was before.
        percent_after: How full it was after.
        freed_bytes: The sum of what the actions freed.
        actions: What each action did.
        reason: Why nothing ran (the threshold), or what went wrong in general.
        actor: Who asked; empty for the timer.
    """

    trigger: str
    status: str = "ok"
    id: int = 0
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    mount: str | None = None
    percent_before: float | None = None
    percent_after: float | None = None
    freed_bytes: int | None = None
    actions: list[ActionOutcome] = field(default_factory=list)
    reason: str | None = None
    actor: str | None = None

    def finish(self) -> None:
        """Settle the status and the total from the actions, and stamp the end."""
        self.ended_at = _now()
        failed = [a for a in self.actions if a.status != "ok"]
        if self.reason and not self.actions:
            self.status = self.status if self.status in ("skipped", "failed") else "skipped"
        elif failed and len(failed) == len(self.actions):
            self.status = "failed"
        elif failed:
            self.status = "partial"
        else:
            self.status = "ok"
        sizes = [a.freed_bytes for a in self.actions if a.freed_bytes is not None]
        self.freed_bytes = sum(sizes) if sizes else None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The run, JSON-ready.
        """
        return {
            "id": self.id,
            "trigger": self.trigger,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "mount": self.mount,
            "percent_before": self.percent_before,
            "percent_after": self.percent_after,
            "freed_bytes": self.freed_bytes,
            "actions": [action.to_dict() for action in self.actions],
            "reason": self.reason,
            "actor": self.actor,
        }


class CleanupHistory:
    """
    Read and write the record of every cleanup.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def record(self, run: CleanupRun) -> CleanupRun:
        """
        Write one run, and drop the oldest beyond :data:`RUNS_KEPT`.

        Args:
            run: The run, finished.

        Returns:
            The run, with its number.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO storage_cleanup_runs (trigger, status, started_at, ended_at, mount, "
                "percent_before, percent_after, freed_bytes, actions, reason, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.trigger,
                    run.status,
                    run.started_at,
                    run.ended_at,
                    run.mount,
                    run.percent_before,
                    run.percent_after,
                    run.freed_bytes,
                    json.dumps([action.to_dict() for action in run.actions]),
                    run.reason,
                    run.actor,
                ),
            )
            run.id = int(cursor.lastrowid or 0)
            cursor.execute(
                "DELETE FROM storage_cleanup_runs WHERE id NOT IN "
                "(SELECT id FROM storage_cleanup_runs ORDER BY id DESC LIMIT ?)",
                (RUNS_KEPT,),
            )
        return run

    def list(self, limit: int = 50) -> list[CleanupRun]:
        """
        Read the newest runs.

        Args:
            limit: How many at most.

        Returns:
            The runs, the newest first.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM storage_cleanup_runs ORDER BY id DESC LIMIT ?", (limit,))
            .fetchall()
        )
        return [_run(row) for row in rows]


def _run(row: Any) -> CleanupRun:
    """
    Build a run out of its row.

    Args:
        row: A ``storage_cleanup_runs`` row.

    Returns:
        The run.
    """
    try:
        raw = json.loads(row["actions"])
    except (TypeError, ValueError):
        raw = []
    actions = [
        ActionOutcome(
            action=str(item.get("action")),
            status=str(item.get("status", "ok")),
            freed_bytes=item.get("freed_bytes"),
            removed=[str(r) for r in item.get("removed") or []],
            error=item.get("error"),
        )
        for item in raw
        if isinstance(item, dict)
    ]
    return CleanupRun(
        id=int(row["id"]),
        trigger=str(row["trigger"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        ended_at=row["ended_at"],
        mount=row["mount"],
        percent_before=row["percent_before"],
        percent_after=row["percent_after"],
        freed_bytes=row["freed_bytes"],
        actions=actions,
        reason=row["reason"],
        actor=row["actor"],
    )


class CleanupSchedule:
    """
    The policy, its timer, and running it.

    Args:
        storage: The manager that cleans.
        store: The store the policy and the history live in.
        scheduler: Writes the timer; a :class:`~noust.managers.backup_scheduler.BackupScheduler`
            by default.
    """

    def __init__(
        self,
        storage: StorageManager,
        *,
        store: NoustStore | None = None,
        scheduler: Any = None,
    ) -> None:
        self.storage = storage
        self._store = store
        self._scheduler = scheduler
        self.history = CleanupHistory(store)

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def scheduler(self) -> Any:
        """Whatever writes the timer."""
        if self._scheduler is None:
            from noust.managers.backup_scheduler import BackupScheduler

            self._scheduler = BackupScheduler(runner=self.storage._runner, fs=self.storage._fs)
        return self._scheduler

    # -- The policy ---------------------------------------------------------

    def policy(self) -> CleanupPolicy:
        """
        Returns:
            The policy in force: the default (disabled) when none was ever set.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT document FROM storage_cleanup_policy WHERE id = 1")
            .fetchone()
        )
        if row is None:
            return CleanupPolicy()
        try:
            return CleanupPolicy.from_dict(json.loads(row["document"]))
        except (ValueError, ValidationError):
            # A document this version cannot read is no policy: it never runs half of one.
            return CleanupPolicy()

    def set_policy(self, policy: CleanupPolicy, *, actor: str | None = None) -> CleanupPolicy:
        """
        Keep a policy, and make the timer match it.

        Args:
            policy: The policy, already checked (:meth:`CleanupPolicy.from_dict`).
            actor: Who sets it.

        Returns:
            The policy.

        Raises:
            BackupError: When the timer cannot be written or enabled; the policy is
                then not kept.
        """
        if policy.enabled:
            self._install_timer(policy)
        else:
            self.scheduler.remove_unit_pair(paths.STORAGE_CLEAN_UNIT, TIMER_DESCRIPTION)
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO storage_cleanup_policy (id, document, updated_at, updated_by) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET document = excluded.document, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (json.dumps(policy.to_dict()), _now(), actor),
            )
        return policy

    def _install_timer(self, policy: CleanupPolicy) -> None:
        """
        Write and enable the timer for a policy.

        Args:
            policy: The policy.
        """
        from noust.managers.backup_scheduler import validate_calendar

        self.scheduler.install_unit_pair(
            paths.STORAGE_CLEAN_UNIT,
            TIMER_DESCRIPTION,
            "the storage cleanup",
            "storage-clean-timer.j2",
            "storage-clean-service.j2",
            validate_calendar(policy.schedule),
        )

    def timer_state(self) -> dict[str, Any]:
        """
        Returns:
            ``installed``, ``next_run`` and ``last_run`` of the timer.
        """
        state: dict[str, Any] = self.scheduler.unit_state(paths.STORAGE_CLEAN_UNIT)
        return state

    # -- Running -------------------------------------------------------------

    def run(
        self,
        *,
        trigger: str = "policy",
        force: bool = False,
        actor: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CleanupRun:
        """
        Run the policy, if the disk is as full as it asks.

        Args:
            trigger: ``schedule`` from the timer, ``policy`` from an operator.
            force: Run whatever the disk's state; an operator who asks to run it now
                means it.
            actor: Who asked; the timer has none.
            on_line: Receives what the tools print.

        Returns:
            The run, written to the history: ``skipped`` with the percentage found
            when the disk is below the threshold.

        Raises:
            ValidationError: When the trigger is unknown.
        """
        if trigger not in TRIGGERS:
            raise ValidationError(f"Unknown trigger: {trigger!r}", field="trigger")
        policy = self.policy()
        run = CleanupRun(trigger=trigger, actor=actor)
        worst = self._worst()
        if worst is not None:
            run.mount, run.percent_before = worst
        if trigger == "schedule" and not policy.enabled:
            # A timer that outlived its policy (the policy was switched off by hand
            # while the unit was running) does nothing and says so.
            run.reason = "the cleanup policy is switched off"
            run.finish()
            return self.history.record(run)
        if (
            not force
            and policy.threshold_percent > 0
            and run.percent_before is not None
            and run.percent_before < policy.threshold_percent
        ):
            run.reason = (
                f"{run.mount} is {run.percent_before:g}% full, below the {policy.threshold_percent}% "
                "the policy waits for"
            )
            run.finish()
            return self.history.record(run)
        for action in policy.actions:
            run.actions.append(self._act(policy, action, on_line))
        after = self._worst()
        if after is not None:
            run.percent_after = after[1]
        run.finish()
        recorded = self.history.record(run)
        if trigger == "schedule":
            self._announce(recorded)
        return recorded

    @staticmethod
    def _announce(run: CleanupRun) -> None:
        """
        Tell the notification channels how a cleanup the timer ran ended.

        Only a run that did something is told (a skipped one found the disk
        below the threshold), and only the timer's: an operator who ran the
        policy by hand is looking at the answer.

        Args:
            run: The run, written to the history.
        """
        if run.status not in ("ok", "partial", "failed"):
            return
        # Deferred import: the notifier's own modules read the configuration
        # this package is configured through.
        from noust.core.notifier import notify_composed

        failures = [
            (action.action, action.error or "") for action in run.actions if action.status != "ok"
        ]
        notify_composed(
            lambda ctx: compose_cleanup(
                run.status,
                ctx,
                mount=run.mount,
                percent_before=run.percent_before,
                percent_after=run.percent_after,
                freed_bytes=run.freed_bytes,
                actions=[action.action for action in run.actions],
                failures=failures,
            )
        )

    def _act(
        self, policy: CleanupPolicy, action: str, on_line: Callable[[str], None] | None
    ) -> ActionOutcome:
        """
        Run one action of the policy, keeping its failure as a result.

        Args:
            policy: The policy.
            action: The action.
            on_line: Receives what the tools print.

        Returns:
            What it did, or why it could not.
        """
        outcome = ActionOutcome(action=action)
        params: dict[str, Any] = (
            {"size_mb": policy.journal_mb} if action == "journal" and policy.journal_mb else {}
        )
        try:
            result = self.storage.cleanup(
                action,
                confirm=action in DOCKER_ACTIONS and policy.allow_docker,
                on_line=on_line,
                record=False,
                **params,
            )
        except (ServerError, NoustError) as exc:
            outcome.status = "failed"
            outcome.error = (exc.output or exc.details or exc.message).strip()[:2000]
            return outcome
        outcome.freed_bytes = result.freed_bytes
        outcome.removed = list(result.removed)
        return outcome

    def _worst(self) -> tuple[str, float] | None:
        """
        Read the fullest writable filesystem.

        Returns:
            Its mount point and how full it is; None when the disks cannot be read.
        """
        return self.storage.worst_percent()
