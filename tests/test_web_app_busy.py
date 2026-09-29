"""
An application that is busy answers "busy", not "the server broke".

The engine refuses a second operation on an application while one runs, with
:class:`~noust.core.applock.AppBusyError` naming the one running. Through the
API that refusal used to fall to the error boundary's default and answer 500,
which a client reads as a fault to report rather than a wait to sit out.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.core.applock import AppBusyError, app_lock
from noust.core.store import App, NoustStore
from noust.web.api.deps import error_response
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import bearer

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store in the test directory, with one application; the locks live beside it.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", app_path=f"/var/www/apps/{DOMAIN}", port=3000)
    )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def test_a_busy_application_answers_409_naming_what_runs(
    tmp_path: Path, store: NoustStore, runner: object
) -> None:
    """
    Changing limits takes the application's lock in the request itself.

    Args:
        runner: The fake command runner, so nothing reaches systemd.
    """
    app = create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    client = TestClient(app, client=("testclient", 50000))
    master = get_token_manager().generate_master_token()

    with app_lock(DOMAIN, "update"):
        response = client.patch(
            f"/api/apps/{DOMAIN}/limits", json={"memory_max_mb": 256}, headers=bearer(master)
        )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "app_busy"
    assert "update started at" in body["detail"]
    assert body["hint"] == "Wait for it to finish, or follow it in Jobs"
    assert body["fields"] is None


def test_the_contract_is_the_error_boundary_s_own() -> None:
    """Every router shares the one translation, so no endpoint can answer it differently."""
    response = error_response(AppBusyError(DOMAIN, "rollback", None))

    assert response.status_code == 409
    assert b'"error":"app_busy"' in response.body
    assert b"another Noust operation is still running" in response.body
