# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What to give a registry, over the API and the command line (checklist A-14).

The addresses a container registry is pointed at, with Docker Hub's token masked until sudo mode
asks for it; the check of a tag over the API; the same answer on a terminal.
"""

# The forge-hook harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import runner as runner_module
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.registry_events import registry_hook_token
from noust.core.store import App, NoustStore
from noust.integrations import hooks_site, registry_webhook
from noust.web.api.hooks import mint_webhook_secret
from tests.container_support import DIGEST, Docker
from tests.test_web_hooks import (  # noqa: F401  (pytest resolves fixtures by name)
    admin,
    app,
    client,
    fresh_delivery_cache,
    store,
)

DOMAIN = "img.example.com"
IMAGE = "ghcr.io/owner/app:1.4"


@pytest.fixture
def image_app(store: NoustStore) -> App:
    created = store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            source=IMAGE,
            port=3001,
            app_path="/var/www/apps/img-example-com",
        )
    )
    ContainerStore(store).save(
        DOMAIN,
        ContainerSettings(method="image", image=IMAGE, digest="sha256:" + "1" * 64),
        actor="t",
    )
    return created


@pytest.fixture
def secret(store: NoustStore, image_app: App) -> str:
    return mint_webhook_secret(DOMAIN)


class TestOverTheApi:
    def test_the_addresses_are_given_with_the_token_masked(
        self, admin: TestClient, secret: str
    ) -> None:
        response = admin.get(f"/api/apps/{DOMAIN}/webhook/registry")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["supported"] and data["enabled"]
        assert data["ghcr_url"].endswith(f"/hooks/registry/{DOMAIN}")
        assert data["dockerhub_url"].endswith(f"/hooks/registry/{DOMAIN}/<token>")
        assert data["ghcr_events"] == ["registry_package"]
        assert registry_hook_token(secret, DOMAIN) not in response.text

    def test_sudo_mode_shows_the_token_and_the_audit_trail_says_so(
        self, admin: TestClient, secret: str
    ) -> None:
        response = admin.post(f"/api/apps/{DOMAIN}/webhook/registry/reveal")

        assert response.status_code == 200, response.text
        assert response.json()["dockerhub_url"].endswith(registry_hook_token(secret, DOMAIN))

    def test_without_sudo_mode_the_token_is_not_shown(
        self, app: Any, secret: str, client: TestClient
    ) -> None:
        from noust.web.auth import CSRF_HEADER_NAME
        from noust.web.server import get_token_manager

        token = get_token_manager().generate_master_token()
        login = client.post("/api/auth/login", json={"token": token})
        client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]

        response = client.post(f"/api/apps/{DOMAIN}/webhook/registry/reveal")

        assert response.status_code == 403
        assert registry_hook_token(secret, DOMAIN) not in response.text

    def test_an_application_that_is_not_an_image_is_told_so(
        self, admin: TestClient, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="repo.example.com", app_type="nodejs", app_path="/var/www/apps/repo")
        )

        data = admin.get("/api/apps/repo.example.com/webhook/registry").json()

        assert data["supported"] is False and "image" in data["reason"]

    def test_without_a_secret_there_is_no_address_to_give(
        self, admin: TestClient, image_app: App
    ) -> None:
        data = admin.get(f"/api/apps/{DOMAIN}/webhook/registry").json()

        assert data["supported"] and data["enabled"] is False and data["ghcr_url"] is None

    def test_the_check_of_a_tag_is_a_call_of_its_own(
        self, admin: TestClient, image_app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        docker = Docker()
        runner_module.set_runner(docker)
        try:
            response = admin.post(f"/api/apps/{DOMAIN}/container/check")
        finally:
            runner_module.set_runner(None)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["changed"] is True and data["current"] == DIGEST


class TestOverTheCommandLine:
    def run(self, *args: str) -> Any:
        return CliRunner().invoke(root_cli, ["app", "webhook", "registry", *args])

    def test_it_says_there_is_no_secret_yet(self, image_app: App) -> None:
        done = self.run(DOMAIN)

        assert done.exit_code == 0, done.output
        assert "rotate" in done.output

    def test_it_prints_the_addresses_of_an_exposed_hooks_site(
        self, secret: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            hooks_site, "public_hooks_url", lambda: "https://hooks.example.com/hooks"
        )

        masked = self.run(DOMAIN, "--json")
        shown = self.run(DOMAIN, "--reveal", "--json")

        assert json.loads(masked.output)["dockerhub_url"].endswith("/<token>")
        assert json.loads(shown.output)["dockerhub_url"].endswith(
            registry_hook_token(secret, DOMAIN)
        )
        assert json.loads(shown.output)["public"] is True

    def test_an_application_that_is_not_an_image_is_refused(self, store: NoustStore) -> None:
        store.create_app(
            App(domain="repo.example.com", app_type="nodejs", app_path="/var/www/apps/repo")
        )

        done = self.run("repo.example.com")

        assert done.exit_code != 0

    def test_the_description_of_a_digest_pinned_image_says_it_never_changes(
        self, store: NoustStore, secret: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pinned = "ghcr.io/owner/app@" + DIGEST
        ContainerStore(store).save(
            DOMAIN, ContainerSettings(method="image", image=pinned, digest=DIGEST), actor="t"
        )
        monkeypatch.setattr(
            hooks_site, "public_hooks_url", lambda: "https://hooks.example.com/hooks"
        )

        found = registry_webhook.describe(store.get_app(DOMAIN))  # type: ignore[arg-type]

        assert found.pinned is True
