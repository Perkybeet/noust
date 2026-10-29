# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API of moving sites to the other web server (backlog item 105).

A client of the same plan and the same move the command line uses: what is checked here is what a
screen depends on, the shape of the plan, the structured refusals that name the way out, the
confirmation asked before anything changes, and that the move is a job.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.core.store import App, get_store
from noust.deployers.webserver_move_plan import Managers
from noust.web.api import webserver_move as api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from tests.support.web_host import TwoServers, two_servers

STATIC = "docs.example.com"
NODE = "api.example.com"


@pytest.fixture
def servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TwoServers]:
    """nginx serving a static site and a proxied one, Apache installed and stopped."""
    two = two_servers(tmp_path)
    store = get_store()
    for domain, app_type, static in ((STATIC, "static", True), (NODE, "node", False)):
        store.create_app(
            App(
                domain=domain,
                app_type=app_type,
                app_path=f"/var/www/apps/{domain.replace('.', '-')}",
                port=None if static else 3000,
                is_static=static,
                webserver="nginx",
            )
        )
    two.nginx.create_site(
        STATIC, "static", {"static_dir": "/var/www/apps/docs-example-com/current"}
    )
    two.nginx.create_site(NODE, "proxy", {"port": 3000})
    for domain in (STATIC, NODE):
        two.nginx.enable_site(domain)
        row, app = store.get_site(domain), store.get_app(domain)
        assert row is not None and app is not None
        row.app_id = app.id
        store.update_site(row)

    def managers(source: str, target: str) -> Managers:
        mine = {"nginx": two.nginx, "apache": two.apache}
        return Managers(mine[source], mine[target])

    monkeypatch.setattr("noust.deployers.webserver_move_plan.managers_between", managers)
    monkeypatch.setattr("noust.deployers.webserver_move.wait_until_healthy", two.probe)
    monkeypatch.setattr("noust.deployers.webserver_move.https_check", two.tls)
    monkeypatch.setattr(
        "noust.deployers.webserver_move_plan.ServerProvisioner",
        lambda **_: SimpleNamespace(
            check_webserver=lambda name: SimpleNamespace(command="apt-get install -y apache2")
        ),
    )
    yield two


def client(*, elevated: bool = True) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/webserver")
    app.include_router(api.app_router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    queued: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            queued.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(api, "get_job_manager", lambda: Jobs())
    return queued


class TestTheMachine:
    def test_status_says_what_is_behind_each_server_and_who_holds_the_ports(
        self, servers: TwoServers
    ) -> None:
        body = client().get("/api/webserver/status").json()

        assert body["webserver"] == "nginx" and body["holds_ports"] == "nginx"
        assert body["servers"]["nginx"] == {
            "installed": True,
            "running": True,
            "starts_at_boot": True,
            "version": None,
            "applications": 2,
            "sites": 0,
        }
        assert body["servers"]["apache"]["running"] is False


class TestThePlan:
    def test_it_has_everything_a_screen_needs(self, servers: TwoServers) -> None:
        response = client().get(
            "/api/webserver/migration/plan", params={"to": "apache", "all": True}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "handover" and body["blocked"] is False
        assert body["confirmation"]["code"] == "webserver_move.confirm_handover"
        assert [site["domain"] for site in body["sites"]] == [NODE, STATIC]
        site = body["sites"][0]
        assert site["movable"] is True and site["text"] is None
        assert {f["code"] for f in site["findings"]} <= {
            "webserver_move.tuning",
            "webserver_move.approximate",
        }

    def test_the_translated_site_comes_with_text_true(self, servers: TwoServers) -> None:
        body = (
            client()
            .get(
                "/api/webserver/migration/plan", params={"to": "apache", "all": True, "text": True}
            )
            .json()
        )

        node = next(site for site in body["sites"] if site["domain"] == NODE)
        assert "ProxyPass / http://127.0.0.1:3000/" in node["text"]

    def test_it_needs_the_sites_or_all(self, servers: TwoServers) -> None:
        response = client().get("/api/webserver/migration/plan", params={"to": "apache"})

        assert response.status_code == 400
        assert (
            client()
            .get(
                "/api/webserver/migration/plan",
                params={"to": "apache", "all": True, "domain": NODE},
            )
            .status_code
            == 400
        )

    def test_one_application_of_a_machine_that_must_hand_over_is_blocked_with_the_way_out(
        self, servers: TwoServers
    ) -> None:
        body = client().get(f"/api/apps/{NODE}/webserver/plan").json()

        assert body["target"] == "apache" and body["blocked"] is True
        (blocker,) = body["blockers"]
        assert blocker["code"] == "webserver_move.partial_handover"
        assert blocker["params"]["command"] == "noust webserver migrate --to apache --all"

    def test_one_application_moves_alone_when_the_other_server_is_serving(
        self, servers: TwoServers
    ) -> None:
        servers.host.active["apache2"] = True

        body = client().get(f"/api/apps/{NODE}/webserver/plan").json()

        assert body["mode"] == "live" and body["blocked"] is False

    def test_an_unknown_application_is_a_404(self, servers: TwoServers) -> None:
        assert client().get("/api/apps/nope.example.com/webserver/plan").status_code == 404


class TestMoving:
    def test_it_needs_sudo_mode(self, servers: TwoServers, jobs: list[dict[str, Any]]) -> None:
        response = client(elevated=False).post(
            "/api/webserver/migrate", json={"to": "apache", "all": True, "confirm": True}
        )

        assert response.status_code == 403 and jobs == []

    def test_without_confirm_it_asks_for_the_confirmation_and_changes_nothing(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        response = client().post("/api/webserver/migrate", json={"to": "apache", "all": True})

        body = response.json()
        assert response.status_code == 400, response.text
        assert body["error"] == "confirmation_required"
        assert body["coded"]["code"] == "webserver_move.confirm_handover"
        assert jobs == [] and servers.host.active["nginx"] is True

    def test_losses_are_listed_and_accepted_by_name(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        path = servers.nginx.config_path(NODE)
        path.write_text(
            path.read_text().replace(
                "server_tokens off;", "server_tokens off;\n    sub_filter a b;", 1
            )
        )
        ask = {"to": "apache", "all": True, "confirm": True}

        refused = client().post("/api/webserver/migrate", json=ask)
        accepted = client().post("/api/webserver/migrate", json={**ask, "accept_losses": True})

        assert refused.status_code == 400
        assert refused.json()["coded"]["code"] == "webserver_move.losses_to_accept"
        assert any("sub_filter" in warning for warning in refused.json()["warnings"])
        assert accepted.status_code == 202 and len(jobs) == 1

    def test_a_blocked_plan_is_a_conflict_that_says_everything(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        response = client().post(
            "/api/webserver/migrate", json={"to": "apache", "domains": [NODE], "confirm": True}
        )

        body = response.json()
        assert response.status_code == 409 and body["error"] == "webserver_move"
        assert body["coded"]["code"] == "webserver_move.partial_handover"
        assert "noust webserver migrate --to apache --all" in body["hint"]
        assert jobs == []

    def test_it_is_queued_as_a_job_that_moves_when_it_runs(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        response = client().post(
            "/api/webserver/migrate", json={"to": "apache", "all": True, "confirm": True}
        )

        assert response.status_code == 202, response.text
        [job] = jobs
        assert job["job_type"].value == "site_action"
        assert job["func"] is api.webserver_move_job
        assert job["kwargs"] == {"target": "apache", "domains": None, "accept_losses": False}
        assert job["actor"] == "alice" or job["actor"]

        result = job["func"](**job["kwargs"])

        assert result["mode"] == "handover" and result["target"] == "apache"
        assert {site["domain"] for site in result["sites"]} == {STATIC, NODE}
        assert servers.host.active == {"nginx": False, "apache2": True}

    def test_one_application_is_a_job_too(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        servers.host.active["apache2"] = True

        response = client().post(f"/api/apps/{NODE}/webserver/migrate", json={"confirm": True})

        assert response.status_code == 202, response.text
        [job] = jobs
        assert job["kwargs"]["domains"] == [NODE] and job["kwargs"]["target"] == "apache"
        job["func"](**job["kwargs"])
        assert get_store().get_app(NODE).webserver == "apache"  # type: ignore[union-attr]
        assert get_store().get_app(STATIC).webserver == "nginx"  # type: ignore[union-attr]

    def test_a_site_that_does_not_answer_ends_the_job_with_everything_put_back(
        self, servers: TwoServers, jobs: list[dict[str, Any]]
    ) -> None:
        from noust.core.exceptions import WebserverMoveError

        servers.unanswered.add(NODE)
        client().post("/api/webserver/migrate", json={"to": "apache", "all": True, "confirm": True})
        [job] = jobs

        with pytest.raises(WebserverMoveError) as failed:
            job["func"](**job["kwargs"])

        assert (
            failed.value.coded is not None
            and failed.value.coded.code == "webserver_move.unanswered"
        )
        assert servers.host.active == {"nginx": True, "apache2": False}
        assert get_store().get_app(NODE).webserver == "nginx"  # type: ignore[union-attr]
