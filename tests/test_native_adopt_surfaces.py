# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How adopting a site travels: the API, the command line and the rules that guard both (checklist B-85).

``tests/test_native_adopt.py`` pins what adopting does on a server; here the same adoption is
asked for through ``GET /api/apps/native-sites``, ``POST /api/apps/adopt-site`` and
``noust app discover`` / ``adopt-site``, and the routes are held to the rules every route has: a
permission, a scope, and sudo mode for what registers a unit Noust may restart.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.native_adoption_store import get_record
from noust.core.store import App, NoustStore
from noust.deployers import native_adopt
from noust.managers.site_topology import TopologyProbe
from noust.web.api import app_native_adopt
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from tests.test_native_adopt import (  # noqa: F401 - the fixtures of a server with three sites
    UNIT,
    Machine,
    Server,
    machine,
    probe,
    server,
    store,
    tree,
    world,
)


@pytest.fixture(autouse=True)
def looks_here(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the adoption look in the test server's sites, not in /etc/nginx."""
    web: Server = request.getfixturevalue("world")
    probing: TopologyProbe = request.getfixturevalue("probe")
    monkeypatch.setattr(native_adopt, "_managers", lambda: [web.nginx, web.apache])
    monkeypatch.setattr(native_adopt, "default_probe", lambda: probing)


def client(*, elevated: bool = True) -> TestClient:
    """A client for the router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_native_adopt.router, prefix="/api/apps")
    session = {
        "type": "session",
        "sid": "s",
        "scope": "admin",
        "account_id": 1,
        "username": "alice",
    }
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


# -- The API ---------------------------------------------------------------------------------


def test_the_api_lists_the_sites_that_could_be_adopted(world: Server) -> None:
    body = client(elevated=False).get("/api/apps/native-sites").json()

    found = {site["site"]: site for site in body["sites"]}
    assert found["legacy.example.com"]["kind"] == "proxy"
    assert found["legacy.example.com"]["unit"] == UNIT
    assert found["blog.example.com"]["kind"] == "php"
    assert found["docs.example.com"]["adoptable"] is True


def test_a_preview_says_what_would_be_recorded_and_records_nothing(
    world: Server,
    store: NoustStore,
) -> None:
    response = client().post(
        "/api/apps/adopt-site", json={"domain": "legacy.example.com", "preview": True}
    )

    body = response.json()
    assert response.status_code == 200, body
    assert body["adopted"] is False and body["kind"] == "proxy" and body["unit"] == UNIT
    assert body["root"] == str(world.legacy) and body["port"] == 8000
    assert store.get_app("legacy.example.com") is None


def test_the_api_adopts_the_site_and_the_unit_is_the_operators(
    world: Server,
    store: NoustStore,
    machine: Machine,
) -> None:
    before = (tree(world.units), tree(world.nginx.sites_available), tree(world.legacy))

    response = client().post("/api/apps/adopt-site", json={"domain": "legacy.example.com"})

    assert response.status_code == 200, response.json()
    assert response.json()["adopted"] is True
    record = get_record("legacy.example.com", store=store)
    assert record is not None and record.adopted_by == "alice"
    assert (tree(world.units), tree(world.nginx.sites_available), tree(world.legacy)) == before
    assert machine.changing() == []


def test_the_api_says_what_an_adopted_application_is(
    world: Server, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.teams import ScopedStore

    monkeypatch.setattr(app_native_adopt, "scoped_store", lambda: ScopedStore(store=store))
    client().post("/api/apps/adopt-site", json={"domain": "legacy.example.com"})
    store.create_app(App(domain="built.example.com", app_type="nodejs", app_path="/var/www/apps/b"))

    body = client(elevated=False).get("/api/apps/legacy.example.com/adoption").json()

    assert (body["kind"], body["unit"], body["site_file"], body["webserver"]) == (
        "proxy",
        UNIT,
        "legacy.example.com",
        "nginx",
    )
    assert body["root"] == str(world.legacy) and body["adopted_by"] == "alice"
    # One Noust built is not an adoption, and one that does not exist is the same answer.
    assert client(elevated=False).get("/api/apps/built.example.com/adoption").status_code == 404
    assert client(elevated=False).get("/api/apps/nope.example.com/adoption").status_code == 404


def test_adopting_asks_for_sudo_mode(world: Server, store: NoustStore) -> None:
    response = client(elevated=False).post(
        "/api/apps/adopt-site", json={"domain": "legacy.example.com"}
    )

    assert response.status_code == 403
    assert store.get_app("legacy.example.com") is None


def test_a_refusal_comes_back_as_the_error_contract(world: Server) -> None:
    response = client().post("/api/apps/adopt-site", json={"domain": "missing.example.com"})

    assert response.status_code == 400
    assert "No site in nginx or Apache" in response.json()["detail"]
    assert "noust app discover" in json.dumps(response.json())


def test_the_routes_are_declared_with_a_permission_and_a_scope() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_native_adopt import ROUTES
    from noust.web.permissions.scopes import INSTANCE, rule_for

    assert ROUTES[("GET", "/api/apps/native-sites")] == Permission.APPS_MANAGE
    assert ROUTES[("POST", "/api/apps/adopt-site")] == Permission.APPS_MANAGE
    for method, template in (("GET", "/api/apps/native-sites"), ("POST", "/api/apps/adopt-site")):
        rule = rule_for(method, template)
        assert rule is not None and rule.scoping == INSTANCE


def test_native_sites_is_not_read_as_a_domain() -> None:
    from noust.web.api.openapi import api_routes
    from noust.web.api.router import router

    templates = [str(route.path_format) for route in api_routes(router.routes)]

    assert templates.index("/apps/native-sites") < templates.index("/apps/{domain}")


# -- The command line -----------------------------------------------------------------------------


def test_the_command_lists_what_could_be_adopted(world: Server) -> None:
    result = CliRunner().invoke(root_cli, ["app", "discover"])

    assert result.exit_code == 0, result.output
    assert "legacy.example.com" in result.output and UNIT in result.output
    assert "php" in result.output and "static" in result.output


def test_the_command_prints_the_sites_as_json(world: Server) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "discover"])

    assert result.exit_code == 0, result.output
    sites = json.loads(result.output.strip().splitlines()[-1])["sites"]
    assert {site["site"] for site in sites} >= {"legacy.example.com", "blog.example.com"}


def test_the_command_shows_the_plan_and_adopts_when_confirmed(
    world: Server,
    store: NoustStore,
) -> None:
    result = CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com"], input="y\n")

    assert result.exit_code == 0, result.output
    assert UNIT in result.output and "never rewritten" in result.output
    assert store.get_app("legacy.example.com") is not None


def test_the_command_adopts_nothing_when_not_confirmed(
    world: Server,
    store: NoustStore,
) -> None:
    result = CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com"], input="n\n")

    assert result.exit_code != 0
    assert store.get_app("legacy.example.com") is None


def test_the_command_takes_a_unit_a_path_and_a_site(
    world: Server,
    store: NoustStore,
    machine: Machine,
) -> None:
    machine.listeners.clear()

    result = CliRunner().invoke(
        root_cli,
        [
            "--json",
            "app",
            "adopt-site",
            "legacy.example.com",
            "--unit",
            UNIT,
            "--path",
            str(world.legacy),
            "--site",
            "legacy.example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output.strip().splitlines()[-1])
    assert (body["adopted"], body["unit"], body["root"]) == (True, UNIT, str(world.legacy))


def test_a_rehearsal_adopts_nothing(
    world: Server,
    store: NoustStore,
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --dry-run wraps the real runner and lets declared read-only probes through; the test's
    # machine answers them.
    monkeypatch.setattr("noust.cli.app.SubprocessRunner", lambda: machine)

    result = CliRunner().invoke(root_cli, ["--dry-run", "app", "adopt-site", "legacy.example.com"])

    assert result.exit_code == 0, result.output
    assert "Rehearsal: nothing was adopted" in result.output
    assert store.get_app("legacy.example.com") is None


def test_a_refusal_is_said_in_plain_words(world: Server) -> None:
    result = CliRunner().invoke(root_cli, ["app", "adopt-site", "missing.example.com", "--yes"])

    assert result.exit_code != 0
    assert "No site in nginx or Apache" in str(result.exception)


# -- The verbs the rest of the console already has -----------------------------------------------


def test_the_application_verbs_reach_the_adopted_unit(
    world: Server, store: NoustStore, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.teams import ScopedStore
    from noust.web.api import apps as apps_api

    monkeypatch.setattr(apps_api, "scoped_store", lambda: ScopedStore(store=store))
    client().post("/api/apps/adopt-site", json={"domain": "legacy.example.com"})

    restarted = apps_api.restart_app("legacy.example.com", {})
    stopped = apps_api.stop_app("legacy.example.com", {})
    logs = apps_api.get_app_logs("legacy.example.com", {}, lines=20, service=None)

    assert restarted.success and stopped.success
    assert ("systemctl", "restart", f"{UNIT}.service") in machine.calls
    assert ("systemctl", "stop", f"{UNIT}.service") in machine.calls
    assert "listening on 8000" in logs.logs


def test_the_application_is_listed_with_its_unit_state(
    world: Server, store: NoustStore, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.teams import ScopedStore
    from noust.web.api import apps as apps_api

    monkeypatch.setattr(apps_api, "scoped_store", lambda: ScopedStore(store=store))
    client().post("/api/apps/adopt-site", json={"domain": "legacy.example.com"})
    machine.active[UNIT] = "failed"

    info = apps_api.get_app("legacy.example.com", {})

    assert info.app_type == "adopted"
    assert info.status == "failed", info.status


# -- Deleting from the command line ---------------------------------------------------------------


def test_deleting_from_the_command_line_says_it_only_forgets_and_removes_nothing(
    world: Server, store: NoustStore, machine: Machine
) -> None:
    CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com", "--yes"])
    before = (tree(world.units), tree(world.nginx.sites_available), tree(world.legacy))

    asked = CliRunner().invoke(root_cli, ["delete", "legacy.example.com"], input="n\n")
    result = CliRunner().invoke(root_cli, ["delete", "legacy.example.com", "--force"])

    assert "This forgets legacy.example.com" in asked.output
    assert "Nothing is removed" in asked.output and "the service" not in asked.output
    assert result.exit_code == 0, result.output
    assert "legacy.example.com is no longer managed" in result.output
    assert f"{UNIT}.service keeps running" in result.output
    assert store.get_app("legacy.example.com") is None
    assert (tree(world.units), tree(world.nginx.sites_available), tree(world.legacy)) == before
    assert machine.changing() == []


def test_a_rehearsed_deletion_of_an_adopted_site_forgets_nothing(
    world: Server, store: NoustStore
) -> None:
    CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com", "--yes"])

    result = CliRunner().invoke(root_cli, ["--dry-run", "delete", "legacy.example.com"])

    assert result.exit_code == 0, result.output
    assert "Would forget legacy.example.com" in result.output
    assert store.get_app("legacy.example.com") is not None


def test_the_directory_of_an_adopted_site_is_never_named_for_removal(
    world: Server, store: NoustStore
) -> None:
    CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com", "--yes"])

    result = CliRunner().invoke(
        root_cli,
        [
            "delete",
            "legacy.example.com",
            "--force",
            "--remove-adopted-directory",
            str(world.legacy),
        ],
    )

    assert result.exit_code != 0
    assert "never removes its files" in str(result.exception)
    assert store.get_app("legacy.example.com") is not None


def test_a_sandbox_or_a_tuning_the_unit_cannot_have_is_refused_before_it_is_asked_about(
    world: Server, store: NoustStore
) -> None:
    CliRunner().invoke(root_cli, ["app", "adopt-site", "legacy.example.com", "--yes"])

    hardening = CliRunner().invoke(
        root_cli, ["app", "hardening", "set", "legacy.example.com", "default"]
    )
    tuning = CliRunner().invoke(
        root_cli, ["app", "tuning", "set", "legacy.example.com", "--cpu-weight", "50"]
    )

    for result in (hardening, tuning):
        assert result.exit_code != 0
        assert "Restart" not in result.output  # no question was put
        assert "its unit is yours" in result.output + str(result.exception)
