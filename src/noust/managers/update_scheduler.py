# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating an application by itself, on a schedule (checklist B-23).

``noust deploy schedule set shop.example.com --at 'Sun *-*-* 03:00'`` writes a systemd
timer, ``noust-update-<app>.timer``, that runs ``noust deploy schedule run`` at that time.
Each run is a careful update, because nobody is there to look at it:

- **It is refused when its backup does not happen** (``backup_first``, on by default): an
  application that has a database is not updated if the copy that would let a migration be
  undone could not be taken, and the failure is told like a failed update.
- **It does nothing when there is nothing new** (``only_if_new``): the branch (or the newest
  tag a followed pattern matches) is compared with what runs, so a timer is not a nightly
  rebuild of the same commit.
- **It is held by a pause or a freeze of deploys** (:mod:`noust.core.deploy_guard`) like a
  push is: an application that was rolled back is not updated by a timer that fires an hour
  later, and nothing is deployed in the middle of an incident.
- **It passes the health gate** every update passes, which puts back what served when the
  new version does not answer.

The timer is the backup scheduler's own mechanism (:class:`BackupScheduler.install_unit_pair`):
one place writes the unit files, validates the calendar expression, and reads systemd back.
The row (:mod:`noust.core.update_schedule_store`) is what the timer is for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.central import require_server_role
from noust.core import deploy_guard, update_schedule_store
from noust.core.exceptions import (
    BackupError,
    DeploysFrozenError,
    DeploysPausedError,
    NoustError,
    ValidationError,
)
from noust.core.logger import Logger
from noust.core.store import DeploymentTrigger, get_store
from noust.core.tags import newest_tag
from noust.core.update_schedule_store import UpdateSchedule
from noust.core.utils import domain_to_app_name
from noust.deployers import deploy_events
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.managers.backup_scheduler import BackupScheduler, validate_calendar
from noust.managers.source_manager import SourceManager
from noust.validators.domain import validate_domain
from noust.validators.names import validate_service_name
from noust.validators.source import validate_source

_log = logging.getLogger(__name__)

#: The description every update timer carries, which says it is Noust's.
DESCRIPTION_PREFIX = "Noust update timer for "

#: What the schedule's ``calendar`` may be given as, besides a systemd expression.
ALIASES = {
    "hourly": "*-*-* *:00:00",
    "daily": "*-*-* 03:00:00",
    "weekly": "Sun *-*-* 03:00:00",
    "monthly": "*-*-01 03:00:00",
}


def timer_name(domain: str) -> str:
    """
    Args:
        domain: The application's domain.

    Returns:
        The unit name (without suffix) of its update timer.
    """
    from noust.core import paths

    return f"{paths.UPDATE_UNIT_PREFIX}{domain_to_app_name(domain)}"


@dataclass(frozen=True)
class RunOutcome:
    """
    How a scheduled run ended.

    Attributes:
        domain: The application.
        outcome: One of :data:`noust.core.update_schedule_store.OUTCOMES`.
        detail: One sentence.
        deployment_id: The deployment it made, when it made one.
    """

    domain: str
    outcome: str
    detail: str
    deployment_id: int | None = None


def expand_calendar(value: str) -> str:
    """
    Read what an operator typed as when to update.

    Args:
        value: ``daily``, ``weekly``, ``monthly`` (at 03:00; Sunday for a week) or a systemd
            calendar expression.

    Returns:
        The expression, checked.

    Raises:
        ValidationError: Empty, too long or with a character a unit must not hold.
    """
    try:
        return validate_calendar(ALIASES.get(value.strip(), value.strip()))
    except BackupError as exc:
        raise ValidationError(
            exc.message,
            details=exc.details or "Write a systemd calendar expression.",
            field="calendar",
        ) from exc


class UpdateScheduler:
    """
    Writes an application's update timer and keeps its schedule.

    Args:
        scheduler: The backup scheduler, whose unit-writing machinery this reuses; a default
            one when None.
    """

    def __init__(self, scheduler: BackupScheduler | None = None) -> None:
        self._units = scheduler or BackupScheduler()

    def set(
        self,
        domain: str,
        calendar: str,
        *,
        enabled: bool = True,
        backup_first: bool = True,
        only_if_new: bool = True,
        by: str | None = None,
    ) -> UpdateSchedule:
        """
        Make, or change, the schedule on which an application updates itself.

        Args:
            domain: The application's domain.
            calendar: ``daily``, ``weekly``, ``monthly`` or a systemd calendar expression.
            enabled: Whether the timer is on; off keeps the schedule and stops the timer.
            backup_first: Refuse the update when its backup does not happen.
            only_if_new: Do nothing when there is nothing new.
            by: Who set it.

        Returns:
            The schedule.

        Raises:
            ValidationError: The expression is not acceptable, or the application is a
                preview (a throwaway copy updates with its pull request).
            NoustError: The application is unknown, or the timer cannot be written.
        """
        require_server_role("Applications")
        domain = validate_domain(domain)
        app = get_store().get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        if app.preview_parent:
            raise ValidationError(
                f"{domain} is a preview",
                details="A preview updates with its pull request; schedule the application.",
            )
        expression = expand_calendar(calendar)
        unit = timer_name(domain)
        try:
            validate_service_name(unit)
        except NoustError as exc:
            raise ValidationError(
                f"Cannot schedule updates for {domain}", details=str(exc)
            ) from exc
        saved = update_schedule_store.save(
            domain,
            expression,
            enabled=enabled,
            backup_first=backup_first,
            only_if_new=only_if_new,
            by=by,
        )
        if enabled:
            self._units.install_unit_pair(
                unit,
                DESCRIPTION_PREFIX,
                "this application's updates",
                "update-timer.j2",
                "update-service.j2",
                expression,
                domain=domain,
            )
        else:
            self._units.remove_unit_pair(unit, DESCRIPTION_PREFIX)
        return saved

    def remove(self, domain: str) -> bool:
        """
        Stop updating an application by itself: the timer and the schedule.

        Args:
            domain: The application's domain.

        Returns:
            True when there was a schedule or a timer.
        """
        domain = validate_domain(domain)
        had_timer = self._units.remove_unit_pair(timer_name(domain), DESCRIPTION_PREFIX)
        had_row = update_schedule_store.delete(domain)
        return had_timer or had_row

    def state(self, domain: str) -> dict[str, Any]:
        """
        Ask systemd about an application's update timer.

        Args:
            domain: The application's domain.

        Returns:
            ``installed``, ``next_run`` and ``last_run``.
        """
        return self._units.unit_state(timer_name(domain))


def _something_new(domain: str) -> tuple[bool, str]:
    """
    Say whether a run would deploy anything.

    Args:
        domain: The application.

    Returns:
        Whether there is something new, and a sentence saying what was compared. When the
        remote cannot be asked the answer is yes: the update will say why it cannot.
    """
    from noust.deployers import lifecycle
    from noust.deployers.container import pulls_image
    from noust.managers import image_updates

    app = get_store().get_app(domain)
    if app is None:
        return False, "the application is gone"
    if pulls_image(app):
        # An image from a registry follows its tag: new only when the tag points at another
        # digest than the one that runs (checklist A-14).
        try:
            looked = image_updates.check_image(domain)
        except NoustError as exc:
            return True, f"the registry could not be asked ({exc.message})"
        return looked.changed, looked.reason
    if app.follow_tags:
        if not app.source or validate_source(app.source)[0] != "git":
            return True, "it follows tags but is not deployed from git"
        try:
            newest = newest_tag(
                SourceManager(verbose=False).remote_tags(app.source), app.follow_tags
            )
        except NoustError as exc:
            return True, f"the remote could not be asked ({exc.message})"
        deployed = lifecycle.deployed_tag(app)
        if newest is None:
            return False, f"the remote has no tag matching {app.follow_tags}"
        if newest == deployed:
            return False, f"{newest} is already deployed"
        return True, f"{newest} is newer than {deployed or 'what runs'}"
    state = lifecycle.check_upstream(domain)
    if state is None:
        return True, "the remote could not be compared"
    return state.has_new_commits, state.summary


def run_scheduled(domain: str, *, logger: Logger | None = None) -> RunOutcome:
    """
    Run the update an application's schedule asks for, once.

    What ``noust deploy schedule run`` does, and what the timer's service runs. It never
    raises for how the update went: every way it can end is recorded in the schedule and
    the audit trail, and a failure is announced like any failed update, so a timer firing
    at three in the morning leaves an answer.

    Args:
        domain: The application's domain.
        logger: Where the update reports; a default one when None.

    Returns:
        How it ended.

    Raises:
        NoustError: The application has no schedule, or it is off.
    """
    from noust.deployers.lifecycle import update_app

    require_server_role("Applications")
    log = logger or Logger()
    domain = validate_domain(domain)
    schedule = update_schedule_store.get(domain)
    if schedule is None or not schedule.enabled:
        raise NoustError(
            f"{domain} has no scheduled update that is on",
            details=f"Set one with: noust deploy schedule set {domain} --at 'Sun *-*-* 03:00'",
        )

    def finish(outcome: str, detail: str, deployment_id: int | None = None) -> RunOutcome:
        update_schedule_store.record_run(domain, outcome, detail, deployment_id=deployment_id)
        log.info(f"Scheduled update of {domain}: {outcome}: {detail}")
        return RunOutcome(domain, outcome, detail, deployment_id)

    gate = deploy_guard.gate_for(domain, automatic=True)
    if gate is not None:
        return finish(update_schedule_store.HELD, gate.message)
    if schedule.only_if_new:
        new, why = _something_new(domain)
        if not new:
            return finish(update_schedule_store.NOTHING_NEW, why)
    try:
        updated = update_app(
            domain,
            trigger=DeploymentTrigger.CLI.value,
            automatic=True,
            backup_required=schedule.backup_first,
            logger=log,
        )
    except (DeploysFrozenError, DeploysPausedError) as exc:
        return finish(update_schedule_store.HELD, exc.message)
    except BackupError as exc:
        _announce_refusal(domain, exc)
        return finish(update_schedule_store.BACKUP_FAILED, exc.message)
    except NoustError as exc:
        # The recorder already announced a failure it recorded; a refusal before it did not.
        return finish(update_schedule_store.FAILED, exc.message)
    return finish(
        update_schedule_store.UPDATED,
        f"updated; {', '.join(updated.restarted) or 'nothing to restart'}",
        updated.deployment_id,
    )


def _announce_refusal(domain: str, error: NoustError) -> None:
    """
    Tell the notification channels an update was refused before it was recorded.

    Args:
        domain: The application.
        error: Why.
    """
    deploy_events.publish(
        DeployEvent(
            kind=DeployEventKind.FAILED,
            domain=domain,
            trigger="schedule",
            operation="update",
            error=f"{error.message}. {error.details or ''}".strip(),
            error_message=error.message,
            ts=datetime.now(timezone.utc),
        )
    )


__all__ = [
    "ALIASES",
    "DESCRIPTION_PREFIX",
    "RunOutcome",
    "UpdateScheduler",
    "expand_calendar",
    "run_scheduled",
    "timer_name",
]
