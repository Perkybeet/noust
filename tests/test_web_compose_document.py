# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/apps/{domain}/compose/document``: read, judge and keep the Compose document of a stack.

A client of :mod:`noust.deployers.compose_document`. Pinned here: reading asks to reveal secrets (a
document can hold a typed value), judging writes nothing, keeping needs sudo mode, and a stack with
no document says where its file is edited instead of answering an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_compose_document import APP_NAME, DOCUMENT, DOMAIN, NEWER, make_stack

URL = f"/api/apps/{DOMAIN}/compose/document"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    make_stack(instance, tmp_path)
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


def kept(store: NoustStore) -> str:
    return (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).read_text()


def test_the_document_is_read_with_where_it_is(session: TestClient, store: NoustStore) -> None:
    response = session.get(URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["editable"] is True and body["document"] == DOCUMENT
    assert body["path"] == str(recipe_source_dir(APP_NAME, store) / COMPOSE_FILE)


def test_a_stack_with_no_document_says_where_its_file_is_edited(
    session: TestClient, store: NoustStore
) -> None:
    row = store.get_app(DOMAIN)
    assert row is not None
    row.source = "https://github.com/acme/stack.git"
    store.update_app(row)

    body = session.get(URL).json()

    assert body["editable"] is False and body["document"] is None
    assert "https://github.com/acme/stack.git" in body["reason"]


def test_reading_needs_the_permission_that_reveals_secrets(app: FastAPI) -> None:
    token = str(get_token_manager().create_api_token("dashboard", scope="read")["token"])

    response = TestClient(app, client=("testclient", 50000)).get(
        URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


def test_an_unknown_application_is_a_404(session: TestClient) -> None:
    assert session.get("/api/apps/nothing.example.com/compose/document").status_code == 404


def test_checking_judges_and_writes_nothing(session: TestClient, store: NoustStore) -> None:
    response = session.post(f"{URL}/check", json={"document": NEWER})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True and body["deployable"] is True and body["missing"] == []
    assert [service["name"] for service in body["analysis"]["services"]] == ["web"]
    assert kept(store) == DOCUMENT


def test_checking_names_what_refuses_and_what_is_missing(session: TestClient) -> None:
    document = NEWER + "      - API_KEY=${API_KEY:?a key}\n"

    refused = session.post(f"{URL}/check", json={"document": NEWER + "    privileged: true\n"})
    missing = session.post(f"{URL}/check", json={"document": document})

    assert refused.json()["deployable"] is False and refused.json()["analysis"]["refused"]
    assert missing.json()["missing"] == ["API_KEY"] and missing.json()["deployable"] is False


def test_text_that_is_not_a_document_is_a_400(session: TestClient) -> None:
    response = session.post(f"{URL}/check", json={"document": "services: ["})

    assert response.status_code == 400


def test_keeping_needs_sudo_mode(session: TestClient, store: NoustStore) -> None:
    response = session.put(URL, json={"document": NEWER})

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert kept(store) == DOCUMENT


def test_keeping_writes_the_document_and_says_when_it_runs(
    elevated: TestClient, store: NoustStore
) -> None:
    response = elevated.put(URL, json={"document": NEWER})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True and body["services"] == ["web"]
    assert "next update" in body["applies"]
    assert kept(store) == NEWER


def test_keeping_the_same_document_changes_nothing(elevated: TestClient, store: NoustStore) -> None:
    body = elevated.put(URL, json={"document": DOCUMENT}).json()

    assert body["changed"] is False and "already runs" in body["applies"]


def test_a_refused_document_is_a_400_with_the_reasons_and_nothing_is_written(
    elevated: TestClient, store: NoustStore
) -> None:
    response = elevated.put(URL, json={"document": NEWER + "    privileged: true\n"})

    assert response.status_code == 400
    assert kept(store) == DOCUMENT
