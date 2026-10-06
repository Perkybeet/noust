# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The queue and the cancellation, through the API and the webhooks.

A push that arrives while an application builds is queued (and several pushes
are one update), not refused with "busy"; a full queue answers 429; a job is
cancelled from the console's endpoint whether it waits or builds; and every
refusal says why. The jobs are real, on the real job manager, with the
update's function replaced by a gate the test opens.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import cancellation, delivery, webhook_deliveries
from noust.core.store import App, NoustStore
from noust.web import jobs as jobs_module
from noust.web.api import hooks as hooks_module
from noust.web.jobs import JobContext, JobManager, JobStatus, JobType
from tests.test_delivery_queue import Work, queue_work, wait_until
from tests.test_web_hooks import (
    DOMAIN,
    PUSH_MAIN,
    admin,
    app,
    client,
    fresh_delivery_cache,
    github_headers,
    secret,
    seeded,
    store,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (admin, app, client, fresh_delivery_cache, secret, seeded, store)


@pytest.fixture
def seeded_on_releases(store: NoustStore, seeded: App) -> App:
    """
    The seeded application, built as releases: the layout a build can be stopped on.

    Args:
        store: The store.
        seeded: The seeded application.

    Returns:
        The application.
    """
    row = store.get_app(DOMAIN)
    assert row is not None
    row.layout = "releases"
    store.update_app(row)
    return row


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> list[delivery.DeliverySettings]:
    """
    The server's limits, as a one-element list a test replaces.

    Returns:
        The settings handed out.
    """
    current = [delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=1)]
    monkeypatch.setattr(delivery, "current", lambda **_kwargs: current[0])
    return current


@pytest.fixture
def manager(store: NoustStore, limits: list[delivery.DeliverySettings]) -> Iterator[JobManager]:
    """
    The process-wide job manager, fresh for the test, looking at its queue often.

    Yields:
        The manager.
    """
    JobManager.reset_instance()
    mp = pytest.MonkeyPatch()
    mp.setattr(jobs_module, "DISPATCH_POLL_SECONDS", 0.05)
    mp.setattr(jobs_module, "LOCK_RETRY_SECONDS", 0.05)
    mp.setattr(jobs_module, "EXTERNAL_SYNC_SECONDS", 0.05)
    mp.setattr(cancellation, "POLL_INTERVAL", 0.05)
    instance = JobManager()
    try:
        yield instance
    finally:
        JobManager.reset_instance()
        mp.undo()


@pytest.fixture
def updates(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Replace what a webhook's update job runs with something that records it.

    Returns:
        One entry per update that ran.
    """
    ran: list[dict[str, Any]] = []

    def update(
        domain: str, job_context: JobContext | None = None, tag: str | None = None
    ) -> dict[str, Any]:
        ran.append({"domain": domain, "tag": tag})
        return {"domain": domain, "status": "updated"}

    monkeypatch.setattr(hooks_module, "webhook_update_job", update)
    return ran


def push(client: TestClient, secret: str) -> Any:
    """
    Deliver a signed push to the seeded application's webhook.

    Args:
        client: The anonymous client.
        secret: The webhook secret.

    Returns:
        The response.
    """
    return client.post(
        f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=github_headers(secret, PUSH_MAIN)
    )


def deliveries(store: NoustStore) -> list[webhook_deliveries.WebhookDelivery]:
    """
    Args:
        store: The store.

    Returns:
        The seeded application's delivery log, oldest first.
    """
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return sorted(webhook_deliveries.list_deliveries(app.id, store=store), key=lambda d: d.id)


# ---------------------------------------------------------------------------
# Webhooks: a push during a deploy waits instead of failing
# ---------------------------------------------------------------------------


def test_a_push_during_a_deploy_is_queued_and_runs_once_the_deploy_is_done(
    client: TestClient,
    secret: str,
    manager: JobManager,
    updates: list[dict[str, Any]],
    store: NoustStore,
) -> None:
    """Not AppBusyError, not a lost push: the update waits its turn."""
    running = Work("deploy", [])
    queue_work(manager, running, DOMAIN, JobType.DEPLOY)
    assert running.started.wait(5)

    response = push(client, secret)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["merged"] is False
    waiting = manager.get_job(body["job_id"])
    assert waiting is not None and waiting.status is JobStatus.PENDING
    assert waiting.queue_position == 1
    assert updates == []
    running.release.set()
    assert wait_until(lambda: waiting.status is JobStatus.COMPLETED)
    assert updates == [{"domain": DOMAIN, "tag": None}]


def test_several_pushes_while_it_builds_are_one_update(
    client: TestClient,
    secret: str,
    manager: JobManager,
    updates: list[dict[str, Any]],
    store: NoustStore,
) -> None:
    """The update that waits builds the branch as it is: the last push wins, nothing runs twice."""
    running = Work("deploy", [])
    queue_work(manager, running, DOMAIN, JobType.DEPLOY)
    assert running.started.wait(5)

    first = push(client, secret).json()
    second = push(client, secret).json()
    third = push(client, secret).json()

    assert first["merged"] is False
    assert (second["merged"], third["merged"]) == (True, True)
    assert first["job_id"] == second["job_id"] == third["job_id"]
    assert len(manager.queue().queued) == 1
    log = deliveries(store)
    assert [row.outcome for row in log] == ["deploy_started"] * 3
    assert all(row.job_id == first["job_id"] for row in log)
    assert "joined the update already waiting" in (log[1].detail or "")
    running.release.set()
    waiting = manager.get_job(first["job_id"])
    assert waiting is not None
    assert wait_until(lambda: waiting.status is JobStatus.COMPLETED)
    assert len(updates) == 1


def test_a_full_queue_answers_429_and_says_so_in_the_delivery_log(
    client: TestClient,
    secret: str,
    manager: JobManager,
    limits: list[delivery.DeliverySettings],
    updates: list[dict[str, Any]],
    store: NoustStore,
    tmp_path: Path,
) -> None:
    """The forge retries a 429; the operator reads why in the webhook's log."""
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, queue_limit=1
    )
    store.create_app(
        App(
            domain="other.example.com", app_type="nodejs", app_path="/var/www/apps/other", port=3010
        )
    )
    running = Work("deploy", [])
    queue_work(manager, running, "other.example.com", JobType.DEPLOY)
    assert running.started.wait(5)
    queue_work(manager, Work("filler", []), "third.example.com", JobType.UPDATE)

    response = push(client, secret)

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["error"] == "queue_full"
    assert "deploy.queue_limit" in body["hint"]
    assert response.headers["Retry-After"] == "30"
    assert [row.outcome for row in deliveries(store)] == ["queue_full"]
    running.release.set()


def test_a_tag_announced_while_an_update_waits_is_the_one_it_deploys(
    secret: str,
    manager: JobManager,
    updates: list[dict[str, Any]],
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For an application that follows tags, the newest tag the queue is told of wins."""
    from noust.core.forge_events import TagEvent

    app = store.get_app(DOMAIN)
    assert app is not None
    store.set_app_follow_tags(DOMAIN, "v*")
    monkeypatch.setattr(hooks_module, "why_tag_is_ignored", lambda app, tag: None)
    running = Work("deploy", [])
    queue_work(manager, running, DOMAIN, JobType.DEPLOY)
    assert running.started.wait(5)
    refreshed = store.get_app(DOMAIN)
    assert refreshed is not None

    def announce(tag: str) -> Any:
        event = TagEvent(forge="github", tag=tag, source="release", sha="abc")
        return hooks_module.queue_tag_update(refreshed, event, provider="github", delivery=tag)

    first = announce("v1.2.0")
    second = announce("v1.3.0")

    assert first.outcome == webhook_deliveries.DEPLOY_STARTED
    assert second.job_id == first.job_id
    assert "now deploys v1.3.0" in second.detail
    running.release.set()
    waiting = manager.get_job(first.job_id or "")
    assert waiting is not None
    assert wait_until(lambda: waiting.status is JobStatus.COMPLETED)
    assert updates == [{"domain": DOMAIN, "tag": "v1.3.0"}]


# ---------------------------------------------------------------------------
# The jobs API
# ---------------------------------------------------------------------------


def test_the_queue_endpoint_lists_what_runs_and_what_waits(
    admin: TestClient, manager: JobManager, seeded: App
) -> None:
    running, waiting = Work("running", []), Work("waiting", [])
    one = queue_work(manager, running, DOMAIN, JobType.DEPLOY)
    assert running.started.wait(5)
    two = queue_work(manager, waiting, DOMAIN)

    response = admin.get("/api/jobs/queue")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [job["id"] for job in body["running"]] == [one.id]
    assert [job["id"] for job in body["queued"]] == [two.id]
    queued = body["queued"][0]
    assert (queued["state"], queued["queue_position"]) == ("queued", 1)
    assert "the deploy of" in queued["waiting_for"]
    assert queued["cancellable"] is True
    assert body["running"][0]["state"] == "running"
    assert (body["builds_running"], body["max_concurrent_builds"], body["queue_limit"]) == (
        1,
        1,
        25,
    )
    running.release.set()
    waiting.release.set()


def test_a_waiting_job_is_cancelled_through_the_api(
    admin: TestClient, manager: JobManager, seeded: App
) -> None:
    running, doomed = Work("running", []), Work("doomed", [])
    queue_work(manager, running, DOMAIN)
    assert running.started.wait(5)
    job = queue_work(manager, doomed, DOMAIN)

    response = admin.post(f"/api/jobs/{job.id}/cancel")

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Job cancelled", "job_id": job.id, "state": "cancelled"}
    after = admin.get(f"/api/jobs/{job.id}").json()
    assert (after["status"], after["state"]) == ("cancelled", "cancelled")
    running.release.set()


def test_a_building_job_is_cancelled_through_the_api_and_the_queue_moves_on(
    admin: TestClient, manager: JobManager, seeded_on_releases: App
) -> None:
    reached = threading.Event()

    def build(*, job_context: JobContext | None = None) -> None:
        with cancellation.operation_window(DOMAIN, "update"):
            reached.set()
            while True:
                cancellation.checkpoint()
                time.sleep(0.01)

    building = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="build",
        func=build,
        metadata={"domain": DOMAIN},
    )
    assert reached.wait(5)
    follow_up = Work("follow-up", [])
    queue_work(manager, follow_up, DOMAIN)
    listed = admin.get(f"/api/jobs/{building.id}").json()
    assert listed["cancellable"] is True

    response = admin.post(f"/api/jobs/{building.id}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "cancelling"
    assert wait_until(lambda: building.status is JobStatus.CANCELLED)
    assert follow_up.started.wait(5)
    after = admin.get(f"/api/jobs/{building.id}").json()
    assert after["status"] == "cancelled"
    assert after["error"] is None
    follow_up.release.set()


def test_cancelling_after_the_release_started_to_activate_is_a_409_that_says_why(
    admin: TestClient, manager: JobManager, seeded_on_releases: App
) -> None:
    committed, done = threading.Event(), threading.Event()

    def build(*, job_context: JobContext | None = None) -> None:
        with cancellation.operation_window(DOMAIN, "update"):
            cancellation.commit("activating release 7")
            committed.set()
            done.wait(5)

    building = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="build",
        func=build,
        metadata={"domain": DOMAIN},
    )
    assert committed.wait(5)

    response = admin.post(f"/api/jobs/{building.id}/cancel")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "cancel_refused"
    assert "already being activated" in body["detail"]
    assert "activating release 7" in body["hint"]
    assert admin.get(f"/api/jobs/{building.id}").json()["cancellable"] is False
    done.set()


def test_a_running_backup_cannot_be_cancelled_and_an_unknown_job_is_a_404(
    admin: TestClient, manager: JobManager, seeded: App
) -> None:
    work = Work("backup", [])
    job = queue_work(manager, work, DOMAIN, JobType.BACKUP)
    assert work.started.wait(5)

    refused = admin.post(f"/api/jobs/{job.id}/cancel")
    unknown = admin.post("/api/jobs/deadbeef/cancel")

    assert refused.status_code == 409
    assert refused.json()["error"] == "cancel_refused"
    assert unknown.status_code == 404
    work.release.set()
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)
    again = admin.post(f"/api/jobs/{job.id}/cancel")
    assert again.status_code == 409
    assert "already ended" in again.json()["detail"]


def test_a_second_update_request_joins_the_one_that_waits(
    admin: TestClient, manager: JobManager, seeded: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.web.api.jobs.check_upstream", lambda domain: None)
    running = Work("running", [])
    queue_work(manager, running, DOMAIN)
    assert running.started.wait(5)

    first = admin.post("/api/jobs/update", json={"domain": DOMAIN})
    second = admin.post("/api/jobs/update", json={"domain": DOMAIN})

    assert first.status_code == second.status_code == 202
    assert first.json()["message"] == "Update job created"
    assert "already waiting" in second.json()["message"]
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    running.release.set()


def test_a_build_the_full_queue_has_no_room_for_is_a_429(
    admin: TestClient,
    manager: JobManager,
    limits: list[delivery.DeliverySettings],
    seeded: App,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("noust.web.api.jobs.check_upstream", lambda domain: None)
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, queue_limit=1
    )
    running = Work("running", [])
    queue_work(manager, running, "busy.example.com")
    assert running.started.wait(5)
    queue_work(manager, Work("filler", []), "filler.example.com")

    response = admin.post("/api/jobs/update", json={"domain": DOMAIN})

    assert response.status_code == 429, response.text
    assert response.json()["error"] == "queue_full"
    assert response.headers["Retry-After"] == "30"
    running.release.set()
