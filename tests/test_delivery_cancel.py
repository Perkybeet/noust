# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling a deployment while it builds: the window, the request and the answer.

The request is addressed to the operation that holds an application's lock, by
the pid and start time it recorded, so these tests take the real lock
(:func:`noust.core.applock.app_lock`) in a store of the test's own and prove
what each side may and may not do: who is refused, what is honoured, and what a
request that outlived its operation can never do.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import cancellation, delivery_store
from noust.core.applock import app_lock, held_record
from noust.core.cancellation import (
    interruptible,
    is_cancellation,
    operation_window,
    request_cancel,
    wait_for_outcome,
)
from noust.core.exceptions import CancelRefused, DeploymentCancelled, DeploymentError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import CommandCancelled, FakeRunner
from noust.core.store import App, AppLayout, NoustStore

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store in the test directory with one application on the release layout.

    Yields:
        The store; the lock files live beside it.
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
def quick_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Look for requests of other processes ten times a second, not twice."""
    monkeypatch.setattr(cancellation, "POLL_INTERVAL", 0.05)


def wait_until(condition: object, seconds: float = 20.0) -> bool:
    """
    Poll a condition.

    Args:
        condition: A callable returning a truth value.
        seconds: How long to wait.

    Returns:
        Whether it became true.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_a_window_is_addressed_by_what_the_lock_recorded(store: NoustStore) -> None:
    """The token carries the pid and start time the lock holder wrote."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        record = held_record(DOMAIN)
        assert record is not None
        assert token.holder == record
        assert cancellation.token_of(DOMAIN) is token
        assert cancellation.current() is token

    assert cancellation.token_of(DOMAIN) is None
    assert cancellation.current() is None


def test_a_window_without_the_lock_has_nothing_to_address(store: NoustStore) -> None:
    """A deployer used on its own (a test, a script) is not cancellable, and not broken."""
    with operation_window(DOMAIN, "update") as token:
        assert token is None
        cancellation.checkpoint()
        with interruptible("building"):
            pass


def test_a_rehearsal_opens_no_window(store: NoustStore) -> None:
    """Under --dry-run no lock is taken, so there is no operation to cancel."""
    set_fs(DryRunFileSystem(on_skip=lambda _message: None))

    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is None


def test_an_inner_operation_shares_the_outer_window(store: NoustStore) -> None:
    """An update that hands over to a deployer is one cancellable operation."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as outer:
        with app_lock(DOMAIN, "deploy"), operation_window(DOMAIN, "deploy") as inner:
            assert inner is outer


def test_the_window_closes_when_the_operation_raises(store: NoustStore) -> None:
    """A failure leaves no token behind to be cancelled by the next one."""
    with pytest.raises(DeploymentError):
        with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update"):
            raise DeploymentError("the build failed")

    assert cancellation.token_of(DOMAIN) is None
    assert cancellation.current() is None


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def test_cancelling_an_operation_of_this_process_is_told_at_once(store: NoustStore) -> None:
    """The token is tripped before the request returns, and the next checkpoint stops."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        token.phase = "building"

        result = request_cancel(DOMAIN, by="token:ci")

        assert result.immediate is True
        assert result.operation == "update"
        assert result.phase == "building"
        assert token.requested is True
        assert token.requested_by == "token:ci"
        with pytest.raises(DeploymentCancelled, match="cancelled by token:ci"):
            cancellation.checkpoint()
        assert token.tripped is True


def test_nothing_running_is_refused_as_idle(store: NoustStore) -> None:
    """There is nothing to cancel when no operation holds the lock."""
    with pytest.raises(CancelRefused) as caught:
        request_cancel(DOMAIN, by="op")

    assert caught.value.reason == "idle"
    assert DOMAIN in caught.value.message


@pytest.mark.parametrize("operation", ["rollback", "migration", "deletion", "restore"])
def test_operations_that_change_what_serves_are_refused(store: NoustStore, operation: str) -> None:
    """Only a deploy or an update that builds can be stopped."""
    with app_lock(DOMAIN, operation):
        with pytest.raises(CancelRefused) as caught:
            request_cancel(DOMAIN, by="op")

    assert caught.value.reason == "unsupported"
    assert operation in caught.value.message


def test_a_build_in_place_is_refused_and_explains_why(store: NoustStore) -> None:
    """Stopping a build in the live tree would leave the application broken."""
    store.create_app(
        App(domain="old.example.com", app_type="nodejs", app_path="/var/www/apps/old", port=3010)
    )

    with app_lock("old.example.com", "update"):
        with operation_window("old.example.com", "update") as token:
            assert token is not None and token.supported is False
            with pytest.raises(CancelRefused) as caught:
                request_cancel("old.example.com", by="op")
            # The build is never interrupted either.
            with interruptible("building"):
                cancellation.checkpoint()

    assert caught.value.reason == "unsupported"
    assert "in place" in caught.value.details


def test_a_compose_stack_can_be_stopped_while_its_images_build(store: NoustStore) -> None:
    """The daemon stops the build when its client is killed; the containers that serve are untouched."""
    store.create_app(
        App(
            domain="stack.example.com",
            app_type="docker-compose",
            app_path="/var/www/apps/stack",
            layout=AppLayout.RELEASES.value,
        )
    )

    assert cancellation.supports_cancel("stack.example.com", store) == (True, "")


def test_a_monorepo_built_in_place_is_refused_and_says_why(store: NoustStore) -> None:
    """Its units run from the tree the build writes to."""
    store.create_app(
        App(
            domain="mono.example.com",
            app_type="monorepo",
            app_path="/var/www/apps/mono",
            layout=AppLayout.INPLACE.value,
        )
    )

    supported, why = cancellation.supports_cancel("mono.example.com", store)

    assert supported is False
    assert "in place" in why


def test_a_new_application_of_any_type_can_be_stopped(store: NoustStore) -> None:
    """Nothing serves yet, so nothing can be broken: the caller knowing the type changes nothing."""
    for kind in ("monorepo", "docker-compose", "container"):
        assert cancellation.supports_cancel("brand-new.example.com", store, kind) == (True, "")

    with app_lock("brand-new.example.com", "deploy"):
        result = request_cancel("brand-new.example.com", by="op", app_type="docker-compose")

    assert result.operation == "deploy"


def test_a_deployer_chosen_after_the_window_opened_can_take_the_cancellation_away(
    store: NoustStore,
) -> None:
    """The automatic deployer finds out what the source is only after it fetched it."""
    with app_lock(DOMAIN, "deploy"), operation_window(DOMAIN, "deploy") as token:
        assert token is not None and token.supported is True

        cancellation.restrict("monorepo applications are built by their own tools")

        assert token.supported is False
        with pytest.raises(CancelRefused) as caught:
            request_cancel(DOMAIN, by="op")
        assert "built by their own tools" in caught.value.details
        cancellation.checkpoint()


def test_restricting_outside_a_window_is_nothing(store: NoustStore) -> None:
    cancellation.restrict("whatever")


def test_a_new_application_can_be_cancelled(store: NoustStore) -> None:
    """Nothing serves yet, so nothing can be broken."""
    assert cancellation.supports_cancel("brand-new.example.com", store) == (True, "")


def test_after_the_point_of_no_return_the_request_is_refused_as_too_late(
    store: NoustStore,
) -> None:
    """Activation finishes, or puts back what served, by itself."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        cancellation.commit("activating release 20261006")

        with pytest.raises(CancelRefused) as caught:
            request_cancel(DOMAIN, by="op")

        assert caught.value.reason == "too_late"
        assert "activating release 20261006" in caught.value.details
        # And nothing stops from now on, whatever was asked.
        cancellation.checkpoint()
        with interruptible("building"):
            pass
        assert token.tripped is False


def test_a_request_that_arrives_during_activation_is_answered_too_late(
    store: NoustStore,
) -> None:
    """A request from another process, seen after the commit, ends as too_late."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        record = held_record(DOMAIN)
        assert record is not None
        cancellation.commit("activating")
        delivery_store.put_request(
            DOMAIN, holder_pid=record.pid, holder_started_at=record.started_at, requested_by="x"
        )

    answer = delivery_store.get_request(DOMAIN)
    assert answer is not None and answer.outcome == delivery_store.TOO_LATE


# ---------------------------------------------------------------------------
# Requests of other processes, and requests that outlived their operation
# ---------------------------------------------------------------------------


def test_a_request_recorded_by_another_process_is_found_while_it_builds(
    store: NoustStore,
) -> None:
    """The watcher reads the store: the CLI's request stops the console's build."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        record = held_record(DOMAIN)
        assert record is not None
        # What `noust deploy cancel` writes from its own process.
        delivery_store.put_request(
            DOMAIN,
            holder_pid=record.pid,
            holder_started_at=record.started_at,
            requested_by="root@tty1",
        )

        assert wait_until(lambda: token.requested)
        assert token.requested_by == "root@tty1"
        with pytest.raises(DeploymentCancelled):
            cancellation.checkpoint()

    answer = delivery_store.get_request(DOMAIN)
    assert answer is not None and answer.outcome == delivery_store.CANCELLED


def test_a_request_made_before_the_window_opened_stops_it_before_any_work(
    store: NoustStore,
) -> None:
    """Cancelled the moment it started: not a single step runs."""
    ran: list[str] = []
    with app_lock(DOMAIN, "update"):
        record = held_record(DOMAIN)
        assert record is not None
        delivery_store.put_request(
            DOMAIN, holder_pid=record.pid, holder_started_at=record.started_at, requested_by="op"
        )
        with pytest.raises(DeploymentCancelled):
            with operation_window(DOMAIN, "update"):
                ran.append("step")

    assert ran == []
    answer = delivery_store.get_request(DOMAIN)
    assert answer is not None and answer.outcome == delivery_store.CANCELLED


def test_a_request_addressed_to_an_earlier_operation_cancels_nothing(store: NoustStore) -> None:
    """A stale request, left by a process that died, is dropped by the next operation."""
    delivery_store.put_request(
        DOMAIN, holder_pid=999_999, holder_started_at="2020-01-01T00:00:00+00:00", requested_by="x"
    )

    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        time.sleep(0.2)
        assert token.requested is False
        cancellation.checkpoint()

    assert delivery_store.get_request(DOMAIN) is None


def test_a_second_request_for_the_same_operation_keeps_the_first_requester(
    store: NoustStore,
) -> None:
    """Asking twice is one request, made by whoever asked first."""
    first = delivery_store.put_request(
        DOMAIN, holder_pid=10, holder_started_at="t", requested_by="alice"
    )
    second = delivery_store.put_request(
        DOMAIN, holder_pid=10, holder_started_at="t", requested_by="bob"
    )

    assert second == first
    assert second.requested_by == "alice"


def test_the_requester_learns_what_became_of_it(store: NoustStore) -> None:
    """``wait_for_outcome`` reads the answer the operation wrote."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        result = request_cancel(DOMAIN, by="op")
        with pytest.raises(DeploymentCancelled):
            cancellation.checkpoint()

    assert wait_for_outcome(DOMAIN, result, timeout=1.0, sleep=0.01) == delivery_store.CANCELLED


def test_an_operation_that_ends_without_looking_is_reported_as_finished(
    store: NoustStore,
) -> None:
    """A request the operation never saw (it was already done) says so."""
    with app_lock(DOMAIN, "update"):
        record = held_record(DOMAIN)
        assert record is not None
        result = cancellation.CancelResult(
            DOMAIN, "update", record.pid, record.started_at, immediate=False
        )
        delivery_store.put_request(
            DOMAIN, holder_pid=record.pid, holder_started_at=record.started_at, requested_by="op"
        )

    assert wait_for_outcome(DOMAIN, result, timeout=1.0, sleep=0.01) == delivery_store.FINISHED


def test_waiting_gives_up_while_the_operation_still_runs(store: NoustStore) -> None:
    """Still building after the wait: the caller is told it is not over, not that it worked."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update"):
        result = request_cancel(DOMAIN, by="op")

        assert wait_for_outcome(DOMAIN, result, timeout=0.1, sleep=0.01) is None


def test_the_outcome_must_be_a_known_one(store: NoustStore) -> None:
    """The closed set is the contract between the two processes."""
    with pytest.raises(ValueError, match="not an outcome"):
        delivery_store.decide(DOMAIN, 1, "t", "maybe")


# ---------------------------------------------------------------------------
# What stops, and what does not
# ---------------------------------------------------------------------------


def test_a_command_that_was_cancelled_becomes_the_cancellation(store: NoustStore) -> None:
    """The runner's CommandCancelled never reaches the operator as a build error."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        with pytest.raises(DeploymentCancelled) as caught:
            with interruptible("installing dependencies"):
                token.request("op")
                raise CommandCancelled("Cancelled: npm ci")

        assert isinstance(caught.value.__cause__, CommandCancelled)
        assert token.tripped is True


def test_a_command_started_after_the_request_never_starts(store: NoustStore) -> None:
    """The runner's cancel scope refuses to start what comes after the request."""
    runner = FakeRunner()
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        with pytest.raises(DeploymentCancelled):
            with interruptible("building"):
                runner.run(["npm", "ci"], timeout=10)
                token.request("op")
                runner.run(["npm", "run", "build"], timeout=10)

    assert runner.calls == [("npm", "ci")]


def test_the_undo_of_a_cancelled_step_can_still_run_commands(store: NoustStore) -> None:
    """The cancel scope covers the step, not what puts the machine back."""
    runner = FakeRunner()
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        with pytest.raises(DeploymentCancelled):
            with interruptible("building"):
                token.request("op")
                runner.run(["npm", "ci"], timeout=10)
        # Outside the region, as a pipeline's undo is.
        runner.run(["systemctl", "daemon-reload"], timeout=10)

    assert runner.calls == [("systemctl", "daemon-reload")]


def test_a_checkpoint_between_steps_stops_a_cancellation_asked_during_one(
    store: NoustStore,
) -> None:
    """A step that cannot be interrupted finishes; the next one never starts."""
    steps: list[str] = []
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        with pytest.raises(DeploymentCancelled):
            steps.append("write the site")
            request_cancel(DOMAIN, by="op")
            cancellation.checkpoint()
            steps.append("write the unit")

    assert steps == ["write the site"]


def test_is_cancellation_sees_through_a_wrapped_error(store: NoustStore) -> None:
    """A deployer that wraps what its build raised does not turn a cancel into a failure."""
    try:
        try:
            raise CommandCancelled("Cancelled: npm ci")
        except CommandCancelled as inner:
            raise DeploymentError("Build failed") from inner
    except DeploymentError as wrapped:
        assert is_cancellation(wrapped) is True

    assert is_cancellation(DeploymentError("Build failed")) is False
    assert is_cancellation("Build failed") is False
    assert is_cancellation(None) is False


def test_is_cancellation_asks_the_operation_when_the_chain_is_lost(store: NoustStore) -> None:
    """The build was stopped by the cancellation: what follows is its consequence."""
    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update") as token:
        assert token is not None
        failure = DeploymentError("Build failed")
        assert is_cancellation(failure) is False

        with pytest.raises(DeploymentCancelled):
            with interruptible("building"):
                token.request("op")
                raise CommandCancelled("Cancelled")

        assert is_cancellation(failure) is True
        assert cancellation.cancelled_by(failure) == "op"
