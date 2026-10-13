# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/apps/{domain}/policy`` and ``/api/policy``.

The rules and the rewrite of the site are pinned in ``tests/test_site_policy.py``.
What this module owns: that the request is translated to the changes the
operation takes (a field left out is not changed), that the caller's own address
reaches the lockout check, the status of each refusal, and that no password hash
is ever in a response.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, validated
from noust.web.api import site_policy as policy_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


@pytest.fixture
def store(tmp_path: Any) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="example.com", app_path="/var/www/apps/example.com"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: Any) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(policy_api.router, prefix="/api/apps")
    app.include_router(policy_api.ranges_router, prefix="/api/policy")
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "token",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    return TestClient(app)


def view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app="example.com",
        policy=validated(SitePolicy(), changes),
        users=("ana",),
        webserver="nginx",
        headers=(("X-Frame-Options", "SAMEORIGIN"),),
    )


class Calls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def stub(self, name: str, result: Any = None, error: Exception | None = None) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if error is not None:
                raise error
            return result

        return fake


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    recorder = Calls()
    for name in ("describe", "set_policy", "reset_policy", "set_user", "remove_user"):
        monkeypatch.setattr(access, name, recorder.stub(name, view()))
    return recorder


def test_reading_returns_the_policy_and_the_account_names_only(
    client: TestClient, calls: Calls
) -> None:
    response = client.get("/api/apps/example.com/policy")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["users"] == ["ana"]
    assert body["policy"]["hsts"] == "default"
    assert body["headers"] == [{"name": "X-Frame-Options", "value": "SAMEORIGIN"}]
    assert "hash" not in response.text


def test_an_unknown_application_is_a_404_everywhere(client: TestClient, calls: Calls) -> None:
    for response in (
        client.get("/api/apps/ghost.example.com/policy"),
        client.put("/api/apps/ghost.example.com/policy", json={}),
        client.delete("/api/apps/ghost.example.com/policy"),
        client.put("/api/apps/ghost.example.com/policy/users/ana", json={"password": "x" * 12}),
        client.delete("/api/apps/ghost.example.com/policy/users/ana"),
    ):
        assert response.status_code == 404, response.text
    assert calls.calls == []


def test_only_the_fields_sent_are_changed_and_the_caller_is_the_operator(
    client: TestClient, calls: Calls
) -> None:
    response = client.put(
        "/api/apps/example.com/policy",
        json={
            "rate_limit": "10r/s",
            "headers": {"X-Frame-Options": "DENY"},
            "hidden_headers": ["Referrer-Policy"],
            "hsts": "on",
            "noindex": False,
            "accept_lockout": True,
        },
        headers={"X-Forwarded-For": "198.51.100.4"},
    )

    assert response.status_code == 200, response.text
    ((name, args, kwargs),) = calls.calls
    assert name == "set_policy"
    assert args[0] == "example.com"
    assert args[1] == {
        "rate_limit": "10r/s",
        "headers": {"X-Frame-Options": "DENY", "Referrer-Policy": None},
        "hsts": True,
        "noindex": False,
    }
    assert kwargs["accept_lockout"] is True
    assert kwargs["operator_addresses"] == ["testclient"]


def test_an_empty_body_changes_nothing(client: TestClient, calls: Calls) -> None:
    client.put("/api/apps/example.com/policy", json={})

    assert calls.calls[0][1][1] == {}


def test_a_value_the_operation_refuses_is_a_400_naming_the_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusal = ValidationError("rate_limit is wrong", field="rate_limit")
    monkeypatch.setattr(access, "set_policy", Calls().stub("set_policy", error=refusal))

    response = client.put("/api/apps/example.com/policy", json={"rate_limit": "fast"})

    assert response.status_code == 400
    assert response.json()["fields"]["rate_limit"]


def test_an_unknown_hsts_choice_is_refused(client: TestClient, calls: Calls) -> None:
    response = client.put("/api/apps/example.com/policy", json={"hsts": "sometimes"})

    assert response.status_code == 400
    assert calls.calls == []


def test_an_account_is_set_with_its_password_and_removed(client: TestClient, calls: Calls) -> None:
    password = "correct horse battery"

    put = client.put("/api/apps/example.com/policy/users/ana", json={"password": password})
    delete = client.delete("/api/apps/example.com/policy/users/ana")

    assert put.status_code == 200 and delete.status_code == 200
    assert calls.calls[0][:2] == ("set_user", ("example.com", "ana", password))
    assert calls.calls[1][:2] == ("remove_user", ("example.com", "ana"))
    assert password not in put.text


def test_the_ranges_are_listed_and_refreshed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    refreshed = {"changed": True, "applied": ["example.com"], "failed": {}, "source": "fetched"}
    monkeypatch.setattr(access, "refresh_trusted_ranges", Calls().stub("refresh", refreshed))

    listing = client.get("/api/policy/cloudflare-ranges")
    refresh = client.post("/api/policy/cloudflare-ranges/refresh")

    assert listing.status_code == 200
    assert "173.245.48.0/20" in listing.json()["networks"]
    assert refresh.json()["changed"] is True
    assert refresh.json()["applied"] == ["example.com"]


def test_a_refresh_that_cannot_reach_cloudflare_is_a_bad_gateway(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = IntegrationError("Could not read the list", details="Check outbound HTTPS.")
    monkeypatch.setattr(access, "refresh_trusted_ranges", Calls().stub("refresh", error=error))

    response = client.post("/api/policy/cloudflare-ranges/refresh")

    assert response.status_code == 502
