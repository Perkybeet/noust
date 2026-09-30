# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the job-to-notifier wiring in :mod:`noust.web.server`.

The subscriber sits on the job manager's ``subscribe_all`` for the life of
the server and turns terminal job transitions into notifications - but
only for the ones that do not already announce themselves. A deploy, an
update and a rollback are recorded by
:mod:`noust.deployers.deploy_events`'s ``DeploymentRecorder`` in every
process, CLI included, and :mod:`noust.core.deploy_notifications` is a
default subscriber of that publisher - so this subscriber must stay out of
their way, or the console's own deploys would be announced twice. A backup
restore is not a deployment and is not recorded there, so it is still
reported from here; so is a failed backup. What is defended:

- **A finished restore, or a failed backup, becomes exactly one notification
  of the right kind** (a restore has its own: it is not a deploy), carrying
  the domain and, for a failure, the tool's own error verbatim in its excerpt.
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

import pytest

import noust.web.server as server_module
from noust.core.config import Config
from noust.core.notifications.model import Notification, State
from noust.core.notifier import Notifier
from noust.web.jobs import Job, JobStatus, JobType
from noust.web.server import DEPLOY_JOB_TYPES, JobNotificationSubscriber, deployment_notification
from tests.test_notifier import (  # noqa: F401  (pytest resolves fixtures by name)
    CapturingOpener,
    config,
    public_dns,
)

WEBHOOK_URL = "https://hooks.example.test/wasm"


def make_job(
    status: JobStatus,
    job_type: JobType = JobType.RESTORE,
    *,
    job_id: str = "job-1",
    domain: str | None = "example.com",
    error: str | None = None,
    backup_id: str | None = None,
) -> Job:
    """
    Build a job the way the job manager records one.

    Defaults to a restore: the one deployment-shaped job type this
    subscriber still reports on its own, deploy and update jobs having moved
    to noust.core.deploy_notifications.

    Args:
        status: Status to report.
        job_type: What kind of operation this is.
        job_id: Identifier.
        domain: Resource the job acted on, or None for a job about nothing.
        error: Failure message, verbatim from the tool.
        backup_id: The backup a restore job reads from, the way
            POST /api/backups/{id}/restore sets it in the job's metadata.

    Returns:
        The job.
    """
    metadata: dict[str, str] = {}
    if domain:
        metadata["domain"] = domain
    if backup_id:
        metadata["backup_id"] = backup_id
    return Job(
        id=job_id,
        type=job_type,
        name=f"Restore {domain}" if domain else "Restore",
        description=f"Restoring a backup to {domain}",
        status=status,
        completed_at=datetime.now(),
        error=error,
        metadata=metadata,
    )


class TestDeployJobTypes:
    """The set this module still reports on its own."""

    def test_only_restore_remains(self) -> None:
        """Deploy and update moved to noust.core.deploy_notifications."""
        assert DEPLOY_JOB_TYPES == frozenset({"restore"})


class TestDeploymentNotification:
    """The translation from a job transition to a notification, or to silence."""

    def test_a_failed_restore_is_restore_failed_with_the_domain(self) -> None:
        """A restore is not a deploy: it has its own kind, and the tool's words."""
        job = make_job(JobStatus.FAILED, error="rclone: object not found")

        event = deployment_notification(job)

        assert event is not None
        assert (event.kind, event.code, event.state) == (
            "restore_failed",
            "restore.failed",
            State.FAILED,
        )
        assert event.domain == "example.com"
        assert event.subject == "example.com"
        assert event.excerpt is not None
        assert "rclone: object not found" in event.excerpt.lines
        assert event.title == "Restore failed"

    def test_a_completed_restore_is_restore_success(self) -> None:
        """Success is announced under its own kind."""
        event = deployment_notification(make_job(JobStatus.COMPLETED))

        assert event is not None
        assert (event.kind, event.code, event.state) == (
            "restore_success",
            "restore.completed",
            State.OK,
        )
        assert event.domain == "example.com"

    def test_a_completed_restore_carries_the_backup_id_as_a_fact(self) -> None:
        """The id travels as a fact of its own, not as a sentence."""
        event = deployment_notification(
            make_job(JobStatus.COMPLETED, backup_id="backup-2026-01-01-0000")
        )

        assert event is not None
        assert {fact.key: fact.value for fact in event.facts}["backup"] == "backup-2026-01-01-0000"

    def test_a_completed_restore_without_a_backup_id_has_no_backup_fact(self) -> None:
        """A job whose metadata never carried the id must not format 'None'."""
        event = deployment_notification(make_job(JobStatus.COMPLETED))

        assert event is not None
        assert "backup" not in {fact.key for fact in event.facts}

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
        assert (event.kind, event.code) == ("backup_failed", "backup.failed")
        assert event.excerpt is not None
        assert "tar: disk full" in event.excerpt.lines

    def test_a_completed_backup_is_not_announced(self) -> None:
        """A manual backup the operator asked for needs no message; the scheduled one has its own."""
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
        assert event.subject == ""

    def test_a_job_about_nothing_still_gets_a_title(self) -> None:
        """No domain to name is a message with no subject, not a crash."""
        event = deployment_notification(make_job(JobStatus.FAILED, domain=None, error="boom"))

        assert event is not None
        assert event.title == "Restore failed"


class TestDeploymentNotificationInSpanish:
    """notifications.language: es translates Noust's words; the tool's error stays verbatim."""

    def _wired(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Args:
            config: The sandboxed configuration, mutated in place.
            monkeypatch: Patching helper, scoped to the test.
        """
        config.set("notifications.language", "es")
        # deployment_notification has no config parameter of its own - it
        # reads noust.core.notifier.fresh_config() the same way a real
        # deployment does, so the test stands in for the disk read the same
        # way tests/test_deploy_notifications.py's fake_notifier stands in
        # for delivery.
        monkeypatch.setattr(server_module, "fresh_config", lambda: config)

    def test_a_failed_restore(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wired(config, monkeypatch)
        job = make_job(JobStatus.FAILED, error="rclone: object not found")

        event = deployment_notification(job)

        assert event is not None
        assert (event.locale, event.title, event.subject) == (
            "es",
            "Restauración fallida",
            "example.com",
        )
        assert event.excerpt is not None
        assert "rclone: object not found" in event.excerpt.lines

    def test_a_completed_restore(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wired(config, monkeypatch)

        event = deployment_notification(make_job(JobStatus.COMPLETED))

        assert event is not None
        assert event.title == "Restaurado"

    def test_a_completed_restore_backup_fact_is_labelled_in_spanish(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wired(config, monkeypatch)

        event = deployment_notification(
            make_job(JobStatus.COMPLETED, backup_id="backup-2026-01-01-0000")
        )

        assert event is not None
        backup = next(fact for fact in event.facts if fact.key == "backup")
        assert (backup.label, backup.value) == ("Copia", "backup-2026-01-01-0000")

    def test_a_failed_backup(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wired(config, monkeypatch)
        job = make_job(JobStatus.FAILED, JobType.BACKUP, error="tar: disk full")

        event = deployment_notification(job)

        assert event is not None
        assert event.title == "Copia de seguridad fallida"
        assert event.excerpt is not None
        assert "tar: disk full" in event.excerpt.lines

    def test_a_job_about_nothing_still_gets_a_spanish_title(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wired(config, monkeypatch)

        event = deployment_notification(make_job(JobStatus.FAILED, domain=None, error="boom"))

        assert event is not None
        assert event.title == "Restauración fallida"


class TestSubscriber:
    """The callable registered with the job manager's subscribe_all."""

    def test_a_failed_restore_is_delivered_once(self) -> None:
        """The same terminal job notified twice must not announce twice."""
        events: list[Notification] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)
        job = make_job(JobStatus.FAILED, error="rclone exited 1")

        subscriber(job)
        subscriber(job)

        assert [event.kind for event in events] == ["restore_failed"]
        assert events[0].domain == "example.com"

    def test_success_and_failure_are_distinct_events(self) -> None:
        """Two jobs, two kinds, in order."""
        events: list[Notification] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)

        subscriber(make_job(JobStatus.COMPLETED, job_id="job-ok"))
        subscriber(make_job(JobStatus.FAILED, job_id="job-bad", error="boom"))

        assert [event.kind for event in events] == ["restore_success", "restore_failed"]

    def test_progress_updates_deliver_nothing(self) -> None:
        """Every log line notifies subscribers; none of them is an event."""
        events: list[Notification] = []
        subscriber = JobNotificationSubscriber(deliver=events.append)

        subscriber(make_job(JobStatus.RUNNING))
        subscriber(make_job(JobStatus.PENDING, job_id="job-2"))

        assert events == []

    def test_a_deploy_job_delivers_nothing(self) -> None:
        """The console's own deploy jobs must not duplicate the recorder."""
        events: list[Notification] = []
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
        """restore_success switched off stays off; restore_failed still lands."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.restore_success", False)
        opener = CapturingOpener()
        subscriber = self._wire(config, opener)

        subscriber(make_job(JobStatus.COMPLETED, job_id="job-ok"))
        subscriber(make_job(JobStatus.FAILED, job_id="job-bad", error="boom"))

        payloads = [json.loads(request.data) for request in opener.requests]
        assert [payload["event"] for payload in payloads] == ["restore_failed"]
        assert payloads[0]["domain"] == "example.com"

    def test_muting_deploys_no_longer_mutes_restores(self, config: Config) -> None:
        """A restore used to travel as deploy_success/deploy_failed and vanish with them."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_success", False)
        config.set("notifications.events.deploy_failed", False)
        opener = CapturingOpener()
        subscriber = self._wire(config, opener)

        subscriber(make_job(JobStatus.COMPLETED, job_id="job-ok"))
        subscriber(make_job(JobStatus.FAILED, job_id="job-bad", error="boom"))

        events = [json.loads(request.data)["event"] for request in opener.requests]
        assert events == ["restore_success", "restore_failed"]

    def test_the_master_switch_gates_the_wiring(self, config: Config) -> None:
        """A configured channel stays silent while notifications are off."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        subscriber = self._wire(config, opener)

        subscriber(make_job(JobStatus.FAILED, error="boom"))

        assert opener.requests == []
