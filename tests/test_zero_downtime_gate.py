# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every way a release or a limit reaches an instance passes the same gate.

An application that starts in zero-downtime mode by default is the mode an
operator would have turned on, so each operation that moves traffic (an
update, a rollback, new limits that restart) starts the idle instance, asks
the health gate on its own port and puts nothing out of service when it does
not answer. These tests run each of them on an application converted by the
default, with an instance that does not answer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core import delivery
from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers import bluegreen, lifecycle
from noust.deployers.bluegreen import BlueGreen, adopt_for_new_app
from noust.deployers.releases import ReleaseManager
from noust.managers.service_manager import ResourceLimits
from tests.test_bluegreen import (
    BASE,
    BROKEN,
    DOMAIN,
    FIRST,
    PORT,
    SECOND,
    Machine,
    app,
    machine,
    nobody_can_name_listeners,
    root,
    store,
    write_release,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (app, machine, nobody_can_name_listeners, root, store)


def engine(machine: Machine, store: NoustStore, row: App) -> BlueGreen:
    """
    Build the engine for ``row`` over the fakes.

    Args:
        machine: The fake machine.
        store: The store.
        row: The application's row.

    Returns:
        The engine.
    """
    return BlueGreen(
        row,
        logger=Logger(),
        store=store,
        services=machine,  # type: ignore[arg-type]
        web=machine,  # type: ignore[arg-type]
        probe=machine.probe,
        sleep=machine.sleep,
        refresh_site=machine.refresh_site,
        port_free=lambda port: True,
        listeners=machine.listeners,
        unit_facts=machine.unit_facts,
    )


@pytest.fixture
def converted(
    root: Path, store: NoustStore, app: App, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> Machine:
    """
    An application the default has converted: green serves, blue is idle.

    Returns:
        The fake machine, with what the conversion did forgotten.
    """
    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=True)
    )
    change = adopt_for_new_app(
        DOMAIN,
        logger=Logger(),
        store=store,
        engine=lambda row, _logger: engine(machine, store, row),
    )
    assert change is not None
    machine.events.clear()
    return machine


def serving(store: NoustStore) -> str | None:
    """
    Args:
        store: The store.

    Returns:
        The colour the store says serves.
    """
    row = store.get_app(DOMAIN)
    assert row is not None
    return row.active_color


def test_an_update_that_does_not_answer_is_stopped_and_nothing_else_moves(
    root: Path, store: NoustStore, converted: Machine
) -> None:
    """The old instance keeps the traffic, the upstream and ``current`` are as they were."""
    broken = write_release(root, "20260927-100000-ccccccc", BROKEN)
    upstream = converted.upstream
    before = serving(store)
    row = store.get_app(DOMAIN)
    assert row is not None

    with pytest.raises(RolledBackError):
        engine(converted, store, row).activate(broken, ReleaseManager(root))

    assert serving(store) == before
    assert converted.upstream == upstream
    assert os.readlink(root / "current") == f"releases/{SECOND}"
    assert f"{BASE}@{before}" in converted.running
    other = "blue" if before == "green" else "green"
    assert f"{BASE}@{other}" not in converted.running, "the instance that failed was stopped"


def test_a_rollback_whose_target_does_not_answer_serves_from_the_old_instance(
    root: Path, store: NoustStore, converted: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Going back is the same switch behind the same gate, recorded as a failed activation."""
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: converted)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: converted)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", converted.probe)
    (root / "releases" / FIRST / "server.js").write_text(BROKEN)
    upstream = converted.upstream
    before = serving(store)

    with pytest.raises(RolledBackError):
        lifecycle.activate_release(DOMAIN, FIRST, trigger="panel")

    assert serving(store) == before
    assert converted.upstream == upstream
    assert os.readlink(root / "current") == f"releases/{SECOND}"
    row = store.list_deployments(DOMAIN)[0]
    assert (row.status, row.triggered_by) == ("failed", "panel")


def test_new_limits_that_restart_and_do_not_answer_put_the_old_ones_back(
    root: Path,
    store: NoustStore,
    converted: Machine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The template gets the old limits again; the instance that served never stopped."""
    written: list[tuple[str, Any]] = []
    restored: list[tuple[str, str]] = []
    converted.set_resource_limits = lambda unit, limits: (  # type: ignore[attr-defined]
        written.append((unit, limits)) or "previous body"
    )
    converted.app_units = lambda row: [f"{BASE}@green", f"{BASE}@blue"]  # type: ignore[attr-defined]
    converted.update_config = lambda unit, body: restored.append((unit, body))  # type: ignore[attr-defined]
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: converted)
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: converted)
    # Blue's port never answers; green's does.
    monkeypatch.setattr(
        lifecycle,
        "wait_until_healthy",
        lambda url, **kwargs: f":{PORT}/" not in url,
    )
    monkeypatch.setattr(bluegreen.time, "sleep", converted.sleep)
    before = serving(store)
    upstream = converted.upstream

    with pytest.raises(DeploymentError, match="previous ones are back"):
        lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=256), restart=True)

    assert restored == [(f"{BASE}@green", "previous body")], "the old template is written back"
    row = store.get_app(DOMAIN)
    assert row is not None and row.memory_max_mb is None, "the new limits were never recorded"
    assert serving(store) == before
    assert converted.upstream == upstream
    assert f"{BASE}@{before}" in converted.running
