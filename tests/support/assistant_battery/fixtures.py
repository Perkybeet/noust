# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fixtures every module of the battery of the built-in assistant asks for (item 171).

A test module imports them by name (``from tests.support.assistant_battery.fixtures import ...``), which
is all pytest needs to find them: the battery's fixtures are not in ``conftest.py`` because nothing else
of the suite wants a console with a scripted model in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.runner import set_runner
from tests.support.assistant_battery.canaries import CANARIES
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_fleet_node import FleetNode
from tests.support.assistant_lab import Lab, build_lab
from tests.support.node_proxy import FakeManager, FakeTunnels, ServedNode
from tests.support.scripted_model import ScriptedModel

__all__ = ["battery_environment", "fleet_harness", "fleet_node", "harness", "lab", "model"]


@pytest.fixture(autouse=True)
def battery_environment(monkeypatch: pytest.MonkeyPatch, real_config: Any) -> Iterator[None]:
    """Cheap password hashes (the battery signs people in) and a configuration of its own."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    yield
    set_runner(None)


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    """The scripted provider, which refuses a request without the key it was given."""
    with ScriptedModel(models=["tiny"], api_key=CANARIES["provider_key"].value) as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    """The console, served, with two applications and an administrator."""
    yield from build_lab(sandbox, monkeypatch, model)


@pytest.fixture
def harness(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The harness over the lab."""
    return Harness(lab, monkeypatch)


@pytest.fixture
def fleet_node() -> Iterator[tuple[FleetNode, ServedNode]]:
    """A server of the fleet, listening on a port of this machine."""
    node = FleetNode()
    with ServedNode(node) as served:  # type: ignore[arg-type]
        yield node, served


@pytest.fixture
def fleet_harness(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: ScriptedModel,
    fleet_node: tuple[FleetNode, ServedNode],
) -> Iterator[Harness]:
    """The harness over a console that has one server in its fleet, ``web-2``."""
    from noust.web.api import node_proxy
    from noust.web.api import nodes as nodes_api

    manager = FakeManager(FakeTunnels(fleet_node[1].port))
    monkeypatch.setattr(nodes_api, "node_manager", lambda: manager)
    monkeypatch.setattr(nodes_api, "tunnels", lambda: manager.tunnels)
    node_proxy.node_schemas.forget()
    for fixed in build_lab(sandbox, monkeypatch, model):
        yield Harness(fixed, monkeypatch, node=fleet_node[0])
