# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust deploy cancel`` and ``noust deploy queue``.

The commands run in a terminal while the deployment runs elsewhere (the
console's queue, a webhook's update, another terminal), so what they share
with it is the store and the application's lock. The deployment in these tests
is a thread that takes the real lock and opens the real cancel window, and the
command is told it is not in the same process, so the request travels through
the store exactly as it does between two processes.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import ALIASES
from noust.cli.app import cli as root_cli
from noust.core import cancellation, delivery
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentCancelled
from noust.core.store import App, AppLayout, JobRecord, NoustStore

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store in the test directory with an application on releases.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=f"/var/www/apps/{DOMAIN}",
            port=3000,
            layout=AppLayout.RELEASES.value,
        )
    )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ask for the limits the defaults give, and look for requests quickly."""
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=False)
    )
    monkeypatch.setattr(cancellation, "POLL_INTERVAL", 0.05)


def invoke(*args: str) -> Result:
    """
    Run the real root group.

    Args:
        *args: The command line.

    Returns:
        The result. A refusal is the exception it carries: the error boundary
        that prints it and exits 1 is ``main``'s, which :func:`said` reads the
        words of.
    """
    return CliRunner().invoke(root_cli, list(args))


def said(result: Result) -> str:
    """
    Everything the operator would read: what was printed, and the refusal.

    Args:
        result: What :func:`invoke` returned.

    Returns:
        The output followed by the error's message and details.
    """
    return result.output + (str(result.exception) if result.exception else "")


class Deployment:
    """
    A deployment running in "another process": a thread that holds the lock.

    Attributes:
        building: Set once the cancel window is open.
        release: Set by the test to let a deployment that nobody cancels finish.
        outcome: ``cancelled`` or ``finished`` once it ends.
    """

    def __init__(self, *, activating: bool = False) -> None:
        self.building = threading.Event()
        self.release = threading.Event()
        self.outcome: str | None = None
        self._activating = activating
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> Deployment:
        """
        Returns:
            Itself, running and building (or, past the point of no return,
            activating).
        """
        self._thread.start()
        assert self.building.wait(5)
        return self

    def _run(self) -> None:
        try:
            with app_lock(DOMAIN, "update"), cancellation.operation_window(DOMAIN, "update"):
                if self._activating:
                    cancellation.commit("activating release 20261006")
                self.building.set()
                deadline = time.monotonic() + 10
                while not self.release.is_set() and time.monotonic() < deadline:
                    cancellation.checkpoint()
                    time.sleep(0.01)
            self.outcome = "finished"
        except DeploymentCancelled:
            self.outcome = "cancelled"

    def join(self) -> None:
        """Let it finish if nobody cancelled it, and wait for it."""
        self.release.set()
        self._thread.join(10)


@pytest.fixture
def another_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make this process unable to see the deployment's token, as a terminal cannot.

    The request then travels the one way it can between processes: through the store.
    """
    monkeypatch.setattr(cancellation, "token_of", lambda domain: None)


def test_deploy_is_still_create(store: NoustStore) -> None:
    """The old spelling keeps working: the verbs are only what it also does when named."""
    assert ALIASES["deploy"] == "create"
    assert "Usage: cli create" in invoke("deploy", "--help").output
    assert "Usage: cli deploy cancel" in invoke("deploy", "cancel", "--help").output
    assert "Usage: cli deploy queue" in invoke("deploy", "queue", "--help").output


def test_cancelling_what_is_not_running_says_so(store: NoustStore) -> None:
    result = invoke("deploy", "cancel", DOMAIN)

    assert result.exit_code != 0
    assert f"Nothing is running on {DOMAIN}" in said(result)


def test_a_build_in_another_process_is_stopped_through_the_store(
    store: NoustStore, another_process: None
) -> None:
    """The terminal asks, the deployment's own process stops, the terminal is told."""
    deployment = Deployment().start()

    result = invoke("deploy", "cancel", DOMAIN)

    deployment.join()
    assert result.exit_code == 0, result.output
    assert deployment.outcome == "cancelled"
    assert f"Cancelled the update of {DOMAIN}" in said(result)
    assert "What served before was not touched" in said(result)


def test_a_job_that_builds_can_be_named_by_its_id(store: NoustStore, another_process: None) -> None:
    """The id the console shows works as well as the domain."""
    store.create_job(
        JobRecord(id="abc12345", type="update", name="Update", status="running", domain=DOMAIN)
    )
    deployment = Deployment().start()

    result = invoke("deploy", "cancel", "abc12345")

    deployment.join()
    assert result.exit_code == 0, result.output
    assert deployment.outcome == "cancelled"


def test_a_job_that_waits_is_marked_cancelled_for_the_queue_to_read(store: NoustStore) -> None:
    store.create_job(
        JobRecord(id="abc12345", type="update", name="Update", status="pending", domain=DOMAIN)
    )

    result = invoke("deploy", "cancel", "abc12345")

    assert result.exit_code == 0, result.output
    assert "it never started" in said(result)
    record = store.get_job("abc12345")
    assert record is not None and record.status == "cancelled" and record.finished_at


def test_a_job_that_ended_cannot_be_cancelled(store: NoustStore) -> None:
    store.create_job(
        JobRecord(id="abc12345", type="update", name="Update", status="completed", domain=DOMAIN)
    )

    result = invoke("deploy", "cancel", "abc12345")

    assert result.exit_code != 0
    assert "already ended as succeeded" in said(result)


def test_after_the_point_of_no_return_it_is_too_late_and_the_deployment_finishes(
    store: NoustStore, another_process: None
) -> None:
    deployment = Deployment(activating=True).start()

    result = invoke("deploy", "cancel", DOMAIN)

    deployment.join()
    assert result.exit_code != 0
    assert deployment.outcome == "finished"
    assert "activation" in said(result)
    assert f"noust releases rollback {DOMAIN}" in said(result)


def test_a_build_in_place_is_refused_with_the_reason_whichever_process_runs_it(
    store: NoustStore, another_process: None
) -> None:
    """It builds in the tree that serves: the deployment says so, the terminal repeats it."""
    row = store.get_app(DOMAIN)
    assert row is not None
    row.layout = AppLayout.INPLACE.value
    store.update_app(row)
    deployment = Deployment().start()

    result = invoke("deploy", "cancel", DOMAIN)

    deployment.join()
    assert result.exit_code != 0
    assert deployment.outcome == "finished"
    assert "cannot be interrupted" in said(result)
    assert "builds in place" in said(result)


def test_it_can_ask_and_return_without_waiting(store: NoustStore, another_process: None) -> None:
    deployment = Deployment().start()

    result = invoke("deploy", "cancel", DOMAIN, "--wait", "0")

    assert result.exit_code == 0, result.output
    assert "Asked the update" in said(result)
    deployment.join()


def test_an_operation_that_cannot_be_interrupted_is_refused_up_front(store: NoustStore) -> None:
    with app_lock(DOMAIN, "rollback"):
        result = invoke("deploy", "cancel", DOMAIN)

    assert result.exit_code != 0
    assert "rollback running on" in said(result)
    assert "cannot be cancelled" in said(result)


def test_something_that_is_neither_a_job_nor_a_domain_is_a_usage_error(store: NoustStore) -> None:
    result = invoke("deploy", "cancel", "not a thing")

    assert result.exit_code == 2
    assert "neither a job of the queue nor an application's domain" in said(result)


def test_waiting_requests_are_cancelled_with_all_and_named_when_nothing_builds(
    store: NoustStore,
) -> None:
    for number in (1, 2):
        store.create_job(
            JobRecord(
                id=f"abc0000{number}", type="update", name="Update", status="pending", domain=DOMAIN
            )
        )

    refused = invoke("deploy", "cancel", DOMAIN)
    everything = invoke("deploy", "cancel", DOMAIN, "--all")

    assert refused.exit_code != 0
    assert "2 request(s) wait for it" in said(refused)
    assert everything.exit_code == 0, everything.output
    assert "Cancelled 2 waiting request(s)" in everything.output
    for number in (1, 2):
        record = store.get_job(f"abc0000{number}")
        assert record is not None and record.status == "cancelled"


def test_the_queue_is_empty_when_nothing_deploys(store: NoustStore) -> None:
    result = invoke("deploy", "queue")

    assert result.exit_code == 0, result.output
    assert "Nothing is deploying and nothing is waiting" in said(result)


def test_the_queue_lists_what_builds_and_what_waits_in_order(store: NoustStore) -> None:
    store.create_job(
        JobRecord(
            id="run00001",
            type="update",
            name="Update",
            status="running",
            domain=DOMAIN,
            created_at="2026-10-06T10:00:00",
            started_at="2026-10-06T10:00:01",
        )
    )
    for number, created in ((2, "2026-10-06T10:00:05"), (3, "2026-10-06T10:00:09")):
        store.create_job(
            JobRecord(
                id=f"wait000{number}",
                type="update",
                name="Update",
                status="pending",
                domain=DOMAIN,
                created_at=created,
            )
        )
    store.create_job(
        JobRecord(id="bak00001", type="backup", name="Backup", status="pending", domain=DOMAIN)
    )

    result = invoke("deploy", "queue")

    assert result.exit_code == 0, result.output
    out = result.output
    assert out.index("run00001") < out.index("wait0002") < out.index("wait0003")
    assert "bak00001" not in out, "only builds are the deploy queue"
    assert "queued 1" in out and "queued 2" in out
    assert f"the update of {DOMAIN} (job run00001)" in out
    assert "1 of 2 builds running, 2 of 25 waiting" in out


def test_the_queue_is_json_and_can_be_narrowed_to_an_application(store: NoustStore) -> None:
    store.create_app(App(domain="other.example.com", app_type="nodejs", port=3001))
    store.create_job(
        JobRecord(id="run00001", type="deploy", name="Deploy", status="running", domain=DOMAIN)
    )
    store.create_job(
        JobRecord(
            id="wait0001",
            type="update",
            name="Update",
            status="pending",
            domain="other.example.com",
        )
    )

    everything = json.loads(invoke("deploy", "queue", "--json").output)
    narrowed = json.loads(invoke("--json", "deploy", "queue", DOMAIN).output)

    assert everything["max_concurrent_builds"] == 2 and everything["queue_limit"] == 25
    assert [job["id"] for job in everything["running"]] == ["run00001"]
    queued: list[dict[str, Any]] = everything["queued"]
    assert [(job["id"], job["queue_position"], job["state"]) for job in queued] == [
        ("wait0001", 1, "queued")
    ]
    assert narrowed["queued"] == []
