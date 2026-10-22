# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Going back to a release brings back the processes it declares (checklist A-52, B-67).

A deployment records the process types its repository declares and writes their units before
the release is activated. Every other way a release becomes the active one must do the same,
or a worker is left running code the restored release does not have (or not running at all,
when the release is older than the worker):

- ``noust releases rollback`` and the history's rollback (the console and the API),
- ``noust update --commit`` when a release built from that commit is still on disk,
- ``noust rollback`` to a backup of an application on releases.

Pinned here on real release trees with only the machine faked, next to the deployment's own tests
(``tests/test_declared_processes.py``): the declaration is made once the release is the one
``current`` points at, kept when the release holds and undone when it does not.
"""

# The release-pipeline fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import app_process_store as processes_store
from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.helpers.process_declaration import declare_restored_release
from noust.managers.backup_manager import RollbackManager
from tests.test_declared_processes import (  # noqa: F401  (pytest resolves fixtures by name)
    BASE,
    BROKEN_WORKER,
    CLOCK,
    GOOD_WORKER,
    WORKER,
    ProcessServices,
    SystemdRunner,
    Time,
    processes_of,
    project,
    world,
)
from tests.test_release_pipeline import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
)

V1_YAML = "processes:\n  worker: node worker.js\n  clock: node clock.js\n"
V2_YAML = "processes:\n  worker: node worker.js --fast\n  queue: node queue.js\n"


@pytest.fixture
def two_releases(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    """
    Deploy v1 (a worker and a clock), update to v2 (a faster worker and a queue).

    Returns:
        The ids of the first and the second release; the second is active.
    """
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": V1_YAML}, worker=GOOD_WORKER))
    deploy_new(root, machine)
    first = active_id(root)
    machine.git.publish(project(tmp_path, "v2", {"noust.yaml": V2_YAML}, worker=GOOD_WORKER))
    update(machine, monkeypatch)
    second = active_id(root)

    def probe(url: str, **kwargs: Any) -> bool:
        machine.probes.append(url)
        server = root / "current" / "server.js"
        return "throw" not in server.read_text()

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda **kwargs: machine.git)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", probe)
    world.installed.clear()
    world.enabled.clear()
    world.copy_restarts.clear()
    world.deleted.clear()
    world.templates_removed.clear()
    return first, second


def test_the_setup_left_the_second_release_with_its_own_processes(
    store: NoustStore, two_releases: tuple[str, str]
) -> None:
    assert processes_of(store) == {
        "worker": ("node worker.js --fast", "repository", 1),
        "queue": ("node queue.js", "repository", 1),
    }


def test_going_back_brings_back_the_processes_of_the_release_it_restores(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    first, second = two_releases

    lifecycle.activate_release(DOMAIN, first)

    assert active_id(root) == first
    assert processes_of(store) == {
        "worker": ("node worker.js", "repository", 1),
        "clock": ("node clock.js", "repository", 1),
    }
    # The units are written from the restored release's declaration, and every copy is started by
    # the health gate on the release that serves.
    assert world.command_of(f"{WORKER}@").endswith("node worker.js")
    assert world.command_of(f"{CLOCK}@").endswith("node clock.js")
    assert {unit for unit, _ in world.copy_restarts} >= {f"{WORKER}@1", f"{CLOCK}@1"}
    assert {link for _, link in world.copy_restarts} == {f"releases/{first}"}
    # What the restored release does not declare is stopped once it holds.
    assert world.deleted == [f"{BASE}-queue@1"]
    assert world.templates_removed == [f"{BASE}-queue"]
    assert os.readlink(root / "workers") == os.readlink(root / "current")


def test_going_forward_again_brings_the_newer_declaration_back(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    first, second = two_releases
    lifecycle.activate_release(DOMAIN, first)

    lifecycle.activate_release(DOMAIN, second)

    assert processes_of(store) == {
        "worker": ("node worker.js --fast", "repository", 1),
        "queue": ("node queue.js", "repository", 1),
    }


def test_a_release_that_declares_no_process_stops_the_workers_it_goes_back_over(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first = active_id(root)
    machine.git.publish(project(tmp_path, "v2", {"noust.yaml": V1_YAML}, worker=GOOD_WORKER))
    update(machine, monkeypatch)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda **kwargs: machine.git)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", lambda url, **kwargs: True)
    assert set(processes_of(store)) == {"worker", "clock"}

    lifecycle.activate_release(DOMAIN, first)

    assert processes_of(store) == {}
    assert set(world.deleted) == {f"{WORKER}@1", f"{CLOCK}@1"}
    assert not os.path.lexists(root / "workers"), "the link they ran from goes with the last"


def test_a_worker_that_does_not_stay_up_on_the_restored_release_puts_the_processes_back(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    """The release that served keeps its worker, as it was; the one that broke is failed."""
    first, second = two_releases
    # Fine when it was built, not now: a dependency, a secret that was rotated.
    (root / "releases" / first / "worker.js").write_text(BROKEN_WORKER)

    with pytest.raises(DeploymentError, match=f"{second} is active again") as failure:
        lifecycle.activate_release(DOMAIN, first)

    assert "These processes did not stay up" in failure.value.details
    assert active_id(root) == second
    assert processes_of(store) == {
        "worker": ("node worker.js --fast", "repository", 1),
        "queue": ("node queue.js", "repository", 1),
    }
    assert world.command_of(f"{WORKER}@").endswith("node worker.js --fast"), "its unit as it was"
    # The clock the broken release declared is forgotten, the queue it dropped is not stopped.
    assert f"{CLOCK}@1" in world.deleted and CLOCK in world.templates_removed
    assert f"{BASE}-queue@1" not in world.deleted
    assert os.readlink(root / "workers") == os.readlink(root / "current")
    assert world.states[f"{WORKER}@1"] == ("active", "running", 0)


class Engine:
    """Stands in for the blue/green engine: switches, or fails the way it fails."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.activated: list[Path] = []

    def activate(self, release: Path, releases: Any) -> None:
        self.activated.append(release)
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize(
    ("error", "kept"),
    [(None, True), (RolledBackError("did not hold"), False)],
)
def test_a_blue_green_activation_commits_or_undoes_the_declared_processes(
    root: Path,
    store: NoustStore,
    world: ProcessServices,
    two_releases: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception | None,
    kept: bool,
) -> None:
    first, second = two_releases
    engine = Engine(error)
    store.set_zero_downtime(DOMAIN, True)
    monkeypatch.setattr(lifecycle, "BlueGreen", lambda *a, **k: engine)

    if error is None:
        lifecycle.activate_release(DOMAIN, first)
    else:
        with pytest.raises(RolledBackError):
            lifecycle.activate_release(DOMAIN, first)

    assert engine.activated == [root / "releases" / first]
    # The processes of the release the traffic is moving to were written before the engine
    # started it, so it could prove they stay up; they are kept only if it held.
    expected = (
        {"worker": ("node worker.js", "repository", 1), "clock": ("node clock.js", "repository", 1)}
        if kept
        else {
            "worker": ("node worker.js --fast", "repository", 1),
            "queue": ("node queue.js", "repository", 1),
        }
    )
    assert processes_of(store) == expected


def test_an_operators_process_is_left_alone_when_a_release_is_restored(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    first, _second = two_releases
    processes_store.put_process(
        DOMAIN,
        processes_store.ProcessRecord(name="worker", command="node mine.js", instances=2),
        updated_by="ana",
        store=store,
    )

    lifecycle.activate_release(DOMAIN, first)

    assert processes_of(store)["worker"] == ("node mine.js", "operator", 2)


def test_going_back_to_a_deployment_in_the_history_brings_back_its_processes(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    """The console's and the API's rollback: the history row names the release."""
    first, _second = two_releases
    record = next(row for row in store.list_deployments(DOMAIN) if row.release_id == first)
    assert record.id is not None

    lifecycle.rollback_to_deployment(DOMAIN, record.id, schema_changed_ok=True)

    assert active_id(root) == first
    assert set(processes_of(store)) == {"worker", "clock"}


def test_update_to_a_commit_whose_release_is_on_disk_brings_back_its_processes(
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    two_releases: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _second = two_releases
    short = first.split("-")[2]
    full = next(commit for commit in machine.git.commits if commit.startswith(short))
    machine.git.resolve_commit = lambda repository, wanted: full  # type: ignore[attr-defined]

    update(machine, monkeypatch, commit=short, schema_changed_ok=True)

    assert active_id(root) == first
    assert set(processes_of(store)) == {"worker", "clock"}


def test_an_application_in_place_declares_nothing_when_it_goes_back(
    root: Path, store: NoustStore, world: ProcessServices, two_releases: tuple[str, str]
) -> None:
    """Processes run on the releases layout; a record without a copy would be a lie."""
    app = store.get_app(DOMAIN)
    assert app is not None
    before = processes_of(store)

    declared = declare_restored_release(
        replace(app, layout="inplace"),
        root / "releases" / two_releases[0],
        store=store,
        log=Logger(verbose=False),
    )

    assert declared is None
    assert processes_of(store) == before


# ---------------------------------------------------------------------------
# A rollback to a backup of an application on releases
# ---------------------------------------------------------------------------


@pytest.fixture
def backed_up(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    world: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RollbackManager, str, str]:
    """
    Deploy v1 (a worker and a clock), back it up, update to v2 (another worker and a queue).

    Returns:
        The rollback manager over the backup, the backup's id and the first release's id.
    """
    machine.git.publish(project(tmp_path, "v1", {"noust.yaml": V1_YAML}, worker=GOOD_WORKER))
    deploy_new(root, machine)
    first = active_id(root)

    monkeypatch.setattr("noust.managers.backup_manager.get_store", lambda: store)
    manager = RollbackManager(verbose=False, runner=machine.runner)
    manager.backup_manager.backup_dir = tmp_path / "backups"
    metadata = manager.backup_manager.create(DOMAIN)

    machine.git.publish(project(tmp_path, "v2", {"noust.yaml": V2_YAML}, worker=GOOD_WORKER))
    update(machine, monkeypatch)
    manager.service_manager = machine.services
    monkeypatch.setattr(manager, "create_pre_deploy_backup", lambda *a, **k: None)
    world.installed.clear()
    world.copy_restarts.clear()
    world.deleted.clear()
    world.templates_removed.clear()
    return manager, metadata.id, first


def test_a_rollback_to_a_backup_brings_back_the_processes_of_the_release_it_restores(
    root: Path,
    store: NoustStore,
    world: ProcessServices,
    backed_up: tuple[RollbackManager, str, str],
) -> None:
    manager, backup_id, first = backed_up
    assert set(processes_of(store)) == {"worker", "queue"}
    gate_ran_with: list[dict[str, tuple[str, str, int]]] = []

    def gate() -> tuple[bool, str]:
        # The health gate restarts the copies of what the store holds at that moment.
        gate_ran_with.append(processes_of(store))
        return True, ""

    assert manager.rollback(DOMAIN, backup_id=backup_id, gate=gate, schema_changed_ok=True)

    assert active_id(root) == first
    expected = {
        "worker": ("node worker.js", "repository", 1),
        "clock": ("node clock.js", "repository", 1),
    }
    assert gate_ran_with == [expected], "declared before the gate judges the restored release"
    assert processes_of(store) == expected
    assert world.command_of(f"{WORKER}@").endswith("node worker.js")
    assert f"{BASE}-queue@1" in world.deleted and f"{BASE}-queue" in world.templates_removed
    assert os.readlink(root / "workers") == os.readlink(root / "current")


def test_a_rollback_to_a_backup_keeps_the_declaration_when_the_gate_is_not_met(
    root: Path,
    store: NoustStore,
    world: ProcessServices,
    backed_up: tuple[RollbackManager, str, str],
) -> None:
    """The files are what is on disk and nothing puts the previous ones back: so are the workers."""
    manager, backup_id, _first = backed_up

    with pytest.raises(DeploymentError, match="did not pass its health check"):
        manager.rollback(
            DOMAIN,
            backup_id=backup_id,
            gate=lambda: (False, "nothing answered"),
            schema_changed_ok=True,
        )

    assert set(processes_of(store)) == {"worker", "clock"}
