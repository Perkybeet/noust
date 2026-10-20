# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``GET /api/apps/{domain}/export`` and ``POST /api/apps/import``.

The API is a client of :mod:`noust.deployers.app_export`: pinned here are
the scopes (admin to export, sudo mode for secrets and for an import), the
audit record of a secret export, the checks an import runs before anything
is queued, and that the job deploys through the same deploy job as ``POST
/api/apps``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.deployers import app_export
from noust.deployers.helpers.layout import env_file_for
from noust.web.api import app_export as api_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.jobs import JobType
from noust.web.server import create_app, get_token_manager

DOMAIN = "shop.example.com"
STRIPE = "sk_live_" + "51Habcdefghijklmn" + "opqrstuvwxyz"


class NoCron:
    def list_jobs(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    app = instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
        )
    )
    env = env_file_for(app)
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(f"APP_NAME=Shop\nSTRIPE_KEY={STRIPE}\n")
    monkeypatch.setattr(app_export, "CronManager", NoCron)
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def master_token(app: FastAPI) -> str:
    return get_token_manager().generate_master_token()


@pytest.fixture
def session(app: FastAPI, master_token: str) -> TestClient:
    client = TestClient(app, client=("testclient", 50000))
    response = client.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def elevated(session: TestClient, master_token: str) -> TestClient:
    response = session.post("/api/auth/elevate", json={"token": master_token})
    assert response.status_code == 200, response.text
    return session


@pytest.fixture
def read_token(app: FastAPI) -> str:
    return str(get_token_manager().create_api_token("dashboard", scope="read")["token"])


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            captured.append(kwargs)
            return FakeJob(f"job-{len(captured)}")

    monkeypatch.setattr(api_module, "get_job_manager", Manager)
    return captured


def test_export_answers_the_document_without_secrets(session: TestClient) -> None:
    response = session.get(f"/api/apps/{DOMAIN}/export")

    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["format"] == "noust-app"
    assert doc["app"]["domain"] == DOMAIN
    assert doc["env"]["STRIPE_KEY"] == {"secret": True, "value": None}
    assert STRIPE not in response.text
    assert list(doc)[:3] == ["format", "version", "exported_at"]


def test_export_needs_admin_scope(app: FastAPI, read_token: str) -> None:
    client = TestClient(app, client=("testclient", 50000))
    response = client.get(
        f"/api/apps/{DOMAIN}/export", headers={"Authorization": f"Bearer {read_token}"}
    )
    assert response.status_code == 403


def test_export_with_secrets_needs_sudo_mode(session: TestClient) -> None:
    response = session.get(f"/api/apps/{DOMAIN}/export", params={"with_secrets": "true"})
    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"


def test_export_with_secrets_is_audited(elevated: TestClient) -> None:
    response = elevated.get(f"/api/apps/{DOMAIN}/export", params={"with_secrets": "true"})

    assert response.status_code == 200, response.text
    assert response.json()["env"]["STRIPE_KEY"]["value"] == STRIPE
    audit = elevated.get("/api/audit", params={"action": "apps.export.secrets"})
    assert audit.status_code == 200, audit.text
    assert STRIPE not in audit.text
    assert "apps.export.secrets" in audit.text


def test_export_of_an_unknown_application(session: TestClient) -> None:
    assert session.get("/api/apps/nothing.example.com/export").status_code == 404


def exported(session: TestClient) -> dict[str, Any]:
    response = session.get(f"/api/apps/{DOMAIN}/export")
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_import_needs_sudo_mode(session: TestClient, queued: list[dict[str, Any]]) -> None:
    response = session.post("/api/apps/import", json={"document": exported(session)})
    assert response.status_code == 403
    assert queued == []


def test_import_refuses_a_taken_domain(elevated: TestClient, queued: list[dict[str, Any]]) -> None:
    body = {"document": exported(elevated), "env": {"STRIPE_KEY": STRIPE}}
    response = elevated.post("/api/apps/import", json=body)
    assert response.status_code == 409
    assert queued == []


def test_import_names_the_missing_secrets(
    elevated: TestClient, queued: list[dict[str, Any]]
) -> None:
    response = elevated.post(
        "/api/apps/import", json={"document": exported(elevated), "domain": "store.example.org"}
    )
    assert response.status_code == 400
    assert "STRIPE_KEY" in response.json()["detail"]
    assert queued == []


def test_import_refuses_what_is_not_an_export(
    elevated: TestClient, queued: list[dict[str, Any]]
) -> None:
    document = {**exported(elevated), "format": "something-else"}
    response = elevated.post(
        "/api/apps/import", json={"document": document, "domain": "store.example.org"}
    )
    assert response.status_code == 400
    assert queued == []


def test_import_queues_a_deploy_job_with_a_free_port(
    elevated: TestClient, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = elevated.post(
        "/api/apps/import",
        json={
            "document": exported(elevated),
            "domain": "store.example.org",
            "env": {"STRIPE_KEY": STRIPE},
        },
    )

    assert response.status_code == 202, response.text
    [job] = queued
    assert job["job_type"] == JobType.DEPLOY
    assert job["func"] is api_module.import_app_job
    plan = job["kwargs"]["plan"]
    assert plan.domain == "store.example.org"
    assert plan.create.port is not None and plan.create.port != 3000
    assert STRIPE not in response.text

    deployed: list[dict[str, Any]] = []
    monkeypatch.setattr("noust.web.jobs.deploy_app_job", lambda **kwargs: deployed.append(kwargs))
    result = api_module.import_app_job(plan)
    assert deployed[0]["domain"] == "store.example.org"
    assert deployed[0]["env_vars"] == {"APP_NAME": "Shop", "STRIPE_KEY": STRIPE}
    assert result["domain"] == "store.example.org"


def test_a_platform_proposal_translates_to_its_api_model() -> None:
    """What the inspection response carries as platform_proposal."""
    from noust.deployers.importers import Proposal, ProposedEnv
    from noust.web.api.platform_proposal import platform_proposal_response
    from noust.web.pydantic_compat import dump_model

    assert platform_proposal_response(None) is None
    proposal = Proposal(
        platform="render",
        files=["render.yaml"],
        health_path="/healthz",
        env=[ProposedEnv(name="SESSION_SECRET", secret=True, generated=True)],
        warnings=["something"],
    )
    model = platform_proposal_response(proposal)
    assert model is not None
    data = dump_model(model)
    assert data["platform"] == "render" and data["health_path"] == "/healthz"
    assert data["env"][0]["generated"] is True
    assert data["warnings"] == ["something"]


def mounted_file(store: NoustStore, text: str) -> None:
    """Keep a file for the application, as the mounts API would."""
    from noust.managers.app_mounts import MountManager
    from tests.test_app_mounts import OwnerFs

    row = store.get_app(DOMAIN)
    assert row is not None
    row.identity = "noust-app-shop"
    store.update_app(row)
    MountManager(store=store, fs=OwnerFs()).put_file(
        DOMAIN, "config/production.json", text, mode=0o640
    )


def test_the_export_keeps_what_the_document_has_beyond_the_old_model(
    elevated: TestClient, store: NoustStore
) -> None:
    """Delivery, source options, the build block and the mounts used to be dropped here."""
    from noust.core import app_build_store, app_rollout_store, app_source_store

    mounted_file(store, "token=abc\n")
    app_build_store.set_document(DOMAIN, "build:\n  start: node server.js\n", updated_by="t")
    app_rollout_store.set_history_keep(DOMAIN, 5, actor="t")
    app_source_store.set_options(DOMAIN, submodules=True, actor="t")

    response = elevated.get(f"/api/apps/{DOMAIN}/export", params={"with_secrets": "true"})

    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["mounts"][0]["target"] == "config/production.json"
    assert doc["mounts"][0]["content"] == "token=abc\n" and doc["mounts"][0]["mode"] == "640"
    assert doc["app"]["build"] == "build:\n  start: node server.js\n"
    assert doc["delivery"]["history_keep"] == 5
    assert doc["source_options"]["submodules"] is True


def test_a_mounted_files_text_needs_sudo_mode_like_any_secret(
    session: TestClient, store: NoustStore
) -> None:
    mounted_file(store, "token=abc\n")

    response = session.get(f"/api/apps/{DOMAIN}/export")

    assert response.status_code == 200
    (mount,) = response.json()["mounts"]
    assert mount["content"] is None and mount["secret"] is True
    assert "token=abc" not in response.text


def test_importing_what_was_exported_brings_the_mounts_and_the_rest_to_the_job(
    elevated: TestClient, store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    from noust.core import app_build_store

    mounted_file(store, "token=abc\n")
    app_build_store.set_document(DOMAIN, "build:\n  start: node server.js\n", updated_by="t")
    document = elevated.get(f"/api/apps/{DOMAIN}/export", params={"with_secrets": "true"}).json()

    response = elevated.post(
        "/api/apps/import", json={"document": document, "domain": "store.example.org"}
    )

    assert response.status_code == 202, response.text
    plan = queued[0]["kwargs"]["plan"]
    assert plan.create.seed_files == {"config/production.json": "token=abc\n"}
    assert [m["target"] for m in plan.mounts] == ["config/production.json"]
    assert plan.create.build_document == "build:\n  start: node server.js\n"
    assert "token=abc" not in response.text
