# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``/api/recipes/private``: the server's own recipes through the API (checklist D-21)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.recipes import get_recipe
from noust.web.api import recipes as recipes_api
from noust.web.api.deps import require_elevated
from tests.recipes.factory import compose_recipe
from tests.recipes.test_api import small_app
from tests.recipes.test_operator_recipes import LOGO


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The server's recipes folder, empty."""
    directory = tmp_path / "recipes"
    directory.mkdir(mode=0o755)
    monkeypatch.setenv("NOUST_RECIPES_DIR", str(directory))
    return directory


@pytest.fixture
def client(recipe_store: NoustStore) -> TestClient:
    """The router alone, signed in and in sudo mode."""
    api = small_app()
    api.dependency_overrides[require_elevated] = lambda: {
        "type": "session",
        "session_id": "s",
        "scope": "admin",
        "name": "alice",
    }
    return TestClient(api)


def body(**overrides: Any) -> dict[str, Any]:
    """A request that adds a valid recipe called acme-wiki."""
    document = yaml.safe_dump(compose_recipe(name="acme-wiki", title="Acme Wiki"), sort_keys=False)
    return {"document": document, "assets": {"logo.svg": LOGO}, **overrides}


def test_check_validates_and_adds_nothing(client: TestClient, folder: Path) -> None:
    response = client.post("/api/recipes/private/check", json=body())

    assert response.status_code == 200, response.text
    assert response.json()["added"] is False
    assert response.json()["recipe"]["origin"] == "operator"
    assert not (folder / "acme-wiki.yaml").exists()


def test_check_names_the_field_that_is_wrong(client: TestClient, folder: Path) -> None:
    document = yaml.safe_dump({**compose_recipe(name="acme-wiki"), "colour": "red"})

    response = client.post("/api/recipes/private/check", json={"document": document})

    assert response.status_code == 400
    assert "colour" in response.text


def test_add_puts_the_recipe_in_the_catalogue_with_its_origin(
    client: TestClient, folder: Path
) -> None:
    response = client.post("/api/recipes/private", json=body())

    assert response.status_code == 201, response.text
    assert response.json()["added"] is True
    assert get_recipe("acme-wiki").origin == "operator"
    listed = client.get("/api/recipes", params={"origin": "operator"}).json()
    assert [item["name"] for item in listed["items"]] == ["acme-wiki"]
    assert listed["items"][0]["origin"] == "operator"
    assert client.get("/api/recipes/acme-wiki/logo").status_code == 200


def test_add_refuses_a_second_time_without_replace(client: TestClient, folder: Path) -> None:
    client.post("/api/recipes/private", json=body())

    again = client.post("/api/recipes/private", json=body())
    replaced = client.post("/api/recipes/private", json=body(replace=True))

    assert again.status_code == 400 and "already exists" in again.text
    assert replaced.status_code == 201


def test_a_shipped_name_is_refused(client: TestClient, folder: Path) -> None:
    document = yaml.safe_dump(compose_recipe(name="vaultwarden"))

    response = client.post(
        "/api/recipes/private", json={"document": document, "assets": {"logo.svg": LOGO}}
    )

    assert response.status_code == 400 and "shipped" in response.text


@pytest.mark.parametrize("path", ["../escape.conf", "/etc/passwd", "a b.conf"])
def test_an_asset_path_that_leaves_the_recipe_is_refused(
    client: TestClient, folder: Path, path: str
) -> None:
    response = client.post(
        "/api/recipes/private/check", json=body(assets={path: "x", "logo.svg": LOGO})
    )

    assert response.status_code == 400 and "plain relative path" in response.text


def test_a_document_without_a_valid_name_is_refused(client: TestClient, folder: Path) -> None:
    response = client.post("/api/recipes/private/check", json={"document": "title: x\n"})

    assert response.status_code == 400 and "name" in response.text


def test_remove_deletes_one_of_the_servers_own_recipes_only(
    client: TestClient, folder: Path
) -> None:
    client.post("/api/recipes/private", json=body())

    removed = client.delete("/api/recipes/private/acme-wiki")
    unknown = client.delete("/api/recipes/private/acme-wiki")
    shipped = client.delete("/api/recipes/private/vaultwarden")

    assert removed.status_code == 204
    assert unknown.status_code == 404
    assert shipped.status_code == 400 and "not one of yours" in shipped.text
    assert not (folder / "acme-wiki.yaml").exists()


def test_the_list_carries_the_files_that_could_not_be_read(
    client: TestClient, folder: Path
) -> None:
    (folder / "notes.txt").write_text("hello")

    listed = client.get("/api/recipes", params={"q": "vaultwarden"}).json()

    assert [problem["name"] for problem in listed["problems"]] == ["notes.txt"]


def test_an_unknown_origin_is_refused(client: TestClient) -> None:
    assert client.get("/api/recipes", params={"origin": "elsewhere"}).status_code == 400


def test_the_new_routes_declare_a_permission() -> None:
    from noust.web.permissions.routes_recipes import ROUTES

    declared = {(method, path) for method, path in ROUTES}
    for route in recipes_api.router.routes:
        path = "/api/recipes" + getattr(route, "path", "")
        for method in getattr(route, "methods", ()) or ():
            if method != "HEAD":
                assert (method, path) in declared, (method, path)
