# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``/api/recipes``: the catalogue, its logos, installs, and what is behind."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.recipes import get_recipe
from noust.recipes.installed import record_install
from noust.web.api import recipes as recipes_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.permissions.routes_recipes import ROUTES

DOMAIN = "site.example.com"


def small_app() -> FastAPI:
    """The recipes router alone, behind a session the test supplies."""
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(recipes_api.router, prefix="/api/recipes")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    api.dependency_overrides[get_current_session] = lambda: session
    return api


@pytest.fixture
def client(recipe_store: NoustStore) -> TestClient:
    """A client for the router alone, signed in, over this test's store."""
    return TestClient(small_app())


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the jobs instead of running them."""
    captured: list[dict[str, Any]] = []

    class Queued:
        """A job that was accepted but never run."""

        id = "job-1"
        status = type("Status", (), {"value": "pending"})()

        def to_dict(self) -> dict[str, Any]:
            return {"id": self.id}

    def create_job(**kwargs: Any) -> Queued:
        captured.append(kwargs)
        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    monkeypatch.setattr("noust.web.api.recipes.get_job_manager", lambda: manager)
    return captured


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_the_list_carries_what_a_catalogue_card_shows(client: TestClient) -> None:
    """Title, slogan, category, tags, version, kind, links and a logo URL."""
    response = client.get("/api/recipes")

    assert response.status_code == 200, response.text
    body = response.json()
    items = {item["name"]: item for item in body["items"]}
    assert body["total"] == len(items) >= 22
    vaultwarden = items["vaultwarden"]
    assert vaultwarden["title"] == "Vaultwarden"
    assert vaultwarden["slogan"] and vaultwarden["category"] == "security"
    assert "passwords" in vaultwarden["tags"]
    assert vaultwarden["version"] == get_recipe("vaultwarden").version
    assert vaultwarden["updated"] == get_recipe("vaultwarden").updated.isoformat()
    assert (vaultwarden["kind"], vaultwarden["runs_in_docker"]) == ("compose", True)
    assert vaultwarden["docs"].startswith("https://") and vaultwarden["repository"]
    assert vaultwarden["logo"] == "/api/recipes/vaultwarden/logo"
    assert vaultwarden["digest_verified"] is True and vaultwarden["schema_version"] == 2
    assert items["wordpress"]["kind"] == "native" and items["wordpress"]["runs_in_docker"] is False
    assert items["wordpress"]["database"] == "mysql" and items["wordpress"]["dependencies"] == [
        "mysql"
    ]
    assert items["wordpress"]["requires"][0].startswith("PHP-FPM")
    assert items["ghost"]["available"] is False and items["ghost"]["unavailable_reason"]


def test_available_recipes_come_first(client: TestClient) -> None:
    """What this release can deploy before what it cannot."""
    flags = [item["available"] for item in client.get("/api/recipes").json()["items"]]

    assert flags == sorted(flags, reverse=True)


def test_search_and_filters_narrow_the_list(client: TestClient) -> None:
    """q, category, tag and kind, alone and together."""

    def names(**params: str) -> set[str]:
        response = client.get("/api/recipes", params=params)
        assert response.status_code == 200, response.text
        return {item["name"] for item in response.json()["items"]}

    assert names(q="password manager") == {"vaultwarden"}
    assert names(q="DASHBOARD") >= {"grafana", "homepage"}
    assert names(category="media") >= {"jellyfin", "navidrome"}
    assert names(category="media", tag="subsonic") == {"navidrome"}
    assert names(kind="native") >= {"wordpress", "n8n", "umami", "uptime-kuma"}
    assert not names(kind="native") & {"vaultwarden", "memos"}
    assert names(q="zzzz") == set()
    assert client.get("/api/recipes", params={"q": "memos"}).json()["total"] == 1


@pytest.mark.parametrize("params", [{"category": "Networking"}, {"kind": "docker"}])
def test_a_filter_outside_the_closed_lists_is_a_400_that_names_the_values(
    client: TestClient, params: dict[str, str]
) -> None:
    """Not an empty list that hides the typo."""
    response = client.get("/api/recipes", params=params)

    assert response.status_code == 400, response.text
    assert "Choose one of" in response.json()["hint"]


def test_the_categories_are_counted(client: TestClient) -> None:
    """For a sidebar: only the ones that have a recipe."""
    response = client.get("/api/recipes/categories")

    assert response.status_code == 200
    counted = {entry["category"]: entry["count"] for entry in response.json()["items"]}
    assert counted["media"] >= 2 and counted["security"] >= 1
    assert "smart-home" not in counted
    assert sum(counted.values()) == client.get("/api/recipes").json()["total"]


def test_a_recipe_in_full_says_where_every_value_comes_from(client: TestClient) -> None:
    """Variables without values, images with digests, ports it opens, the health check."""
    syncthing = client.get("/api/recipes/syncthing").json()
    freshrss = client.get("/api/recipes/freshrss").json()

    assert [(entry["port"], entry["protocol"]) for entry in syncthing["public_ports"]] == [
        (22000, "tcp"),
        (22000, "udp"),
    ]
    image = syncthing["images"][0]
    assert image["repository"].endswith("syncthing/syncthing") and image["digest"].startswith(
        "sha256:"
    )
    assert syncthing["health"] == {"path": "/rest/noauth/health", "expect": "200", "timeout": None}
    variables = {entry["name"]: entry for entry in freshrss["variables"]}
    assert (
        variables["ADMIN_EMAIL"]["required"] is True and variables["ADMIN_EMAIL"]["input"] is True
    )
    assert variables["ADMIN_PASSWORD"]["origin"] == "generated"
    assert (
        variables["ADMIN_PASSWORD"]["secret"] is True
        and variables["ADMIN_PASSWORD"]["default"] is None
    )
    assert variables["TZ"]["default"] == "UTC" and variables["PUBLIC_URL"]["origin"] == "deployment"
    assert freshrss["source"] is None and freshrss["layout"] == "inplace"
    assert freshrss["notes"] and freshrss["changelog"] == []
    body = client.get("/api/recipes/stirling-pdf").json()
    assert body["health"]["timeout"] == 300


def test_a_native_recipe_in_full_keeps_the_shape_the_console_reads(client: TestClient) -> None:
    """source, layout, env, persistent paths: what the wizard's review step shows."""
    wordpress = client.get("/api/recipes/wordpress").json()

    assert wordpress["source"]["checksum_url"].endswith(".sha1")
    assert {"name": "WORDPRESS_AUTH_KEY", "generated": True} in wordpress["env"]
    assert wordpress["persistent_paths"] == []
    assert client.get("/api/recipes/uptime-kuma").json()["persistent_paths"] == ["data"]
    assert client.get("/api/recipes/drupal").status_code == 404


def test_every_route_declares_the_permission_it_needs() -> None:
    """The route map covers every route of the router, and installing is managing."""
    declared = {
        (
            method,
            f"/api/recipes{route.path}".removesuffix("/") if route.path != "" else "/api/recipes",
        )
        for route in recipes_api.router.routes
        for method in getattr(route, "methods", ())
    }

    assert declared == set(ROUTES)
    assert ROUTES[("POST", "/api/recipes/{name}/install")] == "apps.manage"
    assert ROUTES[("POST", "/api/recipes/installed/{domain}/update")] == "apps.deploy"
    assert ROUTES[("GET", "/api/recipes")] == "apps.read"


def test_the_catalogue_is_behind_the_sign_in_and_the_logo_carries_the_consoles_policy(
    tmp_path: Path, recipe_store: NoustStore, runner: object
) -> None:
    """The real application, once: 401 without a session, and the CSP the console enforces."""
    from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
    from noust.web.server import create_app, get_token_manager

    full = create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    anonymous = TestClient(full, client=("testclient", 50001), follow_redirects=False)
    for path in (
        "/api/recipes",
        "/api/recipes/memos",
        "/api/recipes/memos/logo",
        "/api/recipes/installed",
    ):
        assert anonymous.get(path).status_code == 401, path

    signed_in = TestClient(full, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = signed_in.post("/api/auth/login", json={"token": token})
    signed_in.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    response = signed_in.get("/api/recipes/memos/logo")

    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert (
        "img-src 'self'" in policy
        and "script-src 'self'" in policy
        and "unsafe-inline" not in policy
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert signed_in.get("/api/recipes?category=media").status_code == 200


# ---------------------------------------------------------------------------
# Logos
# ---------------------------------------------------------------------------


def test_a_logo_is_served_as_an_image_that_needs_nothing_the_policy_forbids(
    client: TestClient,
) -> None:
    """image/svg+xml, nosniff, cacheable, and the file itself has no script or style."""
    response = client.get("/api/recipes/vaultwarden/logo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"].startswith("public")
    assert response.text.lstrip().startswith("<svg")
    assert "<script" not in response.text and "style" not in response.text


def test_a_logo_of_a_recipe_that_does_not_exist_is_a_404(client: TestClient) -> None:
    """Not a 500, not an empty image."""
    assert client.get("/api/recipes/drupal/logo").status_code == 404
    assert client.get("/api/recipes/..%2f..%2fetc/logo").status_code in (404, 400)


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def test_installing_queues_the_one_deployment_with_the_variables_given(
    client: TestClient, queued: list[dict[str, Any]]
) -> None:
    """POST /api/recipes/{name}/install is POST /api/apps with a recipe."""
    response = client.post(
        "/api/recipes/freshrss/install",
        json={"domain": DOMAIN, "variables": {"ADMIN_EMAIL": "me@example.com"}, "ssl": False},
    )

    assert response.status_code == 202, response.text
    kwargs = queued[0]["kwargs"]
    assert (kwargs["recipe"], kwargs["app_type"], kwargs["domain"]) == (
        "freshrss",
        "docker-compose",
        DOMAIN,
    )
    assert kwargs["env_vars"] == {"ADMIN_EMAIL": "me@example.com"} and kwargs["ssl"] is False
    assert queued[0]["metadata"]["recipe"] == "freshrss"


def test_installing_without_a_required_variable_is_a_400_naming_it_and_queues_nothing(
    client: TestClient, queued: list[dict[str, Any]]
) -> None:
    """${VAR:?required}: before a job exists, before a database is provisioned."""
    response = client.post("/api/recipes/freshrss/install", json={"domain": DOMAIN})

    assert response.status_code == 400, response.text
    assert "ADMIN_EMAIL" in response.json()["detail"]
    assert response.json()["fields"] and queued == []


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/api/recipes/ghost/install", {"domain": DOMAIN}, 400),
        ("/api/recipes/drupal/install", {"domain": DOMAIN}, 404),
        ("/api/recipes/memos/install", {}, 422),
        ("/api/recipes/memos/install", {"domain": "not a domain"}, 400),
    ],
    ids=["unavailable", "unknown", "no-domain", "bad-domain"],
)
def test_installing_refuses_what_cannot_be_installed(
    client: TestClient,
    queued: list[dict[str, Any]],
    path: str,
    body: dict[str, Any],
    status: int,
) -> None:
    """Nothing queued."""
    response = client.post(path, json=body)

    assert response.status_code == status, response.text
    assert queued == []


def test_installing_on_a_domain_that_is_taken_is_a_409(
    client: TestClient, queued: list[dict[str, Any]], recipe_store: NoustStore
) -> None:
    """The same answer POST /api/apps gives."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    response = client.post("/api/recipes/memos/install", json={"domain": DOMAIN})

    assert response.status_code == 409, response.text
    assert queued == []


# ---------------------------------------------------------------------------
# What is installed, and updating it
# ---------------------------------------------------------------------------


def install(store: NoustStore, name: str, version: str | None = None) -> None:
    """An application installed from a recipe, at its catalogue version or another."""
    import dataclasses

    store.create_app(App(domain=DOMAIN, app_type="docker-compose", port=8123))
    recipe = get_recipe(name)
    if version is not None:
        recipe = dataclasses.replace(recipe, version=version)
    record_install(DOMAIN, recipe, store=store)


def test_installed_recipes_are_listed_with_their_version_and_whether_they_are_behind(
    client: TestClient, recipe_store: NoustStore
) -> None:
    """The catalogue knows what is deployed, and says when it is out of date."""
    install(recipe_store, "vaultwarden", "1.0.0")

    listed = client.get("/api/recipes/installed")
    one = client.get(f"/api/recipes/installed/{DOMAIN}")

    assert listed.status_code == 200, listed.text
    entry = listed.json()["items"][0]
    assert (entry["domain"], entry["recipe"], entry["kind"]) == (DOMAIN, "vaultwarden", "compose")
    assert entry["installed_version"] == "1.0.0"
    assert entry["latest_version"] == get_recipe("vaultwarden").version
    assert entry["update_available"] is True
    assert one.json() == entry
    assert client.get("/api/recipes/installed/other.example.com").status_code == 404


def test_an_application_that_is_current_says_so(
    client: TestClient, recipe_store: NoustStore
) -> None:
    """update_available is false at the catalogue's version."""
    install(recipe_store, "memos")

    assert client.get("/api/recipes/installed").json()["items"][0]["update_available"] is False


def test_updating_queues_the_recipe_update_job(
    client: TestClient, queued: list[dict[str, Any]], recipe_store: NoustStore
) -> None:
    """The job that runs update_recipe, for an application that is behind."""
    from noust.web.recipe_jobs import update_recipe_job

    install(recipe_store, "vaultwarden", "1.0.0")

    response = client.post(
        f"/api/recipes/installed/{DOMAIN}/update", json={"variables": {"X": "1"}}
    )

    assert response.status_code == 202, response.text
    assert queued[0]["func"] is update_recipe_job
    assert queued[0]["kwargs"] == {"domain": DOMAIN, "variables": {"X": "1"}, "force": False}
    assert queued[0]["metadata"] == {"domain": DOMAIN, "recipe": "vaultwarden"}


def test_updating_what_is_current_is_a_409_unless_forced(
    client: TestClient, queued: list[dict[str, Any]], recipe_store: NoustStore
) -> None:
    """nothing_new, as an update with nothing to pull answers."""
    install(recipe_store, "memos")

    refused = client.post(f"/api/recipes/installed/{DOMAIN}/update")
    forced = client.post(f"/api/recipes/installed/{DOMAIN}/update", json={"force": True})

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "nothing_new"
    assert forced.status_code == 202, forced.text
    assert queued[0]["kwargs"]["force"] is True


def test_updating_what_was_not_installed_from_a_recipe_is_a_404(
    client: TestClient, queued: list[dict[str, Any]], recipe_store: NoustStore
) -> None:
    """The ordinary update is for that."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    assert client.post(f"/api/recipes/installed/{DOMAIN}/update").status_code == 404
    assert queued == []


def test_the_job_runs_the_recipe_update_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress from the phases, the outcome in the result."""
    from noust.recipes.updates import RecipeUpdate
    from noust.web.jobs import Job, JobContext, JobType
    from noust.web.recipe_jobs import update_recipe_job

    seen: dict[str, Any] = {}

    def fake(domain: str, **kwargs: Any) -> RecipeUpdate:
        seen.update(domain=domain, **kwargs)
        kwargs["on_phase"](2, 4, "Building")
        kwargs["on_step"]("a step")
        return RecipeUpdate(
            domain=domain,
            recipe="memos",
            from_version="0.30.0",
            to_version="0.31.0",
            updated=True,
            deployment_id=9,
            added_variables=("NEW",),
        )

    monkeypatch.setattr("noust.web.recipe_jobs.update_recipe", fake)
    job = Job(id="job-u", type=JobType.UPDATE, name="update", description="")

    result = update_recipe_job(
        DOMAIN, {"A": "1"}, False, job_context=JobContext(job, lambda _job: None)
    )

    assert seen["overrides"] == {"A": "1"} and seen["trigger"] == "panel"
    assert result["status"] == "updated" and result["to_version"] == "0.31.0"
    assert result["added_variables"] == ["NEW"] and result["deployment_id"] == 9
    assert any(entry.step == 25 for entry in job.logs)
    with pytest.raises(ValueError, match="job_context is required"):
        update_recipe_job(DOMAIN)
