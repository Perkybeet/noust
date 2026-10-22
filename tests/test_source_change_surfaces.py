# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ways an operator changes an application's source (checklist A-20).

``noust app source change`` and ``/api/apps/{d}/source/plan`` and ``PUT /api/apps/{d}/source`` are
clients of :mod:`noust.deployers.source_change`, which ``tests/test_source_change.py`` covers; what
is pinned here is what each adds: what the command prints and asks, the rehearsal that writes
nothing, the permission each route declares, sudo mode for the change, and the privilege a local
path needs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.managers.source_manager import _GIT_SAFE_CONFIG
from noust.web.permissions import Permission
from noust.web.permissions.routes_app_source import ROUTES

DOMAIN = "shop.example.com"
OLD = "https://github.com/example/shop.git"
NEW = "https://git.example.org/team/shop.git"
HEAD = "4f1c2b9d8e7a6f5c4b3a29181716151413121110"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "app"
    root.mkdir()
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source=OLD,
            branch="main",
            app_path=str(root),
            layout="releases",
        )
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def remote(runner: FakeRunner) -> Iterator[FakeRunner]:
    ask = ["git", *_GIT_SAFE_CONFIG, "ls-remote"]
    runner.script(
        [*ask, "--exit-code", "--", NEW, "refs/heads/release"],
        stdout=f"{HEAD}\trefs/heads/release\n",
    )
    set_runner(runner)
    yield runner
    set_runner(None)


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from noust.core.config import Config

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


def invoke(*args: str, stdin: str | None = None) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args), input=stdin)


class TestTheCommand:
    def test_it_shows_the_plan_and_changes_the_source_with_yes(
        self, store: NoustStore, remote: FakeRunner, cli_config: None
    ) -> None:
        result = invoke("app", "source", "change", DOMAIN, NEW, "--branch", "release", "--yes")

        assert result.exit_code == 0, result.output
        assert OLD in result.output and NEW in result.output
        assert "another repository" in result.output
        assert f"noust update {DOMAIN}" in result.output
        row = store.get_app(DOMAIN)
        assert row is not None and (row.source, row.branch) == (NEW, "release")

    def test_it_asks_first_and_a_no_changes_nothing(
        self,
        store: NoustStore,
        remote: FakeRunner,
        cli_config: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("noust.cli.commands.app_source._can_ask", lambda: True)

        result = invoke("app", "source", "change", DOMAIN, NEW, "-b", "release", stdin="n\n")

        assert result.exit_code == 0, result.output
        assert "Nothing changed" in result.output
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_without_a_terminal_it_will_not_guess(
        self, store: NoustStore, remote: FakeRunner, cli_config: None
    ) -> None:
        result = invoke("app", "source", "change", DOMAIN, NEW, "-b", "release")

        assert result.exit_code != 0
        assert "--yes" in result.output
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_a_rehearsal_writes_nothing(
        self, store: NoustStore, remote: FakeRunner, cli_config: None
    ) -> None:
        # No network here: a rehearsal's read-only probes run for real, which this suite forbids.
        result = invoke(
            "--dry-run", "app", "source", "change", DOMAIN, NEW, "-b", "release", "--no-check"
        )

        assert result.exit_code == 0, result.output
        assert "Would change the source" in result.output
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_json_is_the_plan_and_whether_it_was_applied(
        self, store: NoustStore, remote: FakeRunner, cli_config: None
    ) -> None:
        shown = json.loads(
            invoke(
                "app", "source", "change", DOMAIN, NEW, "-b", "release", "--yes", "--json"
            ).output
        )

        assert shown["applied"] is True and shown["target"] == NEW
        assert shown["head"] == HEAD and shown["target_branch"] == "release"

    def test_no_check_asks_nothing_of_the_remote(
        self, store: NoustStore, remote: FakeRunner, cli_config: None
    ) -> None:
        result = invoke(
            "app", "source", "change", DOMAIN, NEW, "-b", "release", "--no-check", "--yes"
        )

        assert result.exit_code == 0, result.output
        assert not [call for call in remote.calls if "ls-remote" in call]

    def test_the_same_source_is_nothing_to_change(
        self, store: NoustStore, runner: FakeRunner, cli_config: None
    ) -> None:
        runner.script(
            ["git", *_GIT_SAFE_CONFIG, "ls-remote", "--exit-code", "--", OLD, "refs/heads/main"],
            stdout=f"{HEAD}\trefs/heads/main\n",
        )
        set_runner(runner)

        result = invoke("app", "source", "change", DOMAIN, OLD, "--yes")

        assert result.exit_code != 0
        assert "already deploys" in result.output

    def test_an_unknown_application(self, store: NoustStore, cli_config: None) -> None:
        result = invoke("app", "source", "change", "nope.example.com", NEW, "--yes")

        assert result.exit_code != 0
        assert "Application not found" in str(result.exception)


def test_every_route_declares_its_permission() -> None:
    assert ROUTES[("POST", "/api/apps/{domain}/source/plan")] == Permission.APPS_MANAGE
    assert ROUTES[("PUT", "/api/apps/{domain}/source")] == Permission.APPS_MANAGE


def client_for(*, elevated: bool = True, kind: str = "session") -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_source as api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/apps")
    session = {"type": kind, "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestTheApi:
    def test_the_plan_asks_the_remote_and_writes_nothing(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        response = client_for().post(
            f"/api/apps/{DOMAIN}/source/plan", json={"source": NEW, "branch": "release"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["target"] == NEW and body["head"] == HEAD and body["checked"] is True
        assert body["changed"] is True and body["same_repository"] is False
        assert any("another repository" in warning for warning in body["warnings"])
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_the_change_writes_it_and_says_so(self, store: NoustStore, remote: FakeRunner) -> None:
        response = client_for().put(
            f"/api/apps/{DOMAIN}/source", json={"source": NEW, "branch": "release"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["applied"] is True
        row = store.get_app(DOMAIN)
        assert row is not None and (row.source, row.branch) == (NEW, "release")

    def test_the_change_needs_sudo_mode_and_the_plan_does_not(
        self, store: NoustStore, remote: FakeRunner
    ) -> None:
        client = client_for(elevated=False)

        refused = client.put(
            f"/api/apps/{DOMAIN}/source", json={"source": NEW, "branch": "release"}
        )
        planned = client.post(
            f"/api/apps/{DOMAIN}/source/plan", json={"source": NEW, "branch": "release"}
        )

        assert refused.status_code == 403 and planned.status_code == 200
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD

    def test_a_refusal_names_the_field(self, store: NoustStore, remote: FakeRunner) -> None:
        response = client_for().post(
            f"/api/apps/{DOMAIN}/source/plan",
            json={"source": "https://user:s3cr3tvalue@git.example.org/team/shop.git"},
        )

        assert response.status_code == 400
        assert "s3cr3tvalue" not in response.text
        assert response.json()["fields"] == {"source": "The new source carries a credential"} or (
            "source" in json.dumps(response.json())
        )

    def test_an_unknown_application_is_a_404(self, store: NoustStore, remote: FakeRunner) -> None:
        response = client_for().post(
            "/api/apps/nope.example.com/source/plan", json={"source": NEW, "branch": "release"}
        )

        assert response.status_code == 404

    def test_a_remote_that_cannot_be_read_is_a_400_with_gits_words(
        self, store: NoustStore, runner: FakeRunner
    ) -> None:
        runner.script(
            ["git", *_GIT_SAFE_CONFIG, "ls-remote"],
            exit_code=128,
            stderr="fatal: repository not found",
        )
        set_runner(runner)

        response = client_for().post(
            f"/api/apps/{DOMAIN}/source/plan", json={"source": NEW, "branch": "release"}
        )

        assert response.status_code == 400
        set_runner(None)

    def test_a_directory_on_the_server_is_not_for_a_token(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = tmp_path / "code"
        directory.mkdir()

        response = client_for(kind="token").put(
            f"/api/apps/{DOMAIN}/source", json={"source": str(directory)}
        )

        assert response.status_code == 403
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == OLD
