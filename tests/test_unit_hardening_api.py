# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sandbox of an application's unit through the API and the command line (checklist B-58).

``tests/test_unit_hardening.py`` pins what the sandbox is and how it is changed;
here, how it travels: ``GET /api/apps/{domain}/hardening`` shows it, ``PUT``
queues the change as a job behind sudo mode and refuses what a unit cannot
have before queueing, and ``noust app hardening`` is a client of the same
engine.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.deployers import hardening as engine
from noust.web.api import hardening as hardening_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store with one Node application, where the API and the engine look for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(hardening_api, "scoped_store", lambda: ScopedStore(store=instance))
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", port=3100, app_path="/var/www/apps/shop-example-com")
    )
    instance.create_app(
        App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s")
    )
    yield instance
    NoustStore.reset_instance()


def client(*, elevated: bool) -> TestClient:
    """A client for the router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(hardening_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for the job manager, keeping what is queued."""
    jobs: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            jobs.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(hardening_api, "get_job_manager", lambda: Jobs())
    return jobs


def test_the_api_shows_how_hardened_an_application_is(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        engine, "ServiceManager", lambda: SimpleNamespace(serving_units=lambda app: [])
    )

    body = client(elevated=False).get(f"/api/apps/{DOMAIN}/hardening").json()

    assert (body["profile"], body["configured"], body["eligible"]) == ("off", False, True)
    assert body["directives"] == []
    assert client(elevated=False).get("/api/apps/nope.example.com/hardening").status_code == 404


def test_the_api_says_why_a_stack_has_none(store: NoustStore) -> None:
    body = client(elevated=False).get("/api/apps/stack.example.com/hardening").json()

    assert body["eligible"] is False
    assert "Docker" in body["reason"]


def test_a_change_needs_sudo_mode_and_is_a_job(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    body = {"profile": "default", "writable_paths": ["/var/lib/shop"], "restart": True}

    refused = client(elevated=False).put(f"/api/apps/{DOMAIN}/hardening", json=body)
    accepted = client(elevated=True).put(f"/api/apps/{DOMAIN}/hardening", json=body)

    assert refused.status_code == 403
    assert accepted.status_code == 202
    [job] = queued
    assert job["job_type"].value == "hardening"
    assert job["kwargs"] == {
        "domain": DOMAIN,
        "profile": "default",
        "writable_paths": ["/var/lib/shop"],
        "restart": True,
        "actor": job["actor"],
    }
    assert job["func"] is hardening_api.hardening_job


def test_a_stack_is_refused_before_anything_is_queued(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    response = client(elevated=True).put(
        "/api/apps/stack.example.com/hardening", json={"profile": "default"}
    )

    assert response.status_code == 400
    assert queued == []


def test_a_directory_the_system_owns_is_refused_before_anything_is_queued(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    response = client(elevated=True).put(
        f"/api/apps/{DOMAIN}/hardening", json={"profile": "default", "writable_paths": ["/etc"]}
    )

    assert response.status_code == 400
    assert "paths" in response.json()["fields"]
    assert queued == []


def test_the_job_calls_the_engine_with_who_asked(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake(domain: str, profile: str, **kwargs: Any) -> Any:
        calls.append((domain, profile, kwargs))
        return SimpleNamespace(
            domain=domain,
            profile=profile,
            writable_paths=(),
            units=("shop",),
            restarted=True,
            exposure_before=8.1,
            exposure_after=3.2,
        )

    monkeypatch.setattr(engine, "set_hardening", fake)

    result = hardening_api.hardening_job(DOMAIN, "strict", None, True, "alice")

    assert calls[0][:2] == (DOMAIN, "strict")
    assert calls[0][2]["actor"] == "alice" and calls[0][2]["writable_paths"] is None
    assert result["exposure_after"] == 3.2


def test_the_routes_are_the_application_managers() -> None:
    from noust.web.permissions.routes_hardening import ROUTES

    assert ROUTES[("PUT", "/api/apps/{domain}/hardening")] == "apps.manage"
    assert ROUTES[("GET", "/api/apps/{domain}/hardening")] == "apps.read"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_shows_the_status_as_json(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        engine, "ServiceManager", lambda: SimpleNamespace(serving_units=lambda app: [])
    )

    result = CliRunner().invoke(root_cli, ["--json", "app", "hardening", "status", DOMAIN])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile"] == "off"


def test_the_command_sets_the_profile_through_the_engine(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake(domain: str, profile: str, **kwargs: Any) -> Any:
        calls.append((domain, profile, kwargs))
        return SimpleNamespace(
            domain=domain,
            profile=profile,
            writable_paths=tuple(kwargs["writable_paths"] or ()),
            units=("shop",),
            restarted=kwargs["restart"],
            exposure_before=None,
            exposure_after=None,
        )

    monkeypatch.setattr(engine, "set_hardening", fake)

    result = CliRunner().invoke(
        root_cli,
        ["--json", "app", "hardening", "set", DOMAIN, "strict", "--writable", "/var/lib/shop"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][0:2] == (DOMAIN, "strict")
    assert calls[0][2]["writable_paths"] == ["/var/lib/shop"]
    assert calls[0][2]["restart"] is True
    assert json.loads(result.output)["profile"] == "strict"


def test_no_writable_forgets_the_directories(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    def fake(domain: str, profile: str, **kwargs: Any) -> Any:
        seen.append(kwargs["writable_paths"])
        return SimpleNamespace(
            domain=domain,
            profile=profile,
            writable_paths=(),
            units=(),
            restarted=True,
            exposure_before=None,
            exposure_after=None,
        )

    monkeypatch.setattr(engine, "set_hardening", fake)

    CliRunner().invoke(
        root_cli, ["--json", "app", "hardening", "set", DOMAIN, "default", "--no-writable"]
    )

    assert seen == [()]


def test_the_command_refuses_a_profile_that_does_not_exist(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["app", "hardening", "set", DOMAIN, "paranoid"])

    assert result.exit_code == 2


def test_the_command_asks_before_restarting(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(engine, "set_hardening", lambda *a, **k: called.append("x"))

    result = CliRunner().invoke(
        root_cli, ["app", "hardening", "set", DOMAIN, "default"], input="n\n"
    )

    assert result.exit_code != 0
    assert called == []
