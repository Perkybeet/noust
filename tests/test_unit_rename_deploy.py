# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An update renames the legacy unit of an application, as part of the deployment and nowhere else.

The service manager does the rename (``tests/test_unit_rename.py``); what is pinned here is when
the deployer asks for it: on an update, before the release is activated, and put back when the
release does not pass its health check. A first deployment makes the unit under its current name
and has nothing to rename.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.managers.service_manager import UnitRename
from tests.test_release_pipeline import (  # noqa: F401 - fixtures
    BROKEN_SERVER,
    FakeServices,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
)


class RenamingServices(FakeServices):
    """The fake service manager, with the unit of an application deployed before 0.14.1."""

    def __init__(self, app_root: Path) -> None:
        super().__init__(app_root)
        self.legacy = True
        self.renamed: list[str] = []
        self.restored: list[str] = []
        self.order: list[str] = []

    def rename_legacy_unit(self, name: str) -> UnitRename | None:
        self.order.append("rename")
        if not self.legacy:
            return None
        self.legacy = False
        self.renamed.append(name)
        return UnitRename(old=f"wasm-{name}", new=name, old_text="# old\n", was_enabled=True)

    def restore_legacy_unit(self, renamed: UnitRename) -> None:
        self.order.append("restore")
        self.legacy = True
        self.restored.append(renamed.old)

    def restart(self, name: str) -> None:
        self.order.append("restart")
        super().restart(name)


@pytest.fixture
def legacy(machine: SimpleNamespace, root: Path) -> RenamingServices:  # noqa: F811
    machine.services = RenamingServices(root)
    return machine.services


def test_an_update_renames_the_unit_before_the_release_is_activated(
    tmp_path: Path,
    root: Path,  # noqa: F811
    store: NoustStore,  # noqa: F811
    machine: SimpleNamespace,  # noqa: F811
    legacy: RenamingServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    assert legacy.renamed == [], "a first deployment writes the unit under its current name"
    legacy.order.clear()

    machine.git.publish(node_tree(tmp_path / "v2", server="// v2\n" + "setTimeout(()=>{}, 1e9)\n"))
    update(machine, monkeypatch)

    assert legacy.renamed == ["rel-example-com"]
    assert legacy.order.index("rename") < legacy.order.index("restart"), (
        "the restart that starts the new unit comes after the rename"
    )
    assert legacy.restored == []


def test_the_second_update_has_nothing_left_to_rename(
    tmp_path: Path,
    root: Path,  # noqa: F811
    store: NoustStore,  # noqa: F811
    machine: SimpleNamespace,  # noqa: F811
    legacy: RenamingServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    machine.git.publish(node_tree(tmp_path / "v2", server="// v2\n" + "setTimeout(()=>{}, 1e9)\n"))
    update(machine, monkeypatch)
    machine.git.publish(node_tree(tmp_path / "v3", server="// v3\n" + "setTimeout(()=>{}, 1e9)\n"))

    update(machine, monkeypatch)

    assert legacy.renamed == ["rel-example-com"]


def test_a_release_that_does_not_answer_gets_the_old_unit_back(
    tmp_path: Path,
    root: Path,  # noqa: F811
    store: NoustStore,  # noqa: F811
    machine: SimpleNamespace,  # noqa: F811
    legacy: RenamingServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first = active_id(root)

    machine.git.publish(node_tree(tmp_path / "v2", server=BROKEN_SERVER))
    with pytest.raises(DeploymentError):
        update(machine, monkeypatch)

    assert legacy.renamed == ["rel-example-com"]
    assert legacy.restored == ["wasm-rel-example-com"]
    assert active_id(root) == first
    assert legacy.order[-1] == "restart", "what serves is restarted again, under its old name"


def test_a_service_manager_that_cannot_rename_leaves_the_unit_as_it_is(
    tmp_path: Path,
    root: Path,  # noqa: F811
    store: NoustStore,  # noqa: F811
    machine: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake without the method (and any manager that has none) is simply not asked."""
    assert not hasattr(machine.services, "rename_legacy_unit")
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    machine.git.publish(node_tree(tmp_path / "v2", server="// v2\n"))

    result: Any = update(machine, monkeypatch)

    assert result is not None
