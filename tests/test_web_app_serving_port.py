# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The port an application answers on now, which is not always its recorded port (item 146).

In zero-downtime mode an application runs as two instances, blue on its port and green on the next
one, and only one serves. The console's header said ``3001`` while green served on ``3002``: the
record has one port, the answer says which one is live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def client(tmp_path: Path, store: Any, runner: object) -> TestClient:
    app: FastAPI = build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def deployed(store: Any, domain: str, **fields: Any) -> None:
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nodejs",
        "app_path": f"/srv/{domain}",
        "port": 3001,
        "status": "running",
        "layout": "releases",
    }
    values.update(fields)
    store.create_app(App(**values))


def test_an_application_that_runs_as_one_process_answers_on_its_port(
    client: TestClient, store: Any
) -> None:
    deployed(store, "plain.example.com")

    body = client.get("/api/apps/plain.example.com").json()

    assert (body["port"], body["serving_port"]) == (3001, 3001)


@pytest.mark.parametrize(("color", "live"), [("blue", 3001), ("green", 3002)])
def test_a_zero_downtime_application_answers_on_the_port_of_the_instance_that_serves(
    client: TestClient, store: Any, color: str, live: int
) -> None:
    deployed(store, "zero.example.com", zero_downtime=True, active_color=color)

    body = client.get("/api/apps/zero.example.com").json()

    assert body["port"] == 3001, "the record keeps blue's port"
    assert body["serving_port"] == live


def test_the_list_says_it_too(client: TestClient, store: Any) -> None:
    deployed(store, "zero.example.com", zero_downtime=True, active_color="green")

    [item] = [
        a for a in client.get("/api/apps").json()["apps"] if a["domain"] == "zero.example.com"
    ]

    assert item["serving_port"] == 3002


def test_an_application_without_a_port_has_no_serving_port(client: TestClient, store: Any) -> None:
    deployed(store, "static.example.com", port=None, app_type="static")

    assert client.get("/api/apps/static.example.com").json()["serving_port"] is None
