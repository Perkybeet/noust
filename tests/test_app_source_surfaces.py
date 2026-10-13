"""
The ways an operator reaches an application's source options (checklist A-12, A-13, A-28).

``noust app source`` and ``/api/apps/{d}/source-options`` are clients of
``noust.core.app_source_store``; what is tested here is what each adds:
parsing and presenting for the commands, and for the API the permission each
route declares and the error contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_source_store
from noust.core.store import App, NoustStore

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "app"
    root.mkdir()
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(root)))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from noust.core.config import Config
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()
    set_runner(None)


def invoke(*args: str) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args))


class TestTheCommands:
    def test_show_lists_the_defaults(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "source", "show", DOMAIN, "--json")

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["submodules"] is True
        assert shown["lfs"] == "auto"
        assert shown["shallow"] is True
        assert shown["watch_paths"] == []
        assert shown["skip_markers"] is True
        assert shown["customised"] is False

    def test_show_in_words(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "source", "show", DOMAIN)

        assert result.exit_code == 0, result.output
        assert "every push deploys" in result.output
        assert "Shallow clone" in result.output

    def test_set_changes_only_what_is_given(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke(
            "app",
            "source",
            "set",
            DOMAIN,
            "--no-shallow",
            "--lfs",
            "on",
            "--watch-path",
            "apps/web/**",
            "--watch-path",
            "!apps/web/docs/**",
            "--json",
        )

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["shallow"] is False
        assert shown["lfs"] == "on"
        assert shown["submodules"] is True
        assert shown["watch_paths"] == ["apps/web/**", "!apps/web/docs/**"]
        assert app_source_store.get_options(DOMAIN, store=store).shallow is False

    def test_switches_have_a_negative_form(self, store: NoustStore, cli_config: Any) -> None:
        invoke("app", "source", "set", DOMAIN, "--no-submodules", "--no-skip-markers")
        options = app_source_store.get_options(DOMAIN, store=store)
        assert options.submodules is False
        assert options.skip_markers is False

    def test_the_watch_paths_can_be_cleared(self, store: NoustStore, cli_config: Any) -> None:
        invoke("app", "source", "set", DOMAIN, "--watch-path", "a/**")
        result = invoke("app", "source", "set", DOMAIN, "--clear-watch-paths")
        assert result.exit_code == 0, result.output
        assert app_source_store.get_options(DOMAIN, store=store).watch_paths == ()

    def test_set_refuses_a_bad_watch_path_and_names_the_field(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        result = invoke("app", "source", "set", DOMAIN, "--watch-path", "../etc/**")

        assert result.exit_code != 0
        assert result.exception.field == "watch_paths"
        assert app_source_store.get_options(DOMAIN, store=store).customised is False

    def test_set_needs_something_to_set(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "source", "set", DOMAIN)

        assert result.exit_code != 0
        assert "at least one option" in result.output

    def test_watch_paths_and_clearing_them_are_exclusive(
        self, store: NoustStore, cli_config: Any
    ) -> None:
        result = invoke(
            "app", "source", "set", DOMAIN, "--watch-path", "a/**", "--clear-watch-paths"
        )
        assert result.exit_code != 0
        assert "not both" in result.output

    def test_reset_goes_back_to_the_defaults(self, store: NoustStore, cli_config: Any) -> None:
        invoke("app", "source", "set", DOMAIN, "--no-shallow")
        result = invoke("app", "source", "reset", DOMAIN)
        assert result.exit_code == 0, result.output
        assert app_source_store.get_options(DOMAIN, store=store).customised is False

    def test_a_dry_run_changes_nothing(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("--dry-run", "app", "source", "set", DOMAIN, "--no-shallow")
        assert result.exit_code == 0, result.output
        assert "Would set" in result.output
        assert app_source_store.get_options(DOMAIN, store=store).customised is False

    def test_an_unknown_application_is_an_error(self, store: NoustStore, cli_config: Any) -> None:
        result = invoke("app", "source", "show", "nope.example.com")
        assert result.exit_code != 0
        assert "Application not found" in str(result.exception)


def source_client() -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import app_source
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_source.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


class TestTheApi:
    def test_every_route_declares_its_permission(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_source import ROUTES

        assert ROUTES[("GET", "/api/apps/{domain}/source-options")] == Permission.APPS_READ
        assert ROUTES[("PUT", "/api/apps/{domain}/source-options")] == Permission.APPS_MANAGE
        assert ROUTES[("DELETE", "/api/apps/{domain}/source-options")] == Permission.APPS_MANAGE

    def test_get_put_and_delete(self, store: NoustStore) -> None:
        client = source_client()

        first = client.get(f"/api/apps/{DOMAIN}/source-options")
        assert first.status_code == 200, first.text
        assert first.json()["customised"] is False

        changed = client.put(
            f"/api/apps/{DOMAIN}/source-options",
            json={"shallow": False, "watch_paths": ["apps/web/**"], "lfs": "off"},
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["shallow"] is False
        assert body["lfs"] == "off"
        assert body["watch_paths"] == ["apps/web/**"]
        assert body["submodules"] is True

        # A second PUT keeps what the first set.
        again = client.put(f"/api/apps/{DOMAIN}/source-options", json={"skip_markers": False})
        assert again.json()["watch_paths"] == ["apps/web/**"]
        assert again.json()["skip_markers"] is False

        gone = client.delete(f"/api/apps/{DOMAIN}/source-options")
        assert gone.status_code == 200
        assert gone.json()["customised"] is False

    def test_a_bad_value_is_a_400_naming_the_field(self, store: NoustStore) -> None:
        response = source_client().put(
            f"/api/apps/{DOMAIN}/source-options", json={"watch_paths": ["../x"]}
        )
        assert response.status_code == 400
        assert response.json()["fields"] is not None or "watch_paths" in response.text

    def test_an_empty_body_is_refused(self, store: NoustStore) -> None:
        response = source_client().put(f"/api/apps/{DOMAIN}/source-options", json={})
        assert response.status_code == 400

    def test_an_unknown_lfs_mode_is_a_422(self, store: NoustStore) -> None:
        response = source_client().put(
            f"/api/apps/{DOMAIN}/source-options", json={"lfs": "sometimes"}
        )
        assert response.status_code == 422

    def test_an_unknown_application_is_a_404(self, store: NoustStore) -> None:
        assert source_client().get("/api/apps/nope.example.com/source-options").status_code == 404
