# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_cloud.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.exceptions import NodeError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.fleet.cloud.accounts import CloudAccounts
from noust.fleet.cloud.servers import CloudServers
from noust.web.api import cloud as api
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import create_app, get_token_manager
from tests.support.cloud_support import FakeApi
from tests.support.web_auth import make_config


class FakeProvisioner:
    """What the routes ask of the provisioner."""

    def __init__(self, servers: CloudServers) -> None:
        self.servers = servers
        self.validated: list[Any] = []
        self.requests: list[Any] = []
        self.refuse: NodeError | None = None
        self.deleted: list[tuple[str, bool]] = []
        self.powered: list[str] = []

    def validate(self, request: Any) -> Any:
        self.validated.append(request)
        if self.refuse is not None:
            raise self.refuse
        return SimpleNamespace(kind="hetzner", display_name="Hetzner Cloud"), []

    def provision(self, request: Any, *, actor: str | None, job_id: str | None, log: Any) -> Any:
        self.requests.append((request, actor, job_id))
        self.servers.record(
            node=request.name,
            account=request.account,
            provider="hetzner",
            provider_id="42",
            location=request.location,
            plan=request.plan,
            image=request.image,
            job_id=job_id,
        )
        log("Hetzner Cloud created 42", "success")
        return SimpleNamespace(to_dict=lambda: {"name": request.name, "status": "reachable"})

    def machine(self, node: str, *, live: bool = False) -> dict[str, Any]:
        data = self.servers.get(node).to_dict()
        if live:
            data["remote"] = {"id": "42", "state": "running"}
        return data

    def power_on(self, node: str, *, actor: str | None = None) -> None:
        self.powered.append(node)

    def delete_machine(
        self, node: str, *, remove_node: bool = False, actor: str | None = None
    ) -> list[str]:
        self.deleted.append((node, remove_node))
        return ["a warning"]


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def provider_api() -> FakeApi:
    fake = FakeApi(prefix="/v1")
    fake.on(
        "GET",
        "/locations",
        (200, {"locations": [{"name": "fsn1", "city": "Falkenstein", "country": "DE"}]}),
    )
    fake.on(
        "GET",
        "/server_types",
        (200, {"server_types": [{"name": "cx22", "cores": 2, "memory": 4, "disk": 40}]}),
    )
    fake.on(
        "GET",
        "/images",
        (200, {"images": [{"name": "ubuntu-24.04", "description": "Ubuntu 24.04"}]}),
    )
    fake.on("GET", "/ssh_keys", (200, {"ssh_keys": []}))
    fake.on("GET", "/firewalls", (200, {"firewalls": []}))
    fake.on("GET", "/networks", (200, {"networks": []}))
    return fake


@pytest.fixture(autouse=True)
def managers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_api: FakeApi
) -> FakeProvisioner:
    secrets = SecretStore(root=tmp_path / "secrets")
    options = {"opener": provider_api, "sleep": lambda _s: None}
    monkeypatch.setattr(
        api, "_accounts", lambda: CloudAccounts(secrets=secrets, provider_options=options)
    )
    provisioner = FakeProvisioner(CloudServers())
    monkeypatch.setattr(api, "_provisioner", lambda: provisioner)
    return provisioner


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


def admin(client: TestClient) -> None:
    elevate(client, sign_in(client))
