# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ways an operator reaches the build block: ``noust app build``, ``noust runtime`` and the API.

The three are clients of one implementation, so what is tested here is what
each adds: parsing and presenting for the commands, and for the API the
permission each route declares, sudo mode for writing, and the error contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_build_store
from noust.core.store import App, NoustStore

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path):
    """An isolated store with one Node application."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "app"
    root.mkdir()
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(root)))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty configuration, and the default runner put back after --dry-run."""
    from noust.core.config import Config
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()
    set_runner(None)


def invoke(*args: str, stdin: str | None = None) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args), input=stdin)


class TestTheCommands:
    def test_show_says_where_the_block_comes_from(
        self, tmp_path: Path, store: NoustStore, cli_config: Any
    ) -> None:
        (tmp_path / "app" / "noust.yaml").write_text(
            "build:\n  start: ./serve\n  runtime: node@22\n"
        )

        result = invoke("app", "build", "show", DOMAIN, "--json")

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["source"] == "repo"
        assert shown["start"] == "./serve"
        assert shown["runtime"] == "node@22"
        assert shown["document"] is None

    def test_show_for_an_application_with_nothing_says_the_types_commands_apply(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        result = invoke("app", "build", "show", DOMAIN)

        assert result.exit_code == 0, result.output
        assert "commands of the application's type" in result.output

    def test_set_from_flags_stores_the_validated_block_and_says_it_replaces_the_repositorys(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        result = invoke(
            "app",
            "build",
            "set",
            DOMAIN,
            "--install",
            "pnpm install --frozen-lockfile",
            "--build",
            "pnpm build",
            "--start",
            "node dist/server.js --port ${PORT}",
            "--runtime",
            "node@22",
            "--base-directory",
            "apps/web",
        )

        assert result.exit_code == 0, result.output
        assert "replaces the repository's noust.yaml whole" in result.output
        document = app_build_store.get_document(DOMAIN, store=store)
        assert document is not None
        assert "base_directory: apps/web" in document
        assert "install: pnpm install --frozen-lockfile" in document
        assert "runtime: node@22" in document

    def test_set_adds_to_what_the_operator_already_set(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js")

        result = invoke("app", "build", "set", DOMAIN, "--runtime", "node@20", "--json")

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["start"] == "node a.js"
        assert shown["runtime"] == "node@20"
        assert shown["source"] == "operator"

    def test_set_from_a_file_replaces_the_whole_block(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js")

        result = invoke(
            "app", "build", "set", DOMAIN, "--file", "-", stdin="build:\n  build: npm run b\n"
        )

        assert result.exit_code == 0, result.output
        document = app_build_store.get_document(DOMAIN, store=store)
        assert document == "build:\n  build: npm run b\n"

    def test_set_refuses_what_is_not_valid_naming_the_field_and_stores_nothing(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        result = invoke("app", "build", "set", DOMAIN, "--base-directory", "../etc")

        assert result.exit_code != 0
        assert result.exception.field == "build.base_directory"
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_set_needs_something_to_set(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "build", "set", DOMAIN)

        assert result.exit_code != 0
        assert "at least one key" in result.output

    def test_flags_and_a_file_are_exclusive(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke(
            "app", "build", "set", DOMAIN, "--start", "x", "--file", "-", stdin="build: {}\n"
        )

        assert result.exit_code != 0
        assert "not both" in result.output

    def test_unset_removes_one_key_and_keeps_the_block_the_operators(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js", "--runtime", "node@22")

        result = invoke("app", "build", "unset", DOMAIN, "runtime")

        assert result.exit_code == 0, result.output
        assert app_build_store.get_document(DOMAIN, store=store) == "build:\n  start: node a.js\n"

    def test_unset_without_a_key_clears_the_whole_block(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js")

        result = invoke("app", "build", "unset", DOMAIN)

        assert result.exit_code == 0, result.output
        assert app_build_store.get_document(DOMAIN, store=store) is None
        assert "applies again" in result.output

    def test_unsetting_the_last_key_gives_the_repository_its_say_back(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js")

        invoke("app", "build", "unset", DOMAIN, "start")

        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_a_key_that_is_not_one_is_refused(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "build", "unset", DOMAIN, "colour")

        assert result.exit_code != 0
        assert "Not a key of the build block" in result.output

    def test_a_dashed_key_is_the_same_key(self, store: NoustStore, cli_config: Any) -> None:
        invoke("app", "build", "set", DOMAIN, "--base-directory", "apps/web", "--start", "x")

        result = invoke("app", "build", "unset", DOMAIN, "base-directory")

        assert result.exit_code == 0, result.output
        assert "base_directory" not in (app_build_store.get_document(DOMAIN, store=store) or "")

    def test_a_dry_run_changes_nothing(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("--dry-run", "app", "build", "set", DOMAIN, "--start", "x")

        assert result.exit_code == 0, result.output
        assert "Would set the build block" in result.output
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_an_unknown_application_is_an_error(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "build", "show", "nope.example.com")

        assert result.exit_code != 0
        assert "Application not found" in str(result.exception)


class TestRuntimeCommands:
    def test_list_with_nothing_installed_says_how_to_pin_one(
        self, tmp_path: Path, store: NoustStore, cli_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.core.paths.RUNTIMES_DIR", tmp_path / "none")
        monkeypatch.setattr(
            "noust.managers.runtimes.RuntimeManager.host", lambda self, language: None
        )
        monkeypatch.setattr("noust.managers.runtimes.RuntimeManager._php_versions", lambda self: [])

        result = invoke("runtime", "list")

        assert result.exit_code == 0, result.output
        assert "noust runtime install node@22" in result.output

    def test_list_names_what_is_installed_and_who_runs_on_it(
        self, tmp_path: Path, store: NoustStore, cli_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "runtimes" / "node" / "22.11.0"
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "node").write_text("")
        monkeypatch.setattr("noust.core.paths.RUNTIMES_DIR", tmp_path / "runtimes")
        monkeypatch.setattr(
            "noust.managers.runtimes.RuntimeManager.host", lambda self, language: None
        )
        monkeypatch.setattr("noust.managers.runtimes.RuntimeManager._php_versions", lambda self: [])
        app_build_store.set_applied(
            DOMAIN, {"runtime": {"language": "node", "version": "22.11.0"}}, store=store
        )

        result = invoke("runtime", "list", "--json")

        assert result.exit_code == 0, result.output
        listed = json.loads(result.output)
        assert listed["installed"] == [
            {
                "language": "node",
                "version": "22.11.0",
                "home": str(home),
                "managed": True,
                "used_by": [DOMAIN],
            }
        ]

    def test_remove_needs_one_version_not_a_range(self, cli_config: Any) -> None:
        result = invoke("runtime", "remove", "node@22")

        assert result.exit_code != 0
        assert "does not name one version" in str(result.exception)

    def test_remove_does_not_touch_php(self, cli_config: Any) -> None:
        result = invoke("runtime", "remove", "php@8.3")

        assert result.exit_code != 0
        assert "distribution's package" in result.exception.details

    def test_install_refuses_what_is_not_a_pin(self, cli_config: Any) -> None:
        result = invoke("runtime", "install", "ruby@3.2")

        assert result.exit_code != 0
        assert result.exception.field == "runtime"
        assert "does not pin" in str(result.exception)

    def test_a_dry_run_installs_nothing(self, cli_config: Any) -> None:
        result = invoke("--dry-run", "runtime", "install", "node@22")

        assert result.exit_code == 0, result.output
        assert "Would install node@22" in result.output


def build_client(*, elevated: bool) -> Any:
    """A client for the build router alone, authenticated, elevated or not."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_build
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_build.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestTheApi:
    def test_every_route_declares_its_permission_and_writing_is_root_equivalent(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_build import ROUTES

        assert ROUTES[("PUT", "/api/apps/{domain}/build")] == Permission.ROOT_EQUIVALENT
        assert ROUTES[("GET", "/api/apps/{domain}/build")] == Permission.APPS_READ
        assert ROUTES[("DELETE", "/api/apps/{domain}/build")] == Permission.APPS_MANAGE

    def test_get_set_and_clear(self, store: NoustStore) -> None:
        client = build_client(elevated=True)

        assert client.get(f"/api/apps/{DOMAIN}/build").json()["source"] == "none"

        response = client.put(
            f"/api/apps/{DOMAIN}/build",
            json={
                "start": "node server.js --port ${PORT}",
                "runtime": "node@22",
                "base_directory": "apps/web",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "operator"
        assert body["start"] == "node server.js --port ${PORT}"
        assert body["runtime"] == "node@22"
        assert body["base_directory"] == "apps/web"
        assert body["document"] is not None

        cleared = client.delete(f"/api/apps/{DOMAIN}/build")
        assert cleared.status_code == 200
        assert cleared.json()["source"] == "none"
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_writing_needs_sudo_mode(self, store: NoustStore) -> None:
        response = build_client(elevated=False).put(
            f"/api/apps/{DOMAIN}/build", json={"start": "node a.js"}
        )

        assert response.status_code == 403
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_an_invalid_block_is_400_with_its_field(self, store: NoustStore) -> None:
        response = build_client(elevated=True).put(
            f"/api/apps/{DOMAIN}/build", json={"base_directory": "../x"}
        )

        assert response.status_code == 400
        assert "build.base_directory" in response.json().get("fields", {})

    def test_a_key_the_type_cannot_honour_is_400_with_its_field(self, store: NoustStore) -> None:
        response = build_client(elevated=True).put(
            f"/api/apps/{DOMAIN}/build", json={"publish_directory": "dist"}
        )

        assert response.status_code == 400
        assert "build.publish_directory" in response.json().get("fields", {})

    def test_an_empty_block_is_refused_rather_than_made_to_replace_the_repositorys(
        self, store: NoustStore
    ) -> None:
        response = build_client(elevated=True).put(f"/api/apps/{DOMAIN}/build", json={})

        assert response.status_code == 400
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_an_unknown_application_is_404(self, store: NoustStore) -> None:
        client = build_client(elevated=True)

        assert client.get("/api/apps/nope.example.com/build").status_code == 404
        assert (
            client.put("/api/apps/nope.example.com/build", json={"start": "x"}).status_code == 404
        )
        assert client.delete("/api/apps/nope.example.com/build").status_code == 404

    def test_the_change_is_audited(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import audit

        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            audit, "record", lambda event, **kwargs: recorded.append((event, kwargs))
        )

        build_client(elevated=True).put(f"/api/apps/{DOMAIN}/build", json={"start": "node a.js"})

        assert [event for event, _ in recorded] == ["apps.build"]


class TestCreateWithABuildFile:
    def test_a_file_that_is_not_a_valid_block_stops_the_command_before_anything_is_fetched(
        self, tmp_path: Path, cli_config: Any
    ) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("build:\n  base_directory: ../x\n")

        result = invoke(
            "create",
            "-d",
            "new.example.com",
            "-s",
            "https://example.com/r.git",
            "--build-file",
            str(bad),
        )

        assert result.exit_code != 0
        assert result.exception.field == "build.base_directory"

    def test_it_is_not_for_a_recipe_which_pins_its_own(
        self, tmp_path: Path, cli_config: Any
    ) -> None:
        good = tmp_path / "good.yaml"
        good.write_text("build:\n  start: ./x\n")

        result = invoke(
            "create", "-d", "new.example.com", "--recipe", "ghost", "--build-file", str(good)
        )

        assert result.exit_code != 0
        assert "recipe pins its own" in result.output

    def test_it_is_offered_by_the_command(self) -> None:
        result = invoke("create", "--help")

        assert "--build-file" in result.output
        assert "several applications" in result.output or "one repository" in result.output
