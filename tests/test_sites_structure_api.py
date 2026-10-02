# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The site API's read side: structure, edits, routes and topology.

The console's Structure and Diagram views are drawn from these endpoints and
edit a draft through them, so three promises are pinned here:

- **Nothing here writes.** A structure, an edit or a route is computed from
  text and returned; the one way to save a site is still ``PUT /config``, with
  its elevation and its test. Every test that calls an endpoint checks the
  configuration tree is byte for byte what it was.
- **A draft is answered as it is.** A draft that does not parse comes back as
  the analyzer's error with its line and column, never as half a structure.
- **Each route reads, and says so.** All five are ``apps.read``.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.managers.site_topology import TopologyProbe
from noust.web.api import sites as sites_api
from noust.web.api.auth import get_current_session
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"
PROGGEST = (FIXTURES / "proggest/proggest.es").read_text()


class FakeStore:
    """A store that knows no application and no site."""

    def list_sites(self) -> list[Any]:
        return []

    def get_site(self, domain: str) -> None:
        return None

    def list_apps(self) -> list[Any]:
        return []

    def list_services(self) -> list[Any]:
        return []

    def get_app(self, domain: str) -> None:
        return None

    def list_domains(self, app_domain: str) -> list[Any]:
        return []

    def domain_owner(self, domain: str) -> None:
        return None


@pytest.fixture
def conf_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Path:
    """An nginx tree holding the Proggest site, with the sites API bound to it."""
    from noust.managers.nginx_manager import NginxManager
    from noust.managers.webserver import NGINX_BACKEND

    root = tmp_path / "etc/nginx"
    for directory in ("sites-available", "sites-enabled", "snippets", "noust-upstreams/shop"):
        (root / directory).mkdir(parents=True)
    (root / "sites-available/proggest.es").write_text(PROGGEST)
    (root / "sites-enabled/proggest.es").symlink_to(root / "sites-available/proggest.es")
    (root / "snippets/headers.conf").write_text("add_header X-Frame-Options DENY;\n")
    backend = dataclasses.replace(
        NGINX_BACKEND,
        sites_available=root / "sites-available",
        sites_enabled=root / "sites-enabled",
        upstreams_dir=root / "noust-upstreams",
    )

    def make(verbose: bool = False, **kwargs: Any) -> NginxManager:
        manager = NginxManager(verbose=verbose, backend=backend)
        manager.__dict__["store"] = FakeStore()
        return manager

    monkeypatch.setattr(sites_api, "MANAGERS", {"nginx": make})
    monkeypatch.setattr(sites_api, "detect_webserver", lambda: "nginx")
    monkeypatch.setattr(sites_api, "get_store", FakeStore)
    return root


@pytest.fixture
def client(conf_root: Path) -> TestClient:
    """An authenticated client of the sites router."""
    app = FastAPI()
    app.include_router(sites_api.router, prefix="/api/sites")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "t", "type": "master"}
    return TestClient(app, raise_server_exceptions=False)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def untouched(conf_root: Path) -> Any:
    """Fail the test if anything under the configuration tree changed."""
    before = _tree(conf_root)
    yield
    assert _tree(conf_root) == before


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_structure_of_the_saved_site(client: TestClient, untouched: None) -> None:
    response = client.get("/api/sites/proggest.es/structure")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["site"] == "proggest.es"
    assert body["webserver"] == "nginx"
    assert body["error"] is None
    model = body["structure"]
    assert len(model["servers"]) == 2
    assert [upstream["name"] for upstream in model["upstreams"]] == [
        "nextjs_upstream",
        "nestjs_upstream",
    ]
    https = model["servers"][1]
    assert https["tls"]["certificate"] == "/etc/letsencrypt/live/proggest.es/fullchain.pem"
    by_path = {location["path"]: location for location in https["locations"]}
    assert by_path["/assets/"]["target"]["kind"] == "static"
    assert by_path["/socket.io"]["settings"]["websocket"] is True
    assert by_path["/api/v1/auth/login"]["settings"]["limit_req"]["zone"] == "auth_limit"


def test_structure_of_a_missing_site_is_404(client: TestClient) -> None:
    assert client.get("/api/sites/nothing.example.com/structure").status_code == 404


def test_structure_of_a_draft_is_the_draft_s(client: TestClient, untouched: None) -> None:
    draft = "server {\n    listen 8080;\n    server_name draft.example.com;\n}\n"

    response = client.post("/api/sites/proggest.es/structure", json={"config": draft})

    assert response.status_code == 200, response.text
    (server,) = response.json()["structure"]["servers"]
    assert server["names"] == ["draft.example.com"]


def test_a_draft_that_does_not_parse_is_the_error_with_its_line(
    client: TestClient, untouched: None
) -> None:
    response = client.post(
        "/api/sites/proggest.es/structure",
        json={"config": "server {\n    listen 80;\n    location / {\n}\n"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["structure"] is None
    assert body["error"]["line"] >= 1
    assert body["error"]["column"] >= 1
    assert body["error"]["message"]


def test_includes_are_resolved_inside_the_configuration_only(
    client: TestClient, untouched: None, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.conf"
    secret.write_text("password;\n")
    draft = f"server {{\n    include snippets/headers.conf;\n    include {secret};\n}}\n"

    response = client.post("/api/sites/proggest.es/structure", json={"config": draft})

    includes = response.json()["structure"]["includes"]
    assert includes[0]["files"][0]["text"] == "add_header X-Frame-Options DENY;\n"
    assert includes[1]["files"] == []
    assert "outside" in includes[1]["error"]


def test_a_domain_that_is_not_one_is_refused(client: TestClient) -> None:
    response = client.post("/api/sites/..%2Fevil/structure", json={"config": "server {}\n"})

    assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------


def test_an_edit_returns_the_new_text_and_writes_nothing(
    client: TestClient, untouched: None
) -> None:
    login = "s1/l0"
    response = client.post(
        "/api/sites/proggest.es/config/edit",
        json={
            "config": PROGGEST,
            "ops": [
                {
                    "op": "set_directive",
                    "parent": login,
                    "name": "proxy_read_timeout",
                    "args": ["120s"],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "proxy_read_timeout 120s;" in body["config"]
    assert body["changed_lines"] >= 1
    assert len(body["structure"]["servers"]) == 2


def test_an_edit_naming_nothing_says_which_operation(client: TestClient, untouched: None) -> None:
    response = client.post(
        "/api/sites/proggest.es/config/edit",
        json={"config": PROGGEST, "ops": [{"op": "remove_block", "target": "s9"}]},
    )

    assert response.status_code == 400
    assert "ops[0].target" in response.json()["fields"]


def test_an_edit_of_a_draft_that_does_not_parse_is_refused(
    client: TestClient, untouched: None
) -> None:
    response = client.post(
        "/api/sites/proggest.es/config/edit",
        json={"config": "server {\n", "ops": []},
    )

    assert response.status_code == 400
    body = response.json()
    assert "config" in body["fields"]
    assert "Line" in body["hint"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_route_of_the_saved_site_explains_the_location(client: TestClient, untouched: None) -> None:
    response = client.post(
        "/api/sites/proggest.es/route",
        json={"host": "proggest.es", "path": "/api/v1/auth/login", "scheme": "https"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "s1"
    model = client.get("/api/sites/proggest.es/structure").json()["structure"]
    location = next(
        loc for loc in model["servers"][1]["locations"] if loc["id"] == body["location_id"]
    )
    assert location["path"] == "/api/v1/auth/login"
    assert body["highlight"][0] == "s1"
    assert body["highlight"][-1] == "u:nestjs_upstream"
    assert all(set(step) == {"code", "params", "text"} for step in body["trace"])
    assert body["steps"] == [step["text"] for step in body["trace"]]
    assert body["redirect"] is None


def test_route_of_a_draft_uses_the_draft(client: TestClient, untouched: None) -> None:
    draft = "server {\n    listen 80;\n    location /only/ { return 302 /x; }\n}\n"

    body = client.post(
        "/api/sites/proggest.es/route",
        json={"config": draft, "host": "a.example.com", "path": "/only/here", "scheme": "http"},
    ).json()

    assert (body["server_id"], body["location_id"]) == ("s0", "s0/l0")


def test_route_refuses_a_scheme_that_is_not_http(client: TestClient) -> None:
    response = client.post(
        "/api/sites/proggest.es/route",
        json={"host": "proggest.es", "path": "/", "scheme": "gopher"},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


class _Certs:
    def list_certificates(self) -> list[Any]:
        from noust.managers.cert_manager import CertificateInfo

        return [
            CertificateInfo(
                name="proggest.es",
                domains=["proggest.es", "www.proggest.es"],
                expiry="2026-12-01",
                cert_path="/etc/letsencrypt/live/proggest.es/fullchain.pem",
            )
        ]


def test_topology_is_the_structure_with_what_is_behind_each_port(
    client: TestClient, untouched: None, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    runner.script(
        ["docker", "ps"],
        stdout='{"Names":"proggest-frontend-1","Ports":"127.0.0.1:3001->3000/tcp",'
        '"Labels":"com.docker.compose.project=proggest,com.docker.compose.service=frontend"}\n',
    )
    probe = TopologyProbe(
        store=FakeStore(),
        certs=_Certs(),
        today=lambda: date(2026, 11, 1),
        connect=lambda port, host, timeout: port == 3001,
    )
    monkeypatch.setattr(sites_api, "topology_probe", lambda: probe)

    response = client.get("/api/sites/proggest.es/topology")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["structure"]["servers"]) == 2
    backends = {backend["address"]: backend for backend in body["backends"]}
    frontend = backends["127.0.0.1:3001"]
    assert frontend["owner"]["kind"] == "compose"
    assert frontend["owner"]["service"] == "frontend"
    assert frontend["reachable"] is True
    assert frontend["upstreams"] == ["nextjs_upstream"]
    assert backends["127.0.0.1:3000"]["reachable"] is False
    assert body["docker"] is True
    (certificate,) = {c["path"]: c for c in body["certificates"]}.values()
    assert certificate["days_left"] == 30


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("GET", "/api/sites/{domain}/structure"),
        ("POST", "/api/sites/{domain}/structure"),
        ("POST", "/api/sites/{domain}/config/edit"),
        ("POST", "/api/sites/{domain}/route"),
        ("GET", "/api/sites/{domain}/topology"),
    ],
)
def test_every_read_route_needs_apps_read(method: str, template: str) -> None:
    assert permission_for_route(method, template) == Permission.APPS_READ


def test_an_edit_with_too_many_operations_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/sites/proggest.es/config/edit",
        json={"config": "", "ops": [{"op": "remove_block", "target": "s0"}] * 201},
    )

    assert response.status_code == 400
    assert "ops" in response.json()["fields"]
