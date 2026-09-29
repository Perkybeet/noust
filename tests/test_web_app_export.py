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
    assert doc["format"] == "wasm-app"
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
    monkeypatch.setattr(api_module, "deploy_app_job", lambda **kwargs: deployed.append(kwargs))
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
