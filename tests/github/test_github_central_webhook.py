# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The GitHub integration of a central says, while it is set up, that GitHub cannot reach it.

A server a central manages has no address GitHub could call, so the App's webhook is the
central's. The App can be created and installed without a public address (the manifest flow goes
through the operator's browser), so nothing fails at that moment; the status and the manifest
say it then. A central that manages nothing, and a server that is not a hub, say nothing
about it: for them a missing webhook is what it always was.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.store import NodeRecord, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

BASE = "/api/integrations/github"


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    """What one test sets in the configuration is not what the next one starts from."""
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    elevated = signed_in.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert elevated.status_code == 200, elevated.text
    return signed_in


def manage_a_server(store: NoustStore) -> None:
    store.save_node(
        NodeRecord(
            name="web-1", ssh_host="web-1", ssh_port=22, ssh_user="u", host_key="k", console_port=1
        )
    )


def codes(body: dict) -> list[str]:  # type: ignore[type-arg]
    return [w["coded"]["code"] for w in body["warnings"]]


class TestTheStatus:
    def test_a_central_with_servers_and_no_address_for_github_says_so(
        self, client: TestClient, store: NoustStore
    ) -> None:
        manage_a_server(store)

        body = client.get(BASE).json()

        assert codes(body) == ["sources.webhook_unreachable"]
        (warning,) = body["warnings"]
        assert "cannot reach this central" in warning["text"]
        assert "noust web expose-hooks" in warning["hint"]
        assert "web.hooks_url" in warning["hint"]
        assert warning["coded"]["params"]["command"].startswith("noust web expose-hooks ")

    def test_a_central_that_has_an_address_says_nothing(
        self, client: TestClient, store: NoustStore
    ) -> None:
        manage_a_server(store)
        Config().set("web.hooks_url", "https://hooks.example.com/hooks")

        assert client.get(BASE).json()["warnings"] == []

    def test_a_hub_says_it_before_it_has_a_single_server(
        self, client: TestClient, store: NoustStore
    ) -> None:
        Config().set("central.role", "hub")

        assert codes(client.get(BASE).json()) == ["sources.webhook_unreachable"]

    def test_a_server_with_no_servers_says_nothing(self, client: TestClient) -> None:
        assert client.get(BASE).json()["warnings"] == []

    def test_the_commands_it_suggests_are_commands(self) -> None:
        from noust.cli.app import main

        assert main(["web", "expose-hooks", "--help"]) == 0
        assert main(["config", "set", "--help"]) == 0


class TestCreatingTheApp:
    def test_the_manifest_that_starts_it_carries_the_same_warning(
        self, client: TestClient, store: NoustStore
    ) -> None:
        manage_a_server(store)

        response = client.post(f"{BASE}/manifest", json={"origin": "https://noust.example.com"})

        assert response.status_code == 200, response.text
        assert codes(response.json()) == ["sources.webhook_unreachable"]
        assert response.json()["manifest"]["name"]

    def test_with_an_address_the_manifest_points_the_webhook_at_it_and_says_nothing(
        self, client: TestClient, store: NoustStore
    ) -> None:
        manage_a_server(store)
        Config().set("web.hooks_url", "https://hooks.example.com/hooks")

        response = client.post(f"{BASE}/manifest", json={"origin": "https://noust.example.com"})

        assert response.json()["warnings"] == []
        assert response.json()["manifest"]["hook_attributes"]["url"] == (
            "https://hooks.example.com/hooks/github"
        )
