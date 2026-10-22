# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A new monorepo does not take the port of another application.

A workspace's port was the one its ``package.json`` names or a default by position (3000, 3001),
and ``base_port`` was read from the command line and never used. The first real deployment of a
monorepo beside a Spring Boot application on 3001 produced a unit that could never bind and a
warning that it "may not be running correctly". Found by the integration harness; pinned here.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, MonorepoWorkspace, NoustStore
from noust.deployers import monorepo
from noust.deployers.monorepo import MonorepoDeployer

DOMAIN = "mono.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(monorepo, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def listening(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """The ports something listens on; the test fills it."""
    busy: set[int] = set()
    monkeypatch.setattr(monorepo, "is_port_available", lambda port: port not in busy)
    monkeypatch.setattr(monorepo, "find_available_port", _finder(busy), raising=True)
    return busy


def _finder(busy: set[int]):
    def find(start: int = 3000, end: int = 9000, preferred=None, exclude=()) -> int | None:
        return next((p for p in range(start, end) if p not in busy and p not in exclude), None)

    return find


def deployer(base_port: int = 3100) -> MonorepoDeployer:
    instance = MonorepoDeployer(runner=FakeRunner())
    instance.configure(DOMAIN, "https://example.com/mono.git", port=base_port)
    instance.workspaces = [
        MonorepoWorkspace(name="api", path="apps/api", port=3000),
        MonorepoWorkspace(name="web", path="apps/web", port=3001),
    ]
    return instance


def ports(instance: MonorepoDeployer) -> dict[str, int]:
    return {ws.name: ws.port for ws in instance.workspaces}


def test_free_ports_are_kept_as_they_are(store: NoustStore, listening: set[int]) -> None:
    instance = deployer()

    instance._keep_ports_free()

    assert ports(instance) == {"api": 3000, "web": 3001}


def test_a_port_another_application_owns_is_not_taken(
    store: NoustStore, listening: set[int]
) -> None:
    store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3001))
    instance = deployer()

    instance._keep_ports_free()

    assert ports(instance) == {"api": 3000, "web": 3100}


def test_a_port_something_listens_on_is_not_taken(store: NoustStore, listening: set[int]) -> None:
    listening.update({3000, 3001})
    instance = deployer()

    instance._keep_ports_free()

    assert ports(instance) == {"api": 3100, "web": 3101}


def test_the_second_port_of_a_zero_downtime_application_is_not_taken(
    store: NoustStore, listening: set[int]
) -> None:
    other = store.create_app(App(domain="shop.example.com", app_type="nodejs", port=2999))
    store.set_zero_downtime(other.domain, True)
    instance = deployer()
    instance.workspaces[0].port = 3000

    instance._keep_ports_free()

    assert ports(instance)["api"] == 3100


def test_two_workspaces_do_not_share_a_port(store: NoustStore, listening: set[int]) -> None:
    instance = deployer()
    instance.workspaces[1].port = 3000

    instance._keep_ports_free()

    assert len(set(ports(instance).values())) == 2


def test_a_server_with_no_free_port_says_so(
    store: NoustStore, listening: set[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    listening.add(3000)
    monkeypatch.setattr(monorepo, "find_available_port", lambda **kwargs: None)
    instance = deployer()

    with pytest.raises(DeploymentError, match="no free port for the api workspace"):
        instance._keep_ports_free()
