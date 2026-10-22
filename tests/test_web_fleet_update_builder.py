# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /api/fleet/placements/{domain}/rollout`` follows the application's build server (checklist F-27).

The decision is tested in ``test_fleet_update_follows_builder.py``; what is pinned here is the
surface: the plan says whether it is a shipment or a rollout, ``build_on_each`` is the old rollout, and
the ship route still takes the placement's own strategy where the request leaves it out.
"""

# The fleet harness of the ship tests is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.builders import Builders
from noust.fleet.placements import Placements
from noust.web.jobs import JobManager
from noust.web.server import create_app
from tests.fleet_views_support import Central
from tests.test_fleet_ship import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    Builder,
    Target,
    builder,
    targets,
)
from tests.test_web_auth import make_config
from tests.test_web_central import sign_in


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    from tests.fleet_views_support import build_central

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", ("build-1", "web-1", "web-2", "web-3"))
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def rollout(client: TestClient, **body):
    return client.post(f"/api/fleet/placements/{DOMAIN}/rollout", json={"plan": True, **body})


def record(central: Central, **fields) -> None:
    Placements(central.store).save(DOMAIN, nodes=["web-1", "web-2"], **fields)
    Builders(central.store).set(DOMAIN, "build-1")


def test_the_rollout_is_planned_as_a_shipment_when_there_is_a_build_server(
    client: TestClient, central: Central, builder: Builder, targets: dict[str, Target]
) -> None:
    sign_in(client)
    record(central, serial=2)

    response = rollout(client)

    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert plan["action"] == "placement_ship"
    assert plan["strategy"]["serial"] == 2
    assert any("build-1 builds" in note for note in plan["notes"])
    assert builder.builds == []


def test_build_on_each_is_the_old_rollout(
    client: TestClient, central: Central, builder: Builder, targets: dict[str, Target]
) -> None:
    sign_in(client)
    record(central)

    plan = rollout(client, build_on_each=True).json()["plan"]

    assert plan["action"] == "placement_rollout"
    assert any("you asked for each server to build it" in note for note in plan["notes"])


def test_a_registry_image_tag_is_rolled_out_with_the_reason(
    client: TestClient, central: Central, builder: Builder, targets: dict[str, Target]
) -> None:
    sign_in(client)
    record(central)

    plan = rollout(client, image_tag="1.4").json()["plan"]

    assert plan["action"] == "placement_rollout"
    assert any("registry" in note for note in plan["notes"])


def test_an_application_without_a_build_server_is_rolled_out(
    client: TestClient, central: Central, targets: dict[str, Target]
) -> None:
    sign_in(client)
    Placements(central.store).save(DOMAIN, nodes=["web-1", "web-2"])

    assert rollout(client).json()["plan"]["action"] == "placement_rollout"


def test_the_ship_route_takes_the_placements_strategy_where_the_request_is_silent(
    client: TestClient, central: Central, builder: Builder, targets: dict[str, Target]
) -> None:
    sign_in(client)
    record(central, serial=2, canary="web-2")

    response = client.post(f"/api/fleet/placements/{DOMAIN}/ship", json={"plan": True})

    assert response.status_code == 200, response.text
    strategy = response.json()["plan"]["strategy"]
    assert (strategy["serial"], strategy["canary"]) == (2, "web-2")
