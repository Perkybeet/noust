# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's API and the command line, on a machine where Docker keeps the stack alive.

Start, stop, restart, the logs and the state of an application are asked of whatever keeps it alive, through
the one factory (``noust.managers.supervision``). On a container host that is Docker, so the same routes and
commands that a server answers with ``systemctl`` are answered with ``docker compose``, and nothing is run
through systemd. A server is shown to be what it was by the tests of the API, which are unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.deployers import compose_supervisor
from noust.web.api import apps as apps_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from tests.support.container_support import DOMAIN, Docker, install_fakes, root_of


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


@pytest.fixture(autouse=True)
def a_container_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def stack(
    tmp_path: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> App:
    root = root_of(tmp_path)
    root.mkdir(parents=True)
    (root / "compose.yaml").write_text(
        'services:\n  app:\n    image: nginx\n    ports:\n      - "3001:8080"\n', encoding="utf-8"
    )
    monkeypatch.setattr(compose_supervisor, "get_store", lambda: store)
    monkeypatch.setattr(apps_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(apps_api, "get_store", lambda: store)
    return store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001)
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(apps_api.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "s", "scope": "admin"}
    return TestClient(app)


def stays_off_systemd(docker: Docker) -> bool:
    return not any(call[0] in ("systemctl", "journalctl") for call in docker.calls)


def says(docker: Docker, containers: list[dict[str, Any]]) -> None:
    """Make ``docker compose ps`` report these containers."""
    original = docker._lookup

    def lookup(argv: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(argv, *args, **kwargs)
        if tuple(argv[:2]) == ("docker", "compose") and "ps" in argv and "-q" not in argv:
            return replace(result, stdout="\n".join(json.dumps(item) for item in containers))
        return result

    docker._lookup = lookup  # type: ignore[method-assign]


class TestTheApi:
    def test_stopping_an_application_stops_its_containers(
        self, stack: App, docker: Docker, client: TestClient
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/stop")

        assert response.status_code == 200, response.text
        assert any(call[-1] == "stop" for call in docker.compose_calls() if call) is True
        assert stays_off_systemd(docker)

    def test_restarting_one_recreates_them(
        self, stack: App, docker: Docker, client: TestClient
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/restart")

        assert response.status_code == 200, response.text
        assert any("--force-recreate" in call for call in docker.compose_calls())
        assert stays_off_systemd(docker)

    def test_an_application_that_is_not_there_is_a_404_as_it_is_for_a_unit(
        self, stack: App, docker: Docker, client: TestClient
    ) -> None:
        response = client.post("/api/apps/other.example.com/stop")

        assert response.status_code == 404

    def test_the_state_is_what_docker_says_and_loopback_is_not_asked(
        self, stack: App, docker: Docker, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import app_state

        monkeypatch.setattr(
            app_state,
            "port_answers",
            lambda *a, **k: pytest.fail(
                "a central that is a container has no 127.0.0.1 of the NAS"
            ),
        )
        says(docker, [{"Service": "app", "Name": "app-1", "State": "running"}])

        response = client.get(f"/api/apps/{DOMAIN}")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "running"
        assert stays_off_systemd(docker)

    def test_a_stack_whose_container_died_is_failed(
        self, stack: App, docker: Docker, client: TestClient
    ) -> None:
        says(docker, [{"Service": "app", "Name": "app-1", "State": "exited", "ExitCode": 137}])

        assert client.get(f"/api/apps/{DOMAIN}").json()["status"] == "failed"


class TestTheCommandLine:
    @pytest.mark.parametrize(
        ("verb", "expected"),
        [("stop", "stop"), ("start", "up"), ("restart", "--force-recreate")],
    )
    def test_start_stop_and_restart_are_dockers(
        self, stack: App, docker: Docker, verb: str, expected: str
    ) -> None:
        result = CliRunner().invoke(root_cli, [verb, DOMAIN])

        assert result.exit_code == 0, result.output
        assert any(expected in call for call in docker.compose_calls())
        assert stays_off_systemd(docker)

    def test_what_is_not_there_is_said_the_way_it_always_was(
        self, stack: App, docker: Docker
    ) -> None:
        result = CliRunner().invoke(root_cli, ["restart", "other.example.com"])

        assert result.exit_code == 1
        assert "Service not found" in result.output
