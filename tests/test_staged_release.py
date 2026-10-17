# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build a release without serving it, promote it later (checklist B-18).

On the same real temporary tree the release pipeline's tests use: the release is built where
an update would build it and nothing that serves is touched; promoting it runs the hooks, is
held by a freeze and passes the health gate; and a release that only exists as a build is never
switched to as if it were a rollback.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import deploy_freeze
from noust.core.exceptions import DeploymentError, DeploysFrozenError, ValidationError
from noust.core.store import NoustStore
from noust.deployers import lifecycle, staged
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.releases import ReleaseManager
from tests.test_release_pipeline import (
    BROKEN_SERVER,
    DOMAIN,
    GOOD_SERVER,
    LOCKFILE,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
    write_tree,
)

FIXTURES = (git, machine, root, store)


@pytest.fixture(autouse=True)
def deployers(machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every deployer the module asks for is wired to the fakes."""
    monkeypatch.setattr(
        staged,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            NodeJSDeployer(verbose=False, runner=machine.runner), machine
        ),
    )


@pytest.fixture(autouse=True)
def the_machine_activations_talk_to(
    machine: SimpleNamespace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake machine, where switching to a release that already exists looks for it."""

    def probe(url: str, **kwargs: Any) -> bool:
        server = root / "current" / "server.js"
        if server.is_file() and "throw" in server.read_text():
            return False
        return True

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda **kwargs: machine.git)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", probe)


@pytest.fixture
def deployed(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> tuple[str, str]:
    """An application serving release one, and the commit of a second waiting to be built."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first = active_id(root)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.branch = "main"
    store.update_app(app)
    # A lockfile of its own: the second release installs, as a changed dependency would.
    return first, machine.git.publish(
        node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n", lockfile=LOCKFILE + "\n")
    )


def releases(root: Path) -> list[str]:
    return [release.id for release in ReleaseManager(root).list()]


class TestBuildingWithoutServing:
    def test_the_release_is_built_and_recorded_but_nothing_that_serves_is_touched(
        self,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        first, _commit = deployed
        restarts = list(machine.services.restarts)
        deployments = len(store.list_deployments(DOMAIN))

        built = staged.stage_build(DOMAIN)

        assert active_id(root) == first
        assert machine.services.restarts == restarts
        assert len(store.list_deployments(DOMAIN)) == deployments
        assert built.release_id in releases(root)
        assert built.release_id != first
        app = store.get_app(DOMAIN)
        assert app is not None
        statuses = {r.id: r.status for r in store.list_releases(app.id)}
        assert statuses[built.release_id] == "built" and statuses[first] == "active"
        assert (root / "releases" / built.release_id / "node_modules" / ".installed-by").exists()
        assert machine.runner.cwd_of("npm", "ci")[-1] == root / "releases" / built.release_id

    def test_the_listing_marks_it_as_waiting_and_only_it(
        self, root: Path, deployed: tuple[str, str]
    ) -> None:
        first, _ = deployed
        built = staged.stage_build(DOMAIN)

        listed = {info.id: info for info in lifecycle.list_releases(DOMAIN)}

        assert listed[built.release_id].waiting is True
        assert listed[first].waiting is False
        assert [item.release_id for item in staged.staged_releases(DOMAIN)] == [built.release_id]

    def test_a_pre_deploy_hook_does_not_run_while_it_waits(
        self,
        tmp_path: Path,
        root: Path,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        tree = node_tree(tmp_path / "v3", server=GOOD_SERVER + "// v3\n")
        write_tree(tree, {"noust.yaml": "hooks:\n  pre_deploy:\n    - run: ./migrate.sh\n"})
        machine.git.publish(tree)

        staged.stage_build(DOMAIN)

        assert not [call for call, _cwd in machine.runner.where if call and "migrate.sh" in call[0]]

    def test_a_build_that_fails_leaves_no_release_behind(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        before = releases(root)
        original = machine.runner.stream

        def failing(argv: Any, **kwargs: Any) -> Any:
            if tuple(argv) == ("npm", "ci"):
                raise DeploymentError("npm ci failed")
            return original(argv, **kwargs)

        machine.runner.stream = failing

        with pytest.raises(DeploymentError, match="npm ci failed"):
            staged.stage_build(DOMAIN)

        assert releases(root) == before
        assert staged.staged_releases(DOMAIN) == []

    def test_a_commit_can_be_named(
        self, tmp_path: Path, root: Path, machine: SimpleNamespace, deployed: tuple[str, str]
    ) -> None:
        _first, commit = deployed
        machine.git.publish(node_tree(tmp_path / "v3", server=GOOD_SERVER + "// v3\n"))
        machine.git.resolve_commit = lambda repository, wanted: wanted  # type: ignore[attr-defined]

        built = staged.stage_build(DOMAIN, commit=commit)

        assert built.release_id.split("-")[2] == commit[:7]

    def test_an_application_in_place_cannot_stage(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        app = store.get_app(DOMAIN)
        assert app is not None
        app.layout = "inplace"
        store.update_app(app)

        with pytest.raises(DeploymentError, match="releases"):
            staged.stage_build(DOMAIN)

    def test_building_is_not_held_by_a_freeze_serving_would_be(
        self, deployed: tuple[str, str]
    ) -> None:
        deploy_freeze.create("incident")

        built = staged.stage_build(DOMAIN)

        assert built.release_id


class TestMakingItServe:
    def test_the_release_that_waits_becomes_the_one_that_serves_without_a_second_build(
        self,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        first, _ = deployed
        built = staged.stage_build(DOMAIN)
        installs = len(machine.runner.cwd_of("npm", "ci"))

        promoted = staged.promote_staged(DOMAIN)

        assert promoted.release_id == built.release_id and promoted.previous_id == first
        assert active_id(root) == built.release_id
        assert machine.services.restarts[-1] == f"releases/{built.release_id}"
        assert len(machine.runner.cwd_of("npm", "ci")) == installs
        app = store.get_app(DOMAIN)
        assert app is not None
        statuses = {r.id: r.status for r in store.list_releases(app.id)}
        assert statuses[built.release_id] == "active" and statuses[first] == "superseded"
        newest = store.list_deployments(DOMAIN)[0]
        assert newest.status == "success" and newest.release_id == built.release_id
        assert staged.staged_releases(DOMAIN) == []

    def test_the_hooks_run_then_and_not_before(
        self,
        tmp_path: Path,
        root: Path,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        tree = node_tree(tmp_path / "v3", server=GOOD_SERVER + "// v3\n")
        write_tree(tree, {"noust.yaml": "hooks:\n  pre_deploy:\n    - run: ./migrate.sh\n"})
        machine.git.publish(tree)
        built = staged.stage_build(DOMAIN)
        ran = lambda: [  # noqa: E731
            cwd for call, cwd in machine.runner.where if call and call[0] == "./migrate.sh"
        ]
        assert ran() == []

        staged.promote_staged(DOMAIN, built.release_id)

        assert ran() == [root / "releases" / built.release_id]

    def test_a_release_that_does_not_answer_is_put_back_for_the_one_that_served(
        self,
        tmp_path: Path,
        root: Path,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        first, _ = deployed
        machine.git.publish(node_tree(tmp_path / "v3", server=BROKEN_SERVER))
        built = staged.stage_build(DOMAIN)

        with pytest.raises(DeploymentError, match="did not pass its health check"):
            staged.promote_staged(DOMAIN, built.release_id)

        assert active_id(root) == first

    def test_a_freeze_holds_it_until_a_person_says_so(
        self, root: Path, deployed: tuple[str, str]
    ) -> None:
        built = staged.stage_build(DOMAIN)
        deploy_freeze.create("incident 412")

        with pytest.raises(DeploysFrozenError):
            staged.promote_staged(DOMAIN, built.release_id)
        promoted = staged.promote_staged(DOMAIN, built.release_id, frozen_ok=True)

        assert active_id(root) == promoted.release_id

    def test_only_a_release_that_waits_can_be_promoted(
        self, root: Path, deployed: tuple[str, str]
    ) -> None:
        first, _ = deployed

        with pytest.raises(ValidationError, match="no release that was built and is waiting"):
            staged.promote_staged(DOMAIN)
        staged.stage_build(DOMAIN)
        with pytest.raises(ValidationError, match="not one that was built and is waiting"):
            staged.promote_staged(DOMAIN, first)


class TestItIsNeverSwitchedToLikeARollback:
    def test_activating_it_is_refused_saying_how_to_promote(
        self, root: Path, deployed: tuple[str, str]
    ) -> None:
        built = staged.stage_build(DOMAIN)

        with pytest.raises(DeploymentError) as caught:
            lifecycle.activate_release(DOMAIN, built.release_id)

        assert "has not served yet" in caught.value.message
        assert f"noust releases promote {DOMAIN} {built.release_id}" in (caught.value.details or "")

    def test_a_release_that_served_still_rolls_back(
        self,
        tmp_path: Path,
        root: Path,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
    ) -> None:
        first, _ = deployed
        built = staged.stage_build(DOMAIN)
        staged.promote_staged(DOMAIN, built.release_id)

        outcome = lifecycle.activate_release(DOMAIN, first)

        assert outcome.changed and outcome.went_back and active_id(root) == first

    def test_a_rebuild_of_its_commit_builds_again_instead_of_reusing_it(
        self,
        root: Path,
        machine: SimpleNamespace,
        deployed: tuple[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _first, commit = deployed
        built = staged.stage_build(DOMAIN)
        machine.git.resolve_commit = lambda repository, wanted: commit  # type: ignore[attr-defined]
        monkeypatch.setattr(
            lifecycle,
            "get_deployer",
            lambda app_type, verbose=False: wire(
                NodeJSDeployer(verbose=False, runner=machine.runner), machine
            ),
        )

        lifecycle.update_app(DOMAIN, commit=commit)

        # The waiting release was not switched to: a new one was built and serves.
        assert active_id(root) != built.release_id
        assert built.release_id in releases(root)

    def test_a_release_with_no_row_is_one_from_before_the_store_kept_them(
        self, root: Path, store: NoustStore, deployed: tuple[str, str]
    ) -> None:
        first, _ = deployed
        built = staged.stage_build(DOMAIN)
        app = store.get_app(DOMAIN)
        assert app is not None
        with store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM releases WHERE app_id = ? AND id = ?", (app.id, built.release_id)
            )

        outcome = lifecycle.activate_release(DOMAIN, built.release_id)

        assert (
            outcome.changed and os.path.basename(os.readlink(root / "current")) == built.release_id
        )
        assert first
