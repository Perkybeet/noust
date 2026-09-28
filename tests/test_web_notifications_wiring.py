# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the job-to-notifier wiring in :mod:`wasm.web.server`.

The subscriber sits on the job manager's ``subscribe_all`` for the life of
the server and turns terminal job transitions into notification events - but
only for the ones that do not already announce themselves. A deploy, an
update and a rollback are recorded by
:mod:`wasm.deployers.deploy_events`'s ``DeploymentRecorder`` in every
process, CLI included, and :mod:`wasm.core.deploy_notifications` is a
default subscriber of that publisher - so this subscriber must stay out of
their way, or the console's own deploys would be announced twice. A backup
restore is not a deployment and is not recorded there, so it is still
reported from here; so is a failed backup. What is defended:

- **A finished restore, or a failed backup, becomes exactly one event of the
  right kind**, carrying the domain and, for a failure, the tool's own error
  verbatim.
- **A deploy or an update job never does**, whatever its outcome: the
  deployment recorder already announced it.
- **Nothing else does either.** Progress updates, cancellations and job types
  with their own reporting surface must not reach anyone's phone.
- **The operator's per-kind switches hold.** The subscriber publishes through
  the notifier, so a kind switched off in ``notifications.events`` sends
  nothing, and that is asserted through the real notifier with an injected
  opener rather than through a mock of the filter.
"""

# The notifier's config fixture is imported rather than replicated, so there
# stays one definition of "a sandboxed configuration". Ruff reads a test
# parameter named after an imported fixture as a redefinition; here it is the
# mechanism.
# ruff: noqa: F811

from __future__ import annotations

import json
from datetime import datetime

from tests.test_notifier import (  # noqa: F401  (pytest resolves fixtures by name)
    CapturingOpener,
    config,
    public_dns,
)
from wasm.core.config import Config
from wasm.core.notifier import NotificationEvent, Notifier
from wasm.web.jobs import Job, JobStatus, JobType
from wasm.web.server import DEPLOY_JOB_TYPES, JobNotificationSubscriber, deployment_notification

WEBHOOK_URL = "https://hooks.example.test/wasm"


def make_job(
    status: JobStatus,
    job_type: JobType = JobType.RESTORE,
    *,
    job_id: str = "job-1",
    domain: str | None = "example.com",
    error: str | None = None,
) -> Job:
    """
    Build a job the way the job manager records one.

    Defaults to a restore: the one deployment-shaped job type this
    subscriber still reports on its own, deploy and update jobs having moved
    to wasm.core.deploy_notifications.

    Args:
        status: Status to report.
        job_type: What kind of operation this is.
        job_id: Identifier.
        domain: Resource the job acted on, or None for a job about nothing.
        error: Failure message, verbatim from the tool.

    Returns:
        The job.
    """
    return Job(
        id=job_id,
        type=job_type,
        name=f"Restore {domain}" if domain else "Restore",
        description=f"Restoring a backup to {domain}",
        status=status,
        completed_at=datetime.now(),
        error=error,
        metadata={"domain": domain} if domain else {},
    )


class TestDeployJobTypes:
    """The set this module still reports on its own."""

    def test_only_restore_remains(self) -> None:
        """Deploy and update moved to wasm.core.deploy_notifications."""
        assert DEPLOY_JOB_TYPES == frozenset({"restore"})


class TestDeploymentNotification:
    """The translation from a job transition to an event, or to silence."""

    def test_a_failed_restore_becomes_deploy_failed_with_the_domain(self) -> None:
        """The event carries the domain and the tool's own words."""
        job = make_job(JobStatus.FAILED, error="rclone: object not found")

        event = deployment_notification(job)

        assert event is not None
        assert event.kind == "deploy_failed"
        assert event.domain == "example.com"
        assert "rclone: object not found" in event.body
        assert "failed" in event.title

    def test_a_completed_restore_becomes_deploy_success(self) -> None:
        """Success is announced under its own kind."""
        event = deployment_notification(make_job(JobStatus.COMPLETED))

        assert event is not None
        assert event.kind == "deploy_success"
        assert event.domain == "example.com"

    def test_a_deploy_or_an_update_job_is_never_announced_here(self) -> None:
        """The deployment recorder already announced it; this would be twice."""
        for job_type in (JobType.DEPLOY, JobType.UPDATE):
            failed = deployment_notification(make_job(JobStatus.FAILED, job_type, error="boom"))
            completed = deployment_notification(make_job(JobStatus.COMPLETED, job_type))
            assert failed is None, job_type
            assert completed is None, job_type

    def test_a_failed_backup_has_a_kind_of_its_own(self) -> None:
        """backup_failed exists precisely for this transition."""
        event = deployment_notification(
            make_job(JobStatus.FAILED, JobType.BACKUP, error="tar: disk full")
        )

        assert event is not None
        assert event.kind == "backup_failed"

    def test_a_completed_backup_is_not_announced(self) -> None:
        """There is no backup_success kind, and none is invented."""
        assert deployment_notification(make_job(JobStatus.COMPLETED, JobType.BACKUP)) is None

    def test_non_terminal_and_cancelled_transitions_are_silent(self) -> None:
        """A rail colour, not an interruption."""
        for status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLED):
            assert deployment_notification(make_job(status)) is None, status

    def test_job_types_with_their_own_surface_are_silent(self) -> None:
        """Certificate, service, deploy and update jobs report elsewhere."""
        for job_type in (
            JobType.DEPLOY,
            JobType.UPDATE,
            JobType.CERT_CREATE,
            JobType.CERT_RENEW,
            JobType.SERVICE_ACTION,
            JobType.SITE_ACTION,
            JobType.DELETE,
            JobType.CUSTOM,
        ):
            assert deployment_notification(make_job(JobStatus.FAILED, job_type)) is None, job_type

    def test_a_job_about_nothing_carries_no_domain(self) -> None:
        """A missing domain must not become the string 'None'."""
        event = deployment_notification(make_job(JobStatus.FAILED, domain=None, error="boom"))

        assert event is not None
        assert event.domain is None


class TestSubscriber:
    """The callable registered with the job manager's subscribe_all."""

    def test_a_failed_restore_is_delivered_once(self) -> None:
        """The same terminal job notified twice must not announce twice."""
        events: list[NotificationEvent] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)
        job = make_job(JobStatus.FAILED, error="rclone exited 1")

        subscriber(job)
        subscriber(job)

        assert [event.kind for event in events] == ["deploy_failed"]
        assert events[0].domain == "example.com"

    def test_success_and_failure_are_distinct_events(self) -> None:
        """Two jobs, two kinds, in order."""
        events: list[NotificationEvent] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)

        subscriber(make_job(JobStatus.COMPLETED, job_id="job-ok"))
        subscriber(make_job(JobStatus.FAILED, job_id="job-bad", error="boom"))

        assert [event.kind for event in events] == ["deploy_success", "deploy_failed"]

    def test_progress_updates_deliver_nothing(self) -> None:
        """Every log line notifies subscribers; none of them is an event."""
        events: list[NotificationEvent] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)

        subscriber(make_job(JobStatus.RUNNING))
        subscriber(make_job(JobStatus.PENDING, job_id="job-2"))

        assert events == []

    def test_a_deploy_job_delivers_nothing(self) -> None:
        """The console's own deploy jobs must not duplicate the recorder."""
        events: list[NotificationEvent] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)

        subscriber(make_job(JobStatus.COMPLETED, JobType.DEPLOY, job_id="job-deploy"))
        subscriber(make_job(JobStatus.FAILED, JobType.UPDATE, job_id="job-update", error="boom"))

        assert events == []


class TestOperatorSwitchesHold:
    """The subscriber publishes through the notifier, filters included."""

    def _wire(self, config: Config, opener: CapturingOpener) -> JobNotificationSubscriber:
        """
        Args:
            config: The sandboxed configuration.
            opener: The stand-in for urlopen.

        Returns:
            A subscriber delivering through a real notifier.
        """
        notifier = Notifier(config, opener=opener)
        return JobNotificationSubscriber(deliver=notifier.notify)

    def test_a_disabled_kind_sends_nothing(self, config: Config) -> None:
        """deploy_success switched off stays off; deploy_failed still lands."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_success", False)
        opener = CapturingOpener()
        subscriber = self._wire(config, opener)

        subscriber(make_job(JobStatus.COMPLETED, job_id="job-ok"))
        subscriber(make_job(JobStatus.FAILED, job_id="job-bad", error="boom"))

        payloads = [json.loads(request.data) for request in opener.requests]
        assert [payload["event"] for payload in payloads] == ["deploy_failed"]
        assert payloads[0]["domain"] == "example.com"

    def test_the_master_switch_gates_the_wiring(self, config: Config) -> None:
        """A configured channel stays silent while notifications are off."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        subscriber = self._wire(config, opener)

        subscriber(make_job(JobStatus.FAILED, error="boom"))

        assert opener.requests == []
