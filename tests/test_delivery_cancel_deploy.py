# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling a deployment at each phase of its life.

The tests build on the same real temporary tree the release pipeline's do:
releases, ``current`` and ``shared/`` are real directories, and only the
machine (git, systemd, nginx, the probe) is faked. A cancellation is requested
from inside the phase under test, the way an operator's request would arrive
while the build runs, and what has to hold at every phase is the same:

- what served before still serves, from the release it served from;
- the half-built release is gone from disk and from the store's live releases;
- the deployment's row ends, says it was cancelled and by whom;
- the application's lock is free for whatever is next.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import cancellation, delivery_store
from noust.core.applock import app_lock, holder_of
from noust.core.cancellation import request_cancel
from noust.core.exceptions import CancelRefused, DeploymentCancelled
from noust.core.runner import CommandCancelled
from noust.core.store import NoustStore
from noust.deployers import deploy_events
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.releases import ReleaseManager
from tests.test_release_pipeline import (
    DOMAIN,
    GIT_URL,
    GOOD_SERVER,
    LOCKFILE,
    PORT,
    TreeRunner,
    active_id,
    deploy_new,
    git,
    machine,
    root,
    store,
    update,
    wire,
    write_tree,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (git, machine, root, store)

WHO = "token:ci"


def project(directory: Path, *, version: str = "1") -> Path:
    """
    A Node project that has a build step and its own lockfile.

    Args:
        directory: Where to create it.
        version: Changes the lockfile, so the install cannot be reused from the
            release that serves.

    Returns:
        The directory.
    """
    return write_tree(
        directory,
        {
            "package.json": json.dumps(
                {"name": "app", "scripts": {"build": "node build.js", "start": "node server.js"}}
            ),
            "package-lock.json": LOCKFILE.replace("lockfileVersion", f"v{version}-lockfileVersion"),
            "server.js": GOOD_SERVER + f"// v{version}\n",
        },
    )


class CancellingRunner(TreeRunner):
    """
    A runner that is told to cancel while one command runs.

    The real runner kills the command and raises ``CommandCancelled`` when the
    operation's cancel event is set; this one asks for the cancellation from
    inside the command and raises the same, which is what the deployer sees.

    Attributes:
        on: The command during which the operator asks to cancel.
    """

    def __init__(self, on: tuple[str, ...] | None = None) -> None:
        super().__init__()
        self.on = on

    def arm(self, argv: Any) -> None:
        """
        Cancel if this is the command the test is about.

        Args:
            argv: The command about to run.

        Raises:
            CommandCancelled: It is, and the cancellation was asked for.
        """
        if self.on is not None and tuple(argv) == self.on:
            request_cancel(DOMAIN, by=WHO)
            raise CommandCancelled(f"Cancelled: {' '.join(argv)}")

    def run(self, argv: Any, **kwargs: Any) -> Any:
        self.arm(argv)
        return super().run(argv, **kwargs)

    def stream(self, argv: Any, **kwargs: Any) -> Any:
        self.arm(argv)
        return super().stream(argv, **kwargs)


@pytest.fixture
def served(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> SimpleNamespace:
    """
    An application deployed once and serving release one.

    Returns:
        The release that serves and what the machine did to serve it.
    """
    machine.git.publish(project(tmp_path / "v1", version="1"))
    deploy_new(root, machine)
    first = active_id(root)
    machine.git.publish(project(tmp_path / "v2", version="2"))
    return SimpleNamespace(first=first, restarts=list(machine.services.restarts))


def update_with(
    machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, runner: CancellingRunner
) -> Any:
    """
    Run an update whose commands go through ``runner``.

    Args:
        machine: The fakes.
        monkeypatch: Patching helper.
        runner: The runner.

    Returns:
        What the update returned.
    """
    machine.runner = runner
    return update(machine, monkeypatch)


def assert_untouched(root: Path, store: NoustStore, machine: SimpleNamespace, first: str) -> None:
    """
    Check what every cancellation before activation promises.

    Args:
        root: The application directory.
        store: The store.
        machine: The fakes.
        first: The release that served before.
    """
    assert active_id(root) == first
    assert [release.id for release in ReleaseManager(root).list()] == [first]
    assert machine.services.restarts == [f"releases/{first}"]
    app = store.get_app(DOMAIN)
    assert app is not None
    live = [r.id for r in store.list_releases(app.id) if r.status in ("active", "built")]
    assert live == [first]
    assert holder_of(DOMAIN) is None
    assert cancellation.token_of(DOMAIN) is None


def assert_recorded_as_cancelled(store: NoustStore) -> None:
    """
    Check the history row of the cancelled attempt.

    Args:
        store: The store.
    """
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "failed"
    assert row.cancelled is True
    assert row.cancelled_by == WHO
    assert row.finished_at is not None
    assert row.error is not None and "was cancelled" in row.error
    # A cancellation is not a build error: nothing from the runner leaks in.
    assert "Cancelled:" not in row.error


# ---------------------------------------------------------------------------
# An update, cancelled at each phase
# ---------------------------------------------------------------------------


def test_cancelled_while_the_source_is_being_fetched(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clone that is killed leaves no half-filled release behind."""
    original = machine.git.sync_cache

    def sync(source: str, cache: Path, branch: str | None = None) -> str | None:
        original(source, cache, branch)
        request_cancel(DOMAIN, by=WHO)
        raise CommandCancelled("Cancelled: git fetch")

    machine.git.sync_cache = sync

    with pytest.raises(DeploymentCancelled, match="cancelled by token:ci"):
        update(machine, monkeypatch)

    assert_untouched(root, store, machine, served.first)
    assert_recorded_as_cancelled(store)


def test_cancelled_while_the_dependencies_install(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The install is killed; the release it was filling is removed."""
    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "ci")))

    assert_untouched(root, store, machine, served.first)
    assert_recorded_as_cancelled(store)
    # The build never started.
    assert ("npm", "run", "build") not in [call for call, _ in machine.runner.where]


def test_cancelled_while_the_application_builds(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build is the longest phase, and the one an operator most often stops."""
    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "run", "build")))

    assert_untouched(root, store, machine, served.first)
    assert_recorded_as_cancelled(store)


def test_cancelled_between_steps_by_a_request_the_runner_never_saw(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that cannot be interrupted finishes; the checkpoint after it stops the update."""

    class Asking(NodeJSDeployer):
        def _set_permissions(self) -> None:
            super()._set_permissions()
            request_cancel(DOMAIN, by=WHO)

    machine.runner = TreeRunner()
    monkeypatch.setattr(
        "noust.deployers.lifecycle.get_deployer",
        lambda app_type, verbose=False: wire(Asking(verbose=False, runner=machine.runner), machine),
    )
    from noust.deployers import lifecycle

    with pytest.raises(DeploymentCancelled):
        lifecycle.update_app(DOMAIN)

    assert_untouched(root, store, machine, served.first)
    assert_recorded_as_cancelled(store)
    assert machine.runner.cwd_of("npm", "run", "build") != []


def test_cancelled_before_the_pre_deploy_hooks_run(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hooks may migrate a database: once started they finish, and one asked-for is never run."""
    ran: list[str] = []

    class Hooked(NodeJSDeployer):
        def _run_pre_deploy_hooks(self, report: Any = None) -> None:
            ran.append("pre_deploy")

        def _set_permissions(self) -> None:
            super()._set_permissions()
            request_cancel(DOMAIN, by=WHO)

    monkeypatch.setattr(
        "noust.deployers.lifecycle.get_deployer",
        lambda app_type, verbose=False: wire(Hooked(verbose=False, runner=machine.runner), machine),
    )
    from noust.deployers import lifecycle

    with pytest.raises(DeploymentCancelled):
        lifecycle.update_app(DOMAIN)

    assert ran == []
    assert_untouched(root, store, machine, served.first)


def test_a_request_during_activation_is_refused_and_the_update_completes(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the point of no return the release serves, or is put back by its own gate."""
    refusals: list[CancelRefused] = []
    original = machine.services.restart

    def restart(name: str) -> None:
        try:
            request_cancel(DOMAIN, by=WHO)
        except CancelRefused as refused:
            refusals.append(refused)
        original(name)

    machine.services.restart = restart

    outcome = update(machine, monkeypatch)

    assert outcome.active is True
    assert [refused.reason for refused in refusals] == ["too_late"]
    assert active_id(root) != served.first
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "success"
    assert row.cancelled is False
    assert holder_of(DOMAIN) is None


def test_a_request_after_the_update_finished_is_refused_as_idle(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is nothing left to cancel once the deployment is over."""
    update(machine, monkeypatch)

    with pytest.raises(CancelRefused) as caught:
        request_cancel(DOMAIN, by=WHO)

    assert caught.value.reason == "idle"


def test_the_next_update_runs_after_a_cancelled_one(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock is free, the request is gone, and the next update is not cancelled by it."""
    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "run", "build")))

    outcome = update_with(machine, monkeypatch, CancellingRunner(None))

    assert outcome.active is True
    assert active_id(root) != served.first
    rows = store.list_deployments(DOMAIN)
    assert [(row.status, row.cancelled) for row in rows[:2]] == [
        ("success", False),
        ("failed", True),
    ]


def test_a_cancelled_update_is_announced_as_cancelled_not_as_a_failure(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscribers (notifications, GitHub) are told it was an operator's decision."""
    heard: list[deploy_events.DeployEvent] = []
    deploy_events.subscribe(heard.append)

    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "run", "build")))

    finished = [event for event in heard if event.kind is not deploy_events.DeployEventKind.STARTED]
    assert [event.cancelled for event in finished] == [True]
    assert finished[0].kind is deploy_events.DeployEventKind.FAILED
    assert finished[0].operation == "update"


def test_the_requester_is_told_it_was_cancelled(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer the operation writes is what the CLI prints."""
    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "ci")))

    answer = delivery_store.get_request(DOMAIN)
    assert answer is not None
    assert answer.outcome == delivery_store.CANCELLED
    assert answer.requested_by == WHO


# ---------------------------------------------------------------------------
# A new application, cancelled
# ---------------------------------------------------------------------------


def test_a_new_application_cancelled_while_it_builds_leaves_nothing_behind(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    """The pipeline undoes what ran: no directory, no row, no unit, no site."""
    machine.git.publish(project(tmp_path / "v1"))
    machine.runner = CancellingRunner(("npm", "run", "build"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout="releases")

    with pytest.raises(DeploymentCancelled):
        deployer.deploy()

    assert not root.exists()
    assert store.get_app(DOMAIN) is None
    assert machine.services.units == {}
    assert machine.web.sites == {}
    # The history outlives the application it was about, by design.
    row = store.list_deployments(DOMAIN)[0]
    assert (row.status, row.cancelled, row.cancelled_by) == ("failed", True, WHO)
    assert holder_of(DOMAIN) is None


def test_a_new_application_cancelled_between_pipeline_steps_never_starts_the_next(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    """The step that finished is undone; the one that was about to start never runs."""
    machine.git.publish(project(tmp_path / "v1"))

    class Asking(NodeJSDeployer):
        def create_site(self, with_ssl: bool = False) -> bool:
            made = super().create_site(with_ssl=with_ssl)
            request_cancel(DOMAIN, by=WHO)
            return made

    deployer = wire(Asking(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout="releases")

    with pytest.raises(DeploymentCancelled):
        deployer.deploy()

    assert machine.services.units == {}
    assert machine.web.sites == {}
    assert machine.services.restarts == []
    assert not root.exists()
    assert store.get_app(DOMAIN) is None


def test_a_new_application_cancelled_during_its_first_activation_is_refused(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    """Even a first release is past the point of no return once it activates."""
    machine.git.publish(project(tmp_path / "v1"))
    refusals: list[str] = []
    original = machine.services.restart

    def restart(name: str) -> None:
        try:
            request_cancel(DOMAIN, by=WHO)
        except CancelRefused as refused:
            refusals.append(refused.reason)
        original(name)

    machine.services.restart = restart

    deploy_new(root, machine)

    assert refusals == ["too_late"]
    assert store.get_app(DOMAIN) is not None


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


def test_the_lock_is_free_the_moment_a_cancelled_update_ends(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    served: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever waited behind it can take the application immediately."""
    with pytest.raises(DeploymentCancelled):
        update_with(machine, monkeypatch, CancellingRunner(("npm", "run", "build")))

    with app_lock(DOMAIN, "rollback"):
        assert holder_of(DOMAIN) is not None
    assert os.path.exists(root / "current")
