# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the secrets map and the operator marks on an application's environment.

Owner feedback item 27: name and value heuristics both miss and over-flag,
so the operator needs the final word. ``GET /api/apps/{domain}/env`` now
carries a ``secrets`` classification for every variable, and
``PUT /api/apps/{domain}/env/marks`` is how an operator corrects it -
sudo mode, merged with what is already stored, audited. A mark on a
variable a later ``PUT .../env`` removes is dropped with it, so a name
reused later is judged fresh rather than inheriting a stale verdict.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

#: A real-shaped Stripe secret key, to prove value-based detection reaches the API.
STRIPE_SECRET = "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A store of this test's own.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: object) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture.
        runner: The fake command runner, so no manager reaches a real process.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A signed-in client carrying the CSRF header.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient) -> None:
    """
    Confirm sudo mode for the signed-in client.

    Args:
        client: A signed-in client.
    """
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def deployed_env(
    store: NoustStore, tmp_path: Path, domain: str = "example.com", env_text: str = ""
) -> Path:
    """
    Deploy an application whose ``.env`` file lives inside the sandbox.

    Args:
        store: The store to write the application record to.
        tmp_path: Per-test temporary directory.
        domain: The application's domain.
        env_text: Content to write to its ``.env`` file, when not empty.

    Returns:
        The application's directory.
    """
    app_dir = tmp_path / "apps" / domain
    app_dir.mkdir(parents=True, exist_ok=True)
    if env_text:
        (app_dir / ".env").write_text(env_text, encoding="utf-8")
    store.create_app(
        App(
            domain=domain,
            app_type="nextjs",
            source="https://github.com/you/app",
            port=3000,
            app_path=str(app_dir),
            status="running",
        )
    )
    return app_dir


def read_audit(tmp_path: Path) -> list[dict[str, Any]]:
    """
    Read the audit log written during a test.

    Args:
        tmp_path: Per-test temporary directory, where the panel's state lives.

    Returns:
        One dict per audit line, oldest first. Empty when nothing was audited.
    """
    path = tmp_path / "state" / "web-audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# GET .../env: the secrets classification
# ---------------------------------------------------------------------------


def test_get_env_carries_a_classification_for_every_variable(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(
        store,
        tmp_path,
        env_text=f"API_KEY=short\nSTRIPE_SK={STRIPE_SECRET}\nPORT=3000\n",
    )

    response = client.get("/api/apps/example.com/env")

    assert response.status_code == 200, response.text
    secrets = response.json()["secrets"]
    assert secrets["API_KEY"] == {"secret": True, "reason": "name", "marked": False}
    assert secrets["STRIPE_SK"] == {"secret": True, "reason": "value: stripe", "marked": False}
    assert secrets["PORT"] == {"secret": False, "reason": "plain", "marked": False}


def test_get_env_secrets_present_whether_or_not_unmasked(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="API_KEY=short\n")
    elevate(client)

    response = client.get("/api/apps/example.com/env", params={"unmask": "true"})

    assert response.status_code == 200, response.text
    assert response.json()["secrets"]["API_KEY"]["secret"] is True


def test_a_stripe_shaped_value_is_masked_even_behind_a_harmless_name(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    """The finding that motivated value-based detection, through the API."""
    deployed_env(store, tmp_path, env_text=f"STRIPE_SK={STRIPE_SECRET}\n")

    response = client.get("/api/apps/example.com/env")

    body = response.json()
    assert body["variables"]["STRIPE_SK"] == "***"
    assert STRIPE_SECRET not in response.text


# ---------------------------------------------------------------------------
# PUT .../env/marks
# ---------------------------------------------------------------------------


def test_marks_endpoint_needs_elevation(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")

    response = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})

    assert response.status_code == 403, response.text


def test_marking_a_variable_secret_masks_it_even_though_nothing_else_would(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)

    put = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})
    assert put.status_code == 200, put.text
    assert put.json()["secrets"]["APP_NAME"] == {
        "secret": True,
        "reason": "marked secret",
        "marked": True,
    }

    got = client.get("/api/apps/example.com/env")
    assert got.json()["variables"]["APP_NAME"] == "***"


def test_marking_a_variable_not_secret_reveals_it_unmasked(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\nAPI_KEY=short\n")
    elevate(client)

    put = client.put("/api/apps/example.com/env/marks", json={"marks": {"API_KEY": False}})
    assert put.status_code == 200, put.text
    assert put.json()["secrets"]["API_KEY"] == {
        "secret": False,
        "reason": "marked not secret",
        "marked": True,
    }

    got = client.get("/api/apps/example.com/env")
    assert got.json()["variables"]["API_KEY"] == "short"


def test_marks_merge_with_what_is_already_stored(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="ONE=a\nTWO=b\n")
    elevate(client)

    first = client.put("/api/apps/example.com/env/marks", json={"marks": {"ONE": True}})
    assert first.status_code == 200, first.text

    second = client.put("/api/apps/example.com/env/marks", json={"marks": {"TWO": True}})
    assert second.status_code == 200, second.text

    secrets = second.json()["secrets"]
    assert secrets["ONE"]["marked"] is True
    assert secrets["TWO"]["marked"] is True


def test_a_null_mark_removes_the_override(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)

    client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})
    cleared = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": None}})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["secrets"]["APP_NAME"] == {
        "secret": False,
        "reason": "plain",
        "marked": False,
    }


def test_an_invalid_variable_name_is_rejected(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)

    response = client.put("/api/apps/example.com/env/marks", json={"marks": {"BAD NAME": True}})

    assert response.status_code == 400, response.text


def test_marks_endpoint_is_audited_without_the_value(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)

    response = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})
    assert response.status_code == 200, response.text

    entries = [e for e in read_audit(tmp_path) if e["action"] == "apps.env.marks"]
    assert entries, "setting a mark must be audited"
    assert entries[0]["result"] == "success"
    assert "APP_NAME" in entries[0]["detail"]


def test_marks_audit_detail_records_the_direction_of_each_change(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    """Verified finding 4: the audit line must say which way each mark went."""
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\nAPI_KEY=short\n")
    elevate(client)

    response = client.put(
        "/api/apps/example.com/env/marks",
        json={"marks": {"APP_NAME": True, "API_KEY": False}},
    )
    assert response.status_code == 200, response.text

    entries = [e for e in read_audit(tmp_path) if e["action"] == "apps.env.marks"]
    detail = entries[-1]["detail"]
    assert "APP_NAME -> secret" in detail
    assert "API_KEY -> not secret" in detail
    assert "short" not in detail
    assert "storefront" not in detail


def test_marks_audit_detail_records_a_cleared_mark_as_automatic(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)
    client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})

    response = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": None}})
    assert response.status_code == 200, response.text

    entries = [e for e in read_audit(tmp_path) if e["action"] == "apps.env.marks"]
    assert "APP_NAME -> automatic" in entries[-1]["detail"]


def test_an_unknown_application_is_404(client: TestClient, store: NoustStore) -> None:
    elevate(client)

    response = client.put(
        "/api/apps/nowhere.example.com/env/marks", json={"marks": {"APP_NAME": True}}
    )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# A mark on a variable a PUT .../env drops is dropped with it
# ---------------------------------------------------------------------------


def test_a_mark_on_a_removed_variable_is_dropped_by_the_next_env_write(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\nKEEP=1\n")
    elevate(client)

    marked = client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})
    assert marked.status_code == 200, marked.text

    rewritten = client.put("/api/apps/example.com/env", json={"variables": {"KEEP": "1"}})
    assert rewritten.status_code == 200, rewritten.text

    app = store.get_app("example.com")
    assert app is not None
    assert "APP_NAME" not in app.env_secret_marks


def test_a_mark_on_a_variable_that_survives_the_write_is_kept(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="APP_NAME=storefront\n")
    elevate(client)

    client.put("/api/apps/example.com/env/marks", json={"marks": {"APP_NAME": True}})
    rewritten = client.put(
        "/api/apps/example.com/env", json={"variables": {"APP_NAME": "newvalue"}}
    )
    assert rewritten.status_code == 200, rewritten.text

    app = store.get_app("example.com")
    assert app is not None
    assert app.env_secret_marks == {"APP_NAME": True}
