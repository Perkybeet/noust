# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How what an application takes of the disk travels: the API and the command line (checklist B-63).

``tests/test_app_disk.py`` pins what is measured and what the limit does; here the same is asked
through ``GET``/``PUT /api/apps/{domain}/disk``, ``POST .../disk/measure`` (a job, since nothing
measures a tree in a request) and ``noust app disk``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import app_disk_store
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.web.api import app_disk as disk_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from tests.test_app_disk import (  # noqa: F401 - the fixtures of a tree with releases, uploads and a cache
    DOMAIN,
    MB,
    Disk,
    disk,
    inplace_app,
    releases_app,
    store,
)


@pytest.fixture(autouse=True)
def where_it_looks(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the API read the test's store, as a request in the console does."""
    monkeypatch.setattr(disk_api, "scoped_store", lambda: ScopedStore(store=store))


def client() -> TestClient:
    """A client for the router alone, authenticated."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(disk_api.router, prefix="/api/apps")
    session = {
        "type": "session",
        "sid": "s",
        "scope": "admin",
        "account_id": 1,
        "username": "alice",
    }
    app.dependency_overrides[get_current_session] = lambda: session
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

    monkeypatch.setattr(disk_api, "get_job_manager", lambda: Jobs())
    return jobs


# -- The API ----------------------------------------------------------------------------------------


def test_the_api_shows_the_limit_the_measurement_and_what_the_filesystem_could_hold(
    releases_app: App, disk: Disk
) -> None:
    from noust.managers import app_disk

    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    app_disk.measure_and_record(releases_app)
    disk.calls.clear()

    body = client().get(f"/api/apps/{DOMAIN}/disk").json()

    assert body["soft_mb"] == 128
    assert body["usage"]["total_bytes"] == 400 * MB
    assert [part["name"] for part in body["usage"]["parts"]] == [
        "shared",
        "releases",
        "current",
        "repo",
    ]
    assert body["verdict"]["state"] == "over"
    assert body["filesystem"]["project_quota"] == "available"
    # Reading it measures nothing.
    assert not [call for call in disk.calls if "du" in call]


def test_an_application_never_measured_says_so(inplace_app: App, disk: Disk) -> None:
    body = client().get("/api/apps/legacy.example.com/disk").json()

    assert body["soft_mb"] is None and body["usage"] is None and body["verdict"] is None


def test_an_unknown_application_is_a_404(disk: Disk) -> None:
    assert client().get("/api/apps/nope.example.com/disk").status_code == 404
    assert client().put("/api/apps/nope.example.com/disk", json={"soft_mb": 512}).status_code == 404
    assert client().post("/api/apps/nope.example.com/disk/measure").status_code == 404


def test_the_api_sets_and_removes_the_limit(
    releases_app: App, store: NoustStore, disk: Disk
) -> None:
    response = client().put(f"/api/apps/{DOMAIN}/disk", json={"soft_mb": 512})

    assert response.status_code == 200 and response.json()["soft_mb"] == 512
    record = app_disk_store.get_record(DOMAIN, store=store)
    assert record is not None and record.soft_mb == 512 and record.changed_by == "alice"

    removed = client().put(f"/api/apps/{DOMAIN}/disk", json={"soft_mb": None})
    assert removed.json()["soft_mb"] is None


@pytest.mark.parametrize("value", [10, 0, -1, 2**40])
def test_a_limit_out_of_range_is_refused_with_the_field_named(
    releases_app: App, value: int
) -> None:
    response = client().put(f"/api/apps/{DOMAIN}/disk", json={"soft_mb": value})

    assert response.status_code == 400
    assert response.json()["fields"] == {"soft_mb": response.json()["detail"]}


def test_measuring_is_a_job_and_the_job_keeps_the_answer(
    releases_app: App, disk: Disk, queued: list[dict[str, Any]], store: NoustStore
) -> None:
    response = client().post(f"/api/apps/{DOMAIN}/disk/measure")

    assert response.status_code == 202 and response.json()["job_id"] == "j1"
    assert len(queued) == 1 and queued[0]["metadata"] == {"domain": DOMAIN}
    assert queued[0]["job_type"].value == "disk"
    # Nothing was walked in the request.
    assert not [call for call in disk.calls if "du" in call]

    result = disk_api.measure_job(DOMAIN)

    assert result["total_bytes"] == 400 * MB
    record = app_disk_store.get_record(DOMAIN, store=store)
    assert record is not None and record.usage is not None and record.measured_at


def test_the_routes_declare_a_permission_a_scope_and_do_not_shadow_the_domain() -> None:
    from noust.web.api.openapi import api_routes
    from noust.web.api.router import router
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_disk import ROUTES
    from noust.web.permissions.scopes import RESOURCE, rule_for

    assert ROUTES[("GET", "/api/apps/{domain}/disk")] == Permission.APPS_READ
    assert ROUTES[("POST", "/api/apps/{domain}/disk/measure")] == Permission.APPS_OPERATE
    assert ROUTES[("PUT", "/api/apps/{domain}/disk")] == Permission.APPS_MANAGE
    for method, template in (
        ("GET", "/api/apps/{domain}/disk"),
        ("PUT", "/api/apps/{domain}/disk"),
        ("POST", "/api/apps/{domain}/disk/measure"),
    ):
        rule = rule_for(method, template)
        assert rule is not None and rule.scoping == RESOURCE
    assert "/apps/{domain}/disk" in {str(route.path_format) for route in api_routes(router.routes)}


# -- The command line ---------------------------------------------------------------------------------


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, list(args))


def test_the_command_shows_what_an_application_takes(releases_app: App, disk: Disk) -> None:
    result = invoke("app", "disk", DOMAIN, "--measure", "--limit", "128")

    assert result.exit_code == 0, result.output
    assert "Limit" in result.output and "128 MB" in result.output
    assert "400.0 MB" in result.output
    assert "shared: 140.0 MB" in result.output
    assert "reclaim" not in result.output.lower() or "a prune would give back" in result.output
    assert "over the limit" in result.output
    assert "Kernel quota" in result.output and "ext4" in result.output


def test_the_command_prints_the_disk_as_json(releases_app: App, disk: Disk) -> None:
    invoke("app", "disk", DOMAIN, "--measure")
    result = invoke("--json", "app", "disk", DOMAIN)

    assert result.exit_code == 0, result.output
    body = json.loads(result.output.strip().splitlines()[-1])
    assert body["soft_mb"] is None and body["usage"]["total_bytes"] == 400 * MB


def test_the_command_removes_the_limit(releases_app: App, disk: Disk, store: NoustStore) -> None:
    invoke("app", "disk", DOMAIN, "--limit", "512")

    result = invoke("app", "disk", DOMAIN, "--no-limit")

    assert result.exit_code == 0, result.output
    assert app_disk_store.get_record(DOMAIN, store=store) is None


def test_the_command_does_not_take_a_limit_and_none(releases_app: App, disk: Disk) -> None:
    result = invoke("app", "disk", DOMAIN, "--limit", "512", "--no-limit")

    assert result.exit_code != 0
    assert "not both" in result.output


def test_the_command_refuses_a_limit_below_a_release(releases_app: App, disk: Disk) -> None:
    result = invoke("app", "disk", DOMAIN, "--limit", "10")

    assert result.exit_code != 0


def test_the_command_says_when_the_application_is_unknown(disk: Disk) -> None:
    result = invoke("app", "disk", "nope.example.com")

    assert result.exit_code != 0
    assert "Application not found" in str(result.exception)


def test_the_command_says_it_has_never_measured(releases_app: App, disk: Disk) -> None:
    result = invoke("app", "disk", DOMAIN)

    assert result.exit_code == 0, result.output
    assert "never" in result.output and "--measure" in result.output


# -- The kernel's quota ---------------------------------------------------------------------------------


def elevated_client() -> TestClient:
    """A client in sudo mode: the kernel quota writes a project id on every file of the tree."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(disk_api.router, prefix="/api/apps")
    session = {
        "type": "session",
        "sid": "s",
        "scope": "admin",
        "account_id": 1,
        "username": "alice",
    }
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app)


def test_the_api_refuses_a_kernel_quota_the_filesystem_cannot_hold_before_it_queues_anything(
    releases_app: App, disk: Disk, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.managers import app_disk

    monkeypatch.setattr("noust.deployers.lifecycle.is_in_apps_directory", lambda path: True)
    app_disk.set_disk_limit(releases_app, 128, actor="ana")

    response = elevated_client().put(f"/api/apps/{DOMAIN}/disk/kernel", json={"enabled": True})

    assert response.status_code == 400
    assert "not mounted with project quotas" in response.json()["detail"]
    assert queued == []


def test_the_api_refuses_a_kernel_quota_with_no_limit_to_give(
    releases_app: App, disk: Disk, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.deployers.lifecycle.is_in_apps_directory", lambda path: True)

    response = elevated_client().put(f"/api/apps/{DOMAIN}/disk/kernel", json={"enabled": True})

    assert response.status_code == 400
    assert response.json()["fields"]["soft_mb"]
    assert queued == []


def test_the_api_queues_the_kernel_quota_as_a_job_and_the_job_reports_what_the_kernel_holds(
    releases_app: App, disk: Disk, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.managers import app_disk, kernel_quota

    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    monkeypatch.setattr(app_disk, "check_kernel_possible", lambda app, runner=None: None)
    seen: list[tuple[str, bool, str]] = []
    state = kernel_quota.KernelState(
        applied=True, project=3000000001, mount="/", hard_mb=128, used_bytes=4096
    )

    def fake(domain: str, enabled: bool, *, actor: str, runner: Any = None) -> Any:
        seen.append((domain, enabled, actor))
        return state

    monkeypatch.setattr(app_disk, "kernel_state_after", fake)

    response = elevated_client().put(f"/api/apps/{DOMAIN}/disk/kernel", json={"enabled": True})
    result = disk_api.kernel_job(DOMAIN, True, "alice")

    assert response.status_code == 202 and response.json()["job_id"] == "j1"
    assert queued[0]["kwargs"] == {"domain": DOMAIN, "enabled": True, "actor": "alice"}
    assert queued[0]["job_type"].value == "disk"
    assert seen == [(DOMAIN, True, "alice")]
    assert result["hard_mb"] == 128 and result["applied"] is True


def test_taking_it_off_is_queued_without_asking_whether_the_filesystem_could_hold_one(
    releases_app: App, disk: Disk, queued: list[dict[str, Any]]
) -> None:
    response = elevated_client().put(f"/api/apps/{DOMAIN}/disk/kernel", json={"enabled": False})

    assert response.status_code == 202
    assert queued[0]["kwargs"]["enabled"] is False


def test_the_kernel_route_is_managing_the_application_and_a_resource_of_the_team() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_disk import ROUTES
    from noust.web.permissions.scopes import RESOURCE, rule_for

    assert ROUTES[("PUT", "/api/apps/{domain}/disk/kernel")] == Permission.APPS_MANAGE
    rule = rule_for("PUT", "/api/apps/{domain}/disk/kernel")
    assert rule is not None and rule.scoping == RESOURCE


def test_the_command_refuses_a_kernel_quota_the_filesystem_cannot_hold(
    releases_app: App, disk: Disk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.deployers.lifecycle.is_in_apps_directory", lambda path: True)
    invoke("app", "disk", DOMAIN, "--limit", "128")

    result = invoke("app", "disk", DOMAIN, "--kernel")

    assert result.exit_code != 0
    assert "not mounted with project quotas" in str(result.exception)


def test_the_command_says_a_kernel_quota_is_not_applied_and_why(
    releases_app: App, disk: Disk
) -> None:
    result = invoke("app", "disk", DOMAIN)

    assert result.exit_code == 0, result.output
    assert "not applied; ext4 on /" in result.output
    assert "available but not enabled" in result.output
