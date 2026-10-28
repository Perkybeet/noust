# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes a repository declares run beside the application it deploys (checklist A-52, B-67).

``noust.yaml`` (``processes:``) and a ``Procfile`` name a ``worker`` or a ``clock`` next to ``web``.
Pinned here, on real release trees with only the machine faked:

- a deployment records them with the repository as their source and writes their units before the
  release is activated, so the health gate restarts each copy with the web unit, on the release;
- the operator's definition of a name wins, and so does the number of copies the operator chose;
- a release that does not hold takes the declared processes with it, and the previous ones come
  back as they were; one the repository stopped declaring is stopped only once the release holds;
- what cannot be started is said in the log and never fails the deployment: an application in place,
  a name a unit cannot have, a unit that cannot be written, a Procfile that is not read.
"""

# The release-pipeline fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import app_process_store as processes_store
from noust.core.appdir.layout import INPLACE, RELEASES
from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers.nodejs import NodeJSDeployer
from noust.managers.app_processes import ProcessManager
from tests.support.declared_processes import (  # noqa: F401
    BASE,
    BROKEN_WORKER,
    CLOCK,
    GOOD_WORKER,
    WORKER,
    ProcessServices,
    processes_of,
    project,
    world,
)
from tests.support.release_pipeline import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    GIT_URL,
    PORT,
    FakeServices,
    TreeRunner,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
    wire,
    write_tree,
)

NOUST_YAML = "processes:\n  worker: node worker.js\n"


# ---------------------------------------------------------------------------
# A new application
# ---------------------------------------------------------------------------


def test_a_declared_worker_runs_beside_a_new_application_on_its_release(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))

    deploy_new(root, machine)

    assert processes_of(store) == {"worker": ("node worker.js", "repository", 1)}
    assert world.command_of(f"{WORKER}@").endswith("node worker.js")
    assert world.enabled == [f"{WORKER}@1"]
    # Started once, by the health gate, on the release that serves.
    assert world.copy_restarts == [(f"{WORKER}@1", f"releases/{active_id(root)}")]
    assert os.readlink(root / "workers") == os.readlink(root / "current")
    assert world.states[f"{WORKER}@1"] == ("active", "running", 0)


def test_a_procfile_gives_a_new_application_its_web_command_and_its_workers(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    """The Procfile of an application that does not exist yet is read, which it was not."""
    procfile = (
        "web: node server.js\nworker: node worker.js --queue mail\nrelease: node migrate.js\n"
    )
    machine.git.publish(project(tmp_path, "v1", {"Procfile": procfile}, worker=GOOD_WORKER))

    deploy_new(root, machine)

    assert machine.services.units[BASE]["command"].endswith("node server.js")
    assert processes_of(store) == {"worker": ("node worker.js --queue mail", "repository", 1)}
    assert world.copy_restarts and world.copy_restarts[0][0] == f"{WORKER}@1"


def test_an_application_that_declares_no_process_gets_none_and_no_link(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    assert processes_of(store) == {} and world.installed == []
    assert not os.path.lexists(root / "workers")


def test_a_worker_that_does_not_stay_up_on_the_first_release_leaves_nothing_behind(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": NOUST_YAML}, worker=BROKEN_WORKER))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    with pytest.raises(DeploymentError, match="did not pass its health check") as failure:
        deployer.deploy()

    assert "These processes did not stay up" in failure.value.details
    assert store.get_app(DOMAIN) is None
    assert not root.exists()
    assert f"{WORKER}@1" in world.deleted and f"{WORKER}" in world.templates_removed


# ---------------------------------------------------------------------------
# Updating
# ---------------------------------------------------------------------------


def test_an_update_that_changes_the_command_writes_the_unit_before_the_copies_restart(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))
    deploy_new(root, machine)
    first = active_id(root)
    processes_store.put_process(
        DOMAIN,
        processes_store.ProcessRecord(
            name="worker", command="node worker.js", instances=3, source="repository"
        ),
        updated_by="ana",
        store=store,
    )
    world.installed.clear()
    world.copy_restarts.clear()

    machine.git.publish(
        project(
            tmp_path,
            "v2",
            {"noust.yaml": "processes:\n  worker: node worker.js --fast\n"},
            worker=GOOD_WORKER,
        )
    )
    update(machine, monkeypatch)

    assert active_id(root) != first
    assert processes_of(store) == {"worker": ("node worker.js --fast", "repository", 3)}
    assert world.command_of(f"{WORKER}@").endswith("node worker.js --fast")
    new = f"releases/{active_id(root)}"
    assert world.copy_restarts == [(f"{WORKER}@{n}", new) for n in (1, 2, 3)]


def test_an_update_whose_worker_breaks_goes_back_with_the_processes_it_had(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(
        project(
            tmp_path,
            "v1",
            {"noust.yaml": "processes:\n  worker: node worker.js\n  clock: node clock.js\n"},
            worker=GOOD_WORKER,
        )
    )
    deploy_new(root, machine)
    first = active_id(root)

    machine.git.publish(
        project(
            tmp_path,
            "v2",
            {"noust.yaml": "processes:\n  worker: node worker.js --new\n  queue: node q.js\n"},
            worker=BROKEN_WORKER,
        )
    )
    with pytest.raises(RolledBackError, match=f"{first} is active again") as failure:
        update(machine, monkeypatch)

    assert "These processes did not stay up" in failure.value.details
    assert active_id(root) == first
    assert processes_of(store) == {
        "worker": ("node worker.js", "repository", 1),
        "clock": ("node clock.js", "repository", 1),
    }
    assert world.command_of(f"{WORKER}@").endswith("node worker.js"), "its unit as it was"
    assert f"{BASE}-queue@1" in world.deleted and f"{BASE}-queue" in world.templates_removed
    assert f"{CLOCK}@1" not in world.deleted, "dropped only if the release holds"
    assert os.readlink(root / "workers") == os.readlink(root / "current")
    assert world.states[f"{WORKER}@1"] == ("active", "running", 0), "running on the old release"


def test_a_process_the_repository_stops_declaring_is_stopped_once_the_release_holds(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(
        project(
            tmp_path,
            "v1",
            {"noust.yaml": "processes:\n  worker: node worker.js\n  clock: node clock.js\n"},
            worker=GOOD_WORKER,
        )
    )
    deploy_new(root, machine)

    machine.git.publish(project(tmp_path, "v2", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))
    update(machine, monkeypatch)

    assert set(processes_of(store)) == {"worker"}
    assert world.deleted == [f"{CLOCK}@1"] and world.templates_removed == [CLOCK]


def test_an_update_of_a_repository_that_declares_nothing_any_more_removes_its_workers(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))
    deploy_new(root, machine)

    machine.git.publish(node_tree(tmp_path / "v2"))
    update(machine, monkeypatch)

    assert processes_of(store) == {}
    assert not os.path.lexists(root / "workers"), "the link they ran from goes with the last"


def test_the_operators_process_is_left_as_it_is_by_the_repository_that_names_it(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    ProcessManager(
        store=store, services=world, runner=machine.runner, log=Logger(verbose=False)
    ).set_process(
        DOMAIN,
        processes_store.ProcessRecord(name="worker", command="node mine.js", instances=2),
    )
    world.installed.clear()

    machine.git.publish(project(tmp_path, "v2", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))
    update(machine, monkeypatch)

    assert processes_of(store) == {"worker": ("node mine.js", "operator", 2)}
    assert world.installed == []


# ---------------------------------------------------------------------------
# What is not started, and never fails the deployment
# ---------------------------------------------------------------------------


def test_an_application_in_place_says_its_workers_are_not_started_and_deploys(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In place stays exactly as it was; a record without a copy would be a lie."""
    node_tree(root)
    write_tree(root, {"noust.yaml": NOUST_YAML, "worker.js": GOOD_WORKER})
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, str(root), port=PORT, ssl=False, app_path=root, layout=INPLACE)
    messages: list[str] = []
    deployer.logger.warning = messages.append  # type: ignore[method-assign]

    assert deployer._declare_processes() is None

    assert any("worker declared by the repository is not started" in m for m in messages)
    assert any("noust app migrate" in m for m in messages)
    assert processes_of(store) == {} and world.installed == []


def test_a_unit_that_cannot_be_written_is_said_and_the_application_deploys_without_it(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    world.refuse_templates = True
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": NOUST_YAML}, worker=GOOD_WORKER))

    deployer = deploy_new(root, machine)

    assert store.get_app(DOMAIN) is not None and active_id(root)
    assert processes_of(store) == {}, "nothing half recorded"
    assert deployer is not None


def test_a_declared_name_a_unit_cannot_have_is_skipped_and_the_rest_runs(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, world: ProcessServices
) -> None:
    procfile = (
        "web: node server.js\nworker: node worker.js\nproc: node p.js\nqueue_jobs: node q.js\n"
    )
    machine.git.publish(project(tmp_path, "v1", {"Procfile": procfile}, worker=GOOD_WORKER))

    deploy_new(root, machine)

    assert set(processes_of(store)) == {"worker"}


def test_a_procfile_an_existing_application_never_read_declares_no_worker(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deploying it worked without the Procfile: an update must not start running what it says."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)

    machine.git.publish(
        project(tmp_path, "v2", {"Procfile": "worker: node worker.js\n"}, worker=GOOD_WORKER)
    )
    update(machine, monkeypatch)

    assert processes_of(store) == {} and world.installed == []


def test_a_procfile_that_cannot_be_used_leaves_the_workers_a_deployment_declared_alone(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(
        project(
            tmp_path,
            "v1",
            {"Procfile": "web: node server.js\nworker: node worker.js\n"},
            worker=GOOD_WORKER,
        )
    )
    deploy_new(root, machine)

    machine.git.publish(
        project(
            tmp_path, "v2", {"Procfile": "worker: node worker.js && echo hi\n"}, worker=GOOD_WORKER
        )
    )
    update(machine, monkeypatch)

    assert processes_of(store) == {"worker": ("node worker.js", "repository", 1)}


# ---------------------------------------------------------------------------
# Zero downtime
# ---------------------------------------------------------------------------


class Engine:
    """Stands in for the blue/green engine: switches, or fails the way it fails."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.activated: list[Path] = []

    def activate(self, release: Path, releases: Any) -> None:
        self.activated.append(release)
        if self.error is not None:
            raise self.error


class Declaration:
    """Records what the deployment did with the declaration it was given."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")


@pytest.mark.parametrize(
    ("error", "expected"),
    [(None, ["commit"]), (RolledBackError("did not hold"), ["rollback"])],
)
def test_a_blue_green_switch_commits_or_rolls_back_the_declared_processes(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception | None,
    expected: list[str],
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = deploy_new(root, machine)
    declaration = Declaration()
    engine = Engine(error)
    staged = SimpleNamespace(id="r2", path=root / "releases" / "r2", manager=SimpleNamespace())
    monkeypatch.setattr(deployer, "_require_staged", lambda: staged)
    monkeypatch.setattr(deployer, "_zero_downtime_app", lambda: SimpleNamespace())
    monkeypatch.setattr(deployer, "_refuse_blue_green_in_subdirectory", lambda: None)
    monkeypatch.setattr(deployer, "_blue_green", lambda app: engine)
    monkeypatch.setattr(deployer, "_declare_processes", lambda: declaration)
    monkeypatch.setattr(deployer, "_record_release_status", lambda *a, **k: None)
    monkeypatch.setattr(deployer, "_record_release_active", lambda *a, **k: None)
    monkeypatch.setattr(deployer, "_prune_releases", lambda *a, **k: None)

    if error is None:
        deployer._activate_release()
    else:
        with pytest.raises(RolledBackError):
            deployer._activate_release()

    assert engine.activated == [staged.path]
    assert declaration.calls == expected
