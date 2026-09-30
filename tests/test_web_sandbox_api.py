"""
Tests for ``/api/apps/{domain}/sandbox`` and ``noust app sandbox``.

Both are thin over :mod:`noust.deployers.helpers.sandbox`, whose rules are
pinned in tests/test_build_sandbox.py. What these own: the two surfaces reach
the same state, the dangerous directions need sudo mode (the API) or a
confirmation (the CLI), a trial is a job, and every change reaches the audit
trail whichever surface made it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers import sandbox as build_sandbox
from noust.web.api import sandbox as sandbox_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.permissions.routes_sandbox import ROUTES

DOMAIN = "shop.example.com"


@pytest.fixture
def store() -> Iterator[NoustStore]:
    """The process-wide store, in the test's directory, with one application."""
    NoustStore.reset_instance()
    instance = get_store()
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", app_path="/var/www/apps/shop", layout="releases")
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every audit event the sandbox module records."""
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("noust.core.audit.record", record)
    return events


def make_client(*, elevated: bool) -> TestClient:
    """A client for the sandbox router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(sandbox_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestApi:
    """The routes translate HTTP to the policy module and back."""

    def test_a_legacy_application_reads_as_building_as_root_with_the_way_out(
        self, store: NoustStore
    ) -> None:
        body = make_client(elevated=False).get(f"/api/apps/{DOMAIN}/sandbox").json()

        assert body["mode"] == "legacy"
        assert body["enabled"] is False
        assert body["trial"] is None
        assert f"noust app sandbox test {DOMAIN}" in body["warning"]

    def test_an_unknown_application_is_404(self, store: NoustStore) -> None:
        response = make_client(elevated=True).get("/api/apps/nope.example.com/sandbox")

        assert response.status_code == 404

    def test_enabling_without_a_trial_is_refused_and_forcing_is_audited(
        self, store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        client = make_client(elevated=False)

        refused = client.post(f"/api/apps/{DOMAIN}/sandbox/enable", json={})
        forced = client.post(
            f"/api/apps/{DOMAIN}/sandbox/enable", json={"force": True, "network": "strict"}
        )

        assert refused.status_code == 400
        assert "no passing trial" in json.dumps(refused.json())
        assert forced.status_code == 200
        assert forced.json()["mode"] == "on"
        assert forced.json()["network"] == "strict"
        assert forced.json()["changed_by"]
        [event] = [e for e in audited if e["event"] == "apps.sandbox"]
        assert event["target"] == f"app:{DOMAIN}"
        assert event["details"]["action"] == "enable"
        assert event["details"]["forced"] is True

    def test_building_as_root_needs_sudo_mode_and_a_reason(
        self, store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        plain = make_client(elevated=False).post(
            f"/api/apps/{DOMAIN}/sandbox/disable", json={"reason": "private registry"}
        )
        empty = make_client(elevated=True).post(
            f"/api/apps/{DOMAIN}/sandbox/disable", json={"reason": ""}
        )
        done = make_client(elevated=True).post(
            f"/api/apps/{DOMAIN}/sandbox/disable", json={"reason": "private registry"}
        )

        assert plain.status_code == 403
        assert empty.status_code == 422
        assert done.status_code == 200
        assert done.json()["mode"] == "off"
        assert done.json()["reason"] == "private registry"
        assert "private registry" in done.json()["warning"]
        sandbox_events = [e for e in audited if e["event"] == "apps.sandbox"]
        assert [e["details"]["action"] for e in sandbox_events] == ["disable"]

    def test_a_compose_exception_needs_sudo_mode_and_can_be_revoked(
        self, store: NoustStore
    ) -> None:
        refused = make_client(elevated=False).put(
            f"/api/apps/{DOMAIN}/sandbox/compose-exception", json={"reason": "portainer"}
        )
        allowed = make_client(elevated=True).put(
            f"/api/apps/{DOMAIN}/sandbox/compose-exception", json={"reason": "portainer"}
        )
        revoked = make_client(elevated=False).delete(
            f"/api/apps/{DOMAIN}/sandbox/compose-exception"
        )

        assert refused.status_code == 403
        assert allowed.json()["compose_exception"]["reason"] == "portainer"
        assert revoked.json()["compose_exception"] is None

    def test_a_trial_is_queued_as_a_job(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Jobs:
            def create_job(self, **kwargs: Any) -> Any:
                queued.append(kwargs)
                from types import SimpleNamespace

                return SimpleNamespace(
                    id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
                )

        monkeypatch.setattr(sandbox_api, "get_job_manager", lambda: Jobs())

        response = make_client(elevated=False).post(f"/api/apps/{DOMAIN}/sandbox/test")

        assert response.status_code == 202
        assert response.json()["job_id"] == "j1"
        [job] = queued
        assert job["job_type"].value == "sandbox_test"
        assert job["kwargs"] == {"domain": DOMAIN}
        assert job["func"] is sandbox_api.sandbox_test_job

    def test_every_route_declares_its_permission(self) -> None:
        declared = {
            (method, f"/api/apps{route.path}")
            for route in sandbox_api.router.routes
            for method in getattr(route, "methods", ())
        }

        assert declared == set(ROUTES)


def invoke(*args: str, input: str | None = None) -> Any:
    """Run the CLI."""
    return CliRunner().invoke(root_cli, list(args), input=input)


class TestCli:
    """``noust app sandbox`` reaches the same state as the API."""

    def test_status_as_json(self, store: NoustStore) -> None:
        result = invoke("app", "sandbox", "status", DOMAIN, "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["mode"] == "legacy"
        assert "still builds as root" in body["warning"]

    def test_enable_needs_a_trial_or_force(
        self, store: NoustStore, audited: list[dict[str, Any]]
    ) -> None:
        refused = invoke("app", "sandbox", "enable", DOMAIN)
        forced = invoke("app", "sandbox", "enable", DOMAIN, "--force", "--pty")

        assert refused.exit_code != 0
        # The CLI's error boundary (noust.cli.main) prints it; the group raises it.
        assert "no passing trial" in str(refused.exception)
        assert forced.exit_code == 0, forced.output
        state = build_sandbox.get_state(DOMAIN, store=store)
        assert state.enabled and state.pty
        sandbox_events = [e for e in audited if e["event"] == "apps.sandbox"]
        assert [e["details"]["action"] for e in sandbox_events] == ["enable"]

    def test_disable_asks_and_records_the_reason(self, store: NoustStore) -> None:
        declined = invoke("app", "sandbox", "disable", DOMAIN, "--reason", "npm token", input="n\n")
        confirmed = invoke(
            "app", "sandbox", "disable", DOMAIN, "--reason", "npm token", input="y\n"
        )

        assert declined.exit_code != 0
        assert confirmed.exit_code == 0, confirmed.output
        state = build_sandbox.get_state(DOMAIN, store=store)
        assert (state.mode, state.reason) == ("off", "npm token")

    def test_disable_needs_a_reason(self, store: NoustStore) -> None:
        result = invoke("app", "sandbox", "disable", DOMAIN, "--yes")

        assert result.exit_code != 0
        assert "--reason" in result.output

    def test_a_compose_exception_can_be_given_before_the_stack_exists(
        self, store: NoustStore
    ) -> None:
        result = invoke(
            "app", "sandbox", "compose-exception", "new.example.com", "--reason", "traefik", "--yes"
        )

        assert result.exit_code == 0, result.output
        exception = build_sandbox.get_compose_exception("new.example.com", store=store)
        assert exception is not None and exception.reason == "traefik"

    def test_a_trial_that_fails_exits_non_zero_with_the_builds_words(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import app_sandbox
        from noust.deployers.helpers.sandbox_trial import TrialResult

        monkeypatch.setattr(
            app_sandbox,
            "run_trial",
            lambda domain, **_k: TrialResult(
                domain, False, "a" * 40, "npm ERR! 401", build_sandbox.SandboxState(domain)
            ),
        )

        result = invoke("app", "sandbox", "test", DOMAIN)

        assert result.exit_code == 1
        assert "npm ERR! 401" in result.output
