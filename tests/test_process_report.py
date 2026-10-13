"""
What ``noust app processes`` and ``GET /api/apps/{domain}/processes`` say (checklist A-52).

The report reads the code an application runs now and says, per process type, whether the next
deployment uses it, and why not when it looks as if it should: the build block's ``start``
wins, deploy hooks win, a Procfile is only honoured by an application deployed with one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_build_store
from noust.core.exceptions import NoustError
from noust.core.store import App, NoustStore
from noust.deployers.helpers.process_report import describe_processes

DOMAIN = "shop.example.com"


def write(root: Path, name: str, text: str = "") -> Path:
    """
    Args:
        root: The project.
        name: A path inside it.
        text: Its content.

    Returns:
        The file.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Returns:
        A store installed as the process-wide singleton, holding the application.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(root), port=3000)
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def code(store: NoustStore) -> Path:
    """
    Returns:
        The directory the application runs from.
    """
    app = store.get_app(DOMAIN)
    assert app is not None
    return Path(app.app_path)


def test_an_application_with_nothing_declared(store: NoustStore, code: Path) -> None:
    report = describe_processes(DOMAIN, store=store)

    assert report.found.source == "none"
    assert report.to_dict()["processes"] == []
    assert report.notes == []


def test_noust_yaml_processes_are_in_effect(store: NoustStore, code: Path) -> None:
    write(
        code,
        "noust.yaml",
        "processes:\n  web: ./bin/server\n  release: ./bin/migrate\n  worker: ./bin/jobs\n",
    )

    report = describe_processes(DOMAIN, store=store).to_dict()

    states = {entry["name"]: entry["in_effect"] for entry in report["processes"]}
    assert states == {"web": True, "release": True, "worker": False}
    assert report["source"] == "noust.yaml"
    assert any("worker is declared here" in note for note in report["notes"])


def test_a_procfile_is_not_in_effect_for_an_application_deployed_without_reading_it(
    store: NoustStore, code: Path
) -> None:
    write(code, "Procfile", "web: ./bin/server\n")

    report = describe_processes(DOMAIN, store=store)

    assert report.procfile_honoured is False
    assert not report.web_in_effect
    assert any("deployed before its Procfile was read" in note for note in report.notes)


def test_a_procfile_is_in_effect_once_the_application_honours_it(
    store: NoustStore, code: Path
) -> None:
    write(code, "Procfile", "web: ./bin/server\nrelease: ./bin/migrate\n")
    app_build_store.set_applied(DOMAIN, {"procfile": True}, store=store)

    report = describe_processes(DOMAIN, store=store)

    assert report.procfile_honoured is True
    assert report.web_in_effect and report.release_in_effect


def test_the_build_blocks_start_wins_and_the_report_says_so(store: NoustStore, code: Path) -> None:
    write(code, "noust.yaml", "build:\n  start: ./other\nprocesses:\n  web: ./bin/server\n")

    report = describe_processes(DOMAIN, store=store)

    assert not report.web_in_effect
    assert any("web is not used: the build block's start" in note for note in report.notes)


def test_declared_hooks_win_over_the_release_process(store: NoustStore, code: Path) -> None:
    write(
        code,
        "noust.yaml",
        "hooks:\n  pre_deploy:\n    - run: ./one.sh\nprocesses:\n  release: ./bin/migrate\n",
    )

    report = describe_processes(DOMAIN, store=store)

    assert not report.release_in_effect
    assert any("deploy hooks are declared" in note for note in report.notes)


def test_a_procfile_that_cannot_be_used_is_a_problem_not_an_error(
    store: NoustStore, code: Path
) -> None:
    write(code, "Procfile", "web: ./a && ./b\n")

    report = describe_processes(DOMAIN, store=store)

    assert "needs a shell" in report.found.problem
    assert report.to_dict()["problem"] == report.found.problem


def test_a_noust_yaml_that_is_wrong_is_reported_too(store: NoustStore, code: Path) -> None:
    write(code, "noust.yaml", "processes:\n  Web: ./x\n")

    report = describe_processes(DOMAIN, store=store)

    assert "not a process name" in report.found.problem


def test_an_unknown_application(store: NoustStore) -> None:
    with pytest.raises(NoustError):
        describe_processes("nobody.example.com", store=store)


class TestSurfaces:
    @pytest.fixture(autouse=True)
    def cli_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        from noust.core.config import Config

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        yield
        Config.reset_instance()

    def invoke(self, *args: str) -> Any:
        from click.testing import CliRunner

        from noust.cli.app import cli

        return CliRunner().invoke(cli, list(args))

    def test_the_command(self, store: NoustStore, code: Path) -> None:
        write(code, "noust.yaml", "processes:\n  web: ./bin/server --port 80\n  worker: ./jobs\n")

        shown = self.invoke("app", "processes", "show", DOMAIN)
        as_json = self.invoke("app", "processes", "show", DOMAIN, "--json")

        assert shown.exit_code == 0, shown.output
        assert "web (used)" in shown.output and "./bin/server --port 80" in shown.output
        assert "worker (declared)" in shown.output
        body = json.loads(as_json.output)
        assert [entry["name"] for entry in body["processes"]] == ["web", "worker"]

    def test_the_api(self, store: NoustStore, code: Path) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import app_processes
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_processes import ROUTES

        assert ROUTES[("GET", "/api/apps/{domain}/processes")] == Permission.APPS_READ
        write(code, "noust.yaml", "processes:\n  web: ./bin/server\n")
        server = FastAPI()
        install_error_handlers(server)
        server.include_router(app_processes.router, prefix="/api/apps")
        server.dependency_overrides[get_current_session] = lambda: {
            "type": "session",
            "session_id": "s",
            "scope": "admin",
            "name": "alice",
        }
        client = TestClient(server)

        got = client.get(f"/api/apps/{DOMAIN}/processes")

        assert got.status_code == 200, got.text
        assert got.json()["processes"][0]["name"] == "web"
        assert got.json()["processes"][0]["in_effect"] is True
        assert client.get("/api/apps/nope.example.com/processes").status_code == 404
