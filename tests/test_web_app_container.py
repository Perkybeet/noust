# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Container applications over the API: creating them, and ``/api/apps/{domain}/container``.

``POST /api/apps`` takes the container settings in ``container`` (no parallel
endpoint), writes the registry password and the build secrets to the secret
store before it queues anything and queues only their names; ``GET`` and
``PUT`` on ``/container`` are translations to the one function the CLI calls.
The tests go through the real application, so the permissions and the
audit-by-name are the real ones.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import audit
from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    registry_secret_name,
)
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager

DOMAIN = "shop.example.com"
APP = "shop-example-com"
PASSWORD = "hunter2-registry-password"
SECRET_VALUE = "//registry.npmjs.org/:_authToken=npm_SECRETVALUE"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: object) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)

        class Queued:
            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                return {"id": self.id}

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    return captured


@pytest.fixture
def secrets(store: NoustStore) -> SecretStore:
    """Where the application keeps its secrets: beside the store, as it does."""
    return SecretStore(root=store.db_path.parent / "secrets")


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        audit, "record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def deployed(store: NoustStore, settings: ContainerSettings | None = None) -> None:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            app_path="/var/www/apps/shop",
            source="https://example.com/shop.git",
            port=3001,
        )
    )
    ContainerStore(store).save(DOMAIN, settings or ContainerSettings(), actor="test")


class TestCreating:
    def test_an_image_from_a_registry_needs_no_source(self, client, queued, store):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "container": {"image": "ghcr.io/owner/app:1.4", "container_port": 8080},
            },
        )

        assert response.status_code == 202, response.text
        kwargs = queued[0]["kwargs"]
        assert kwargs["app_type"] == "container"
        assert kwargs["source"] == "ghcr.io/owner/app:1.4"
        assert kwargs["container"]["method"] == "image"
        assert kwargs["container"]["image"] == "ghcr.io/owner/app:1.4"
        assert kwargs["container"]["container_port"] == 8080

    def test_the_registry_password_is_kept_and_only_its_name_is_queued(
        self, client, queued, secrets
    ):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "container": {
                    "image": "ghcr.io/owner/app:1.4",
                    "registry_username": "ci",
                    "registry_password": PASSWORD,
                },
            },
        )

        assert response.status_code == 202, response.text
        assert secrets.read(registry_secret_name(APP)) == PASSWORD
        job = queued[0]
        assert PASSWORD not in str(job)
        assert job["kwargs"]["container"]["registry_secret"] == registry_secret_name(APP)

    def test_a_dockerfile_build_carries_its_options_and_secrets_by_name(
        self, client, queued, secrets
    ):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "source": "https://example.com/shop.git",
                "container": {
                    "dockerfile": "docker/Dockerfile",
                    "context": "services/api",
                    "target": "runtime",
                    "build_args": {"NODE_ENV": "production"},
                    "build_secrets": {"npmrc": SECRET_VALUE},
                    "volumes": ["/data"],
                    "restart_policy": "always",
                },
            },
        )

        assert response.status_code == 202, response.text
        container = queued[0]["kwargs"]["container"]
        assert container["method"] == "dockerfile"
        assert container["dockerfile"] == "docker/Dockerfile"
        assert container["context"] == "services/api"
        assert container["target"] == "runtime"
        assert container["build_args"] == {"NODE_ENV": "production"}
        assert container["build_secrets"] == ("npmrc",)
        assert container["volumes"] == ("/data",)
        assert SECRET_VALUE not in str(queued[0])
        assert secrets.read(build_secret_name(APP, "npmrc")) == SECRET_VALUE

    def test_a_railpack_build_is_a_repository_and_a_builder(self, client, queued):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "source": "https://example.com/shop.git",
                "container": {"method": "railpack"},
            },
        )

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"]["container"]["method"] == "railpack"

    def test_a_plain_container_type_with_a_source_is_a_dockerfile_build(self, client, queued):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "source": "https://example.com/shop.git",
                "app_type": "container",
            },
        )

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"]["container"]["method"] == "dockerfile"

    def test_other_application_types_carry_no_container_option(self, client, queued):
        response = client.post(
            "/api/apps",
            json={"domain": DOMAIN, "source": "https://example.com/shop.git", "app_type": "nextjs"},
        )

        assert response.status_code == 202, response.text
        assert "container" not in queued[0]["kwargs"]

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"container": {"image": "-v /:/host nginx"}}, "image"),
            ({"container": {"image": "nginx"}, "source": "https://example.com/x.git"}, "source"),
            ({"container": {"method": "dockerfile"}}, None),
            ({"container": {"image": "nginx"}, "app_type": "nodejs"}, "container"),
            ({"container": {"image": "nginx", "build_args": {"A": "1"}}}, "build_args"),
            ({"container": {"image": "nginx", "volumes": ["data"]}}, "volumes"),
            (
                {"container": {"image": "ghcr.io/o/a:1", "registry_username": "ci"}},
                "registry_secret",
            ),
        ],
    )
    def test_what_is_wrong_is_refused_before_anything_is_queued_or_kept(
        self, client, queued, secrets, body, field
    ):
        response = client.post("/api/apps", json={"domain": DOMAIN, **body})

        assert response.status_code == 400, response.text
        if field is not None:
            assert field in response.json()["fields"]
        assert queued == []

    def test_a_refusal_keeps_no_secret_behind(self, client, queued, secrets):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "container": {
                    "image": "ghcr.io/o/a:1",
                    "volumes": ["data"],
                    "registry_username": "ci",
                    "registry_password": PASSWORD,
                },
            },
        )

        assert response.status_code == 400
        assert secrets.read(registry_secret_name(APP)) is None

    def test_an_existing_domain_is_refused_and_keeps_no_secret(
        self, client, queued, store, secrets
    ):
        deployed(store)

        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "container": {
                    "image": "ghcr.io/o/a:1",
                    "registry_username": "ci",
                    "registry_password": PASSWORD,
                },
            },
        )

        assert response.status_code == 409
        assert secrets.read(registry_secret_name(APP)) is None

    def test_a_database_cannot_be_created_with_it(self, client, queued):
        response = client.post(
            "/api/apps",
            json={
                "domain": DOMAIN,
                "container": {"image": "nginx"},
                "database": {"engine": "postgresql"},
            },
        )

        assert response.status_code == 400
        assert queued == []

    def test_the_type_is_offered_to_the_wizard(self, client):
        response = client.get("/api/apps/types")

        assert response.status_code == 200
        types = {entry["type"]: entry for entry in response.json()["types"]}
        assert types["container"]["name"] == "Container"


class TestReadingTheSettings:
    def test_it_shows_how_the_application_is_built_and_run(self, client, store):
        deployed(store, ContainerSettings(target="runtime", volumes=("/data",)))

        response = client.get(f"/api/apps/{DOMAIN}/container")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method"] == "dockerfile"
        assert body["target"] == "runtime"
        assert body["volumes"] == ["/data"]
        assert body["container_port"] == 8080
        assert body["registry_password_set"] is False

    def test_the_password_is_never_in_it(self, client, store, secrets):
        deployed(
            store,
            ContainerSettings(
                method="image",
                image="ghcr.io/o/a:1",
                registry_username="ci",
                registry_secret=registry_secret_name(APP),
            ),
        )
        secrets.write(registry_secret_name(APP), PASSWORD)

        response = client.get(f"/api/apps/{DOMAIN}/container")

        assert response.json()["registry_password_set"] is True
        assert PASSWORD not in response.text

    def test_another_type_of_application_has_none(self, client, store):
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x"))

        response = client.get(f"/api/apps/{DOMAIN}/container")

        assert response.status_code == 400
        assert "not a container application" in response.json()["detail"]

    def test_an_unknown_application_is_404(self, client):
        assert client.get(f"/api/apps/{DOMAIN}/container").status_code == 404


class TestChangingTheSettings:
    def test_what_the_request_names_changes_and_the_rest_stays(self, client, store):
        deployed(store, ContainerSettings(target="runtime", build_args={"A": "1"}))

        response = client.put(
            f"/api/apps/{DOMAIN}/container",
            json={"dockerfile": "docker/Dockerfile", "container_port": 9000},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dockerfile"] == "docker/Dockerfile"
        assert body["container_port"] == 9000
        assert body["target"] == "runtime"
        assert body["build_args"] == {"A": "1"}

    def test_clear_goes_back_to_the_default(self, client, store):
        deployed(store, ContainerSettings(target="runtime", container_port=9000))

        response = client.put(
            f"/api/apps/{DOMAIN}/container", json={"clear": ["target", "container_port"]}
        )

        assert response.status_code == 200, response.text
        assert response.json()["target"] is None
        assert response.json()["container_port"] == 8080

    def test_what_cannot_be_cleared_is_said(self, client, store):
        deployed(store)

        response = client.put(f"/api/apps/{DOMAIN}/container", json={"clear": ["image"]})

        assert response.status_code == 400
        assert "clear" in response.json()["fields"]

    def test_a_password_goes_to_the_secret_store_and_is_never_echoed(
        self, client, store, secrets, audited, tmp_path
    ):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        response = client.put(
            f"/api/apps/{DOMAIN}/container",
            json={"registry_username": "ci", "registry_password": PASSWORD},
        )

        assert response.status_code == 200, response.text
        assert response.json()["registry_password_set"] is True
        assert PASSWORD not in response.text
        assert secrets.read(registry_secret_name(APP)) == PASSWORD
        assert PASSWORD not in str(audited)

    def test_a_build_secret_is_given_and_removed_by_its_id(self, client, store, secrets):
        deployed(store)
        client.put(f"/api/apps/{DOMAIN}/container", json={"build_secrets": {"npmrc": SECRET_VALUE}})
        assert secrets.read(build_secret_name(APP, "npmrc")) == SECRET_VALUE

        response = client.put(
            f"/api/apps/{DOMAIN}/container", json={"build_secrets": {"npmrc": None}}
        )

        assert response.status_code == 200, response.text
        assert response.json()["build_secrets"] == []
        assert secrets.read(build_secret_name(APP, "npmrc")) is None

    def test_a_bad_value_is_a_400_naming_the_field_and_changes_nothing(self, client, store):
        deployed(store, ContainerSettings(target="runtime"))

        response = client.put(
            f"/api/apps/{DOMAIN}/container", json={"target": "new", "volumes": ["data"]}
        )

        assert response.status_code == 400
        assert "volumes" in response.json()["fields"]
        assert client.get(f"/api/apps/{DOMAIN}/container").json()["target"] == "runtime"

    def test_the_change_is_audited_by_name(self, client, store, audited):
        deployed(store)

        client.put(f"/api/apps/{DOMAIN}/container", json={"restart_policy": "always"})

        events = [e for e in audited if e["event"] == "apps.container"]
        assert events and events[0]["details"]["changed"] == ["restart_policy"]
        assert events[0]["target"] == f"app:{DOMAIN}"


class StackDeployer:
    """What ``stack_deployer`` hands the route: the one reader of a stack's containers."""

    def __init__(self, services: list[str] | None = None) -> None:
        self.services = ["app", "db"] if services is None else services
        self.asked: list[tuple[str | None, int]] = []

    def service_names(self) -> list[str]:
        return self.services

    def logs(self, service: str | None = None, lines: int = 50) -> str:
        self.asked.append((service, lines))
        return f"{service or 'app'}-1  | listening on 8080"


class TestTheLogs:
    @pytest.fixture
    def stack(self, monkeypatch: pytest.MonkeyPatch) -> StackDeployer:
        deployer = StackDeployer()
        monkeypatch.setattr(
            "noust.deployers.docker_compose.stack_deployer", lambda app, **kwargs: deployer
        )
        return deployer

    def test_it_is_what_the_container_printed_not_the_units_journal(self, client, store, stack):
        deployed(store)

        response = client.get(f"/api/apps/{DOMAIN}/logs", params={"lines": 20})

        assert response.status_code == 200, response.text
        assert response.json()["logs"] == "app-1  | listening on 8080"
        assert stack.asked == [(None, 20)]

    def test_it_names_the_services_so_one_of_them_can_be_chosen(self, client, store, stack):
        deployed(store)

        body = client.get(f"/api/apps/{DOMAIN}/logs").json()

        assert body["services"] == ["app", "db"]
        assert body["service"] is None

    def test_one_service_of_the_stack_is_read_alone(self, client, store, stack):
        deployed(store)

        response = client.get(f"/api/apps/{DOMAIN}/logs", params={"service": "db", "lines": 30})

        assert response.status_code == 200, response.text
        assert response.json()["logs"] == "db-1  | listening on 8080"
        assert response.json()["service"] == "db"
        assert stack.asked == [("db", 30)]

    def test_a_compose_stack_reads_its_containers_as_a_container_application_does(
        self, client, store, stack
    ):
        store.create_app(
            App(domain=DOMAIN, app_type="docker-compose", app_path="/var/www/apps/shop", port=3001)
        )

        response = client.get(f"/api/apps/{DOMAIN}/logs")

        assert response.status_code == 200, response.text
        assert response.json()["services"] == ["app", "db"]
        assert stack.asked == [(None, 100)]

    def test_a_service_the_stack_does_not_have_is_a_404_that_lists_the_ones_it_has(
        self, client, store, stack
    ):
        deployed(store)

        response = client.get(f"/api/apps/{DOMAIN}/logs", params={"service": "cache"})

        assert response.status_code == 404
        assert "app" in response.text and "db" in response.text
        assert stack.asked == []

    def test_a_name_compose_would_not_accept_is_refused_before_anything_runs(
        self, client, store, stack
    ):
        deployed(store)

        response = client.get(f"/api/apps/{DOMAIN}/logs", params={"service": "db; reboot"})

        assert response.status_code == 400
        assert "service" in response.json()["fields"]
        assert stack.asked == []

    def test_a_stack_compose_cannot_list_is_still_read_whole(self, client, store, monkeypatch):
        deployed(store)
        deployer = StackDeployer(services=[])
        monkeypatch.setattr(
            "noust.deployers.docker_compose.stack_deployer", lambda app, **kwargs: deployer
        )

        response = client.get(f"/api/apps/{DOMAIN}/logs")

        assert response.status_code == 200, response.text
        assert response.json()["services"] == []
        assert deployer.asked == [(None, 100)]

    def test_a_native_application_has_the_units_journal_and_no_services(
        self, client, store, monkeypatch
    ):
        store.create_app(
            App(domain=DOMAIN, app_type="nextjs", app_path="/var/www/apps/shop", port=3001)
        )
        monkeypatch.setattr(
            "noust.managers.service_manager.ServiceManager.logs",
            lambda self, name, lines=100: "journal line",
        )

        body = client.get(f"/api/apps/{DOMAIN}/logs").json()

        assert body["logs"] == "journal line"
        assert body["services"] == []
        assert body["service"] is None


class TestPermissions:
    def test_reading_is_apps_read_and_changing_is_apps_manage(self):
        assert permission_for_route("GET", "/api/apps/{domain}/container") == "apps.read"
        assert permission_for_route("PUT", "/api/apps/{domain}/container") == "apps.manage"
