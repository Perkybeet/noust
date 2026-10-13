# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes of an application through the API and the command line (checklist B-67, B-68).

``tests/test_app_processes.py`` pins what a process is and how it is judged;
here, how it travels: ``GET /api/apps/{domain}/processes`` lists them, ``PUT``
defines one behind sudo mode, ``POST .../scale`` runs another number of copies,
``DELETE`` removes one, and ``noust app process`` is a client of the same manager.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.app_process_store import ProcessRecord
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.managers import app_processes
from noust.managers.app_processes import ProcessChange, ProcessStatus
from noust.web.api import processes as processes_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.auth import actor_label

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", port=3100, app_path="/var/www/apps/shop-example-com")
    )
    monkeypatch.setattr(processes_api, "scoped_store", lambda: ScopedStore(store=instance))
    yield instance
    NoustStore.reset_instance()


class FakeManager:
    """Stands in for the process manager and keeps what it was asked."""

    calls: list[tuple[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    def status(self, domain: str) -> list[ProcessStatus]:
        return [
            ProcessStatus(
                record=ProcessRecord(name="worker", command="node worker.js", instances=2),
                base="shop-example-com-worker",
                instances=(
                    app_processes.InstanceState(
                        "shop-example-com-worker@1", 1, "active", "running"
                    ),
                    app_processes.InstanceState(
                        "shop-example-com-worker@2", 2, "failed", "failed", 3
                    ),
                ),
                installed=True,
            )
        ]

    def set_process(self, domain: str, record: ProcessRecord, *, actor: str) -> ProcessChange:
        FakeManager.calls.append(("set", (domain, record, actor)))
        return ProcessChange(
            domain=domain, name=record.name, instances=record.instances, started=("a@1",)
        )

    def scale(self, domain: str, name: str, instances: int, *, actor: str) -> ProcessChange:
        FakeManager.calls.append(("scale", (domain, name, instances, actor)))
        return ProcessChange(domain=domain, name=name, instances=instances, stopped=("a@3",))

    def remove(self, domain: str, name: str, *, actor: str) -> ProcessChange:
        FakeManager.calls.append(("remove", (domain, name, actor)))
        return ProcessChange(domain=domain, name=name, stopped=("a@1",), removed=True)

    def logs(self, domain: str, name: str, *, lines: int) -> str:
        FakeManager.calls.append(("logs", (domain, name, lines)))
        return "worker started\n"


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> type[FakeManager]:
    FakeManager.calls = []
    monkeypatch.setattr(processes_api, "ProcessManager", FakeManager)
    return FakeManager


def client(*, elevated: bool) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(processes_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


def test_the_api_lists_the_processes_with_their_copies(
    store: NoustStore, manager: type[FakeManager]
) -> None:
    body = client(elevated=False).get(f"/api/apps/{DOMAIN}/processes").json()

    assert body["total"] == 1
    [process] = body["processes"]
    assert (process["name"], process["instances"], process["running"]) == ("worker", 2, 1)
    assert process["copies"][1]["state"] == "failed" and process["copies"][1]["restarts"] == 3
    assert process["unit"] == "shop-example-com-worker@"


def test_an_unknown_application_reads_as_absent(
    store: NoustStore, manager: type[FakeManager]
) -> None:
    assert client(elevated=False).get("/api/apps/nope.example.com/processes").status_code == 404


def test_defining_a_process_needs_sudo_mode_and_says_who_asked(
    store: NoustStore, manager: type[FakeManager]
) -> None:
    body = {"command": "node worker.js", "instances": 2, "memory_max_mb": 256}

    refused = client(elevated=False).put(f"/api/apps/{DOMAIN}/processes/worker", json=body)
    done = client(elevated=True).put(f"/api/apps/{DOMAIN}/processes/worker", json=body)

    assert refused.status_code == 403
    assert done.status_code == 200 and done.json()["started"] == ["a@1"]
    [(kind, (domain, record, actor))] = manager.calls
    assert (kind, domain) == ("set", DOMAIN)
    assert actor == actor_label(
        {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    )
    assert (record.name, record.command, record.instances, record.memory_max_mb) == (
        "worker",
        "node worker.js",
        2,
        256,
    )


@pytest.mark.parametrize("body", [{"command": ""}, {"command": "x", "instances": 33}, {}])
def test_a_definition_that_is_not_one_is_refused_before_the_manager_hears_of_it(
    store: NoustStore, manager: type[FakeManager], body: dict[str, Any]
) -> None:
    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/processes/worker", json=body)

    assert response.status_code == 422
    assert manager.calls == []


def test_scaling_and_removing_need_sudo_mode(store: NoustStore, manager: type[FakeManager]) -> None:
    assert (
        client(elevated=False)
        .post(f"/api/apps/{DOMAIN}/processes/worker/scale", json={"instances": 3})
        .status_code
        == 403
    )
    assert client(elevated=False).delete(f"/api/apps/{DOMAIN}/processes/worker").status_code == 403

    scaled = client(elevated=True).post(
        f"/api/apps/{DOMAIN}/processes/worker/scale", json={"instances": 3}
    )
    removed = client(elevated=True).delete(f"/api/apps/{DOMAIN}/processes/worker")

    assert scaled.json()["instances"] == 3 and scaled.json()["stopped"] == ["a@3"]
    assert removed.json()["removed"] is True
    assert [kind for kind, _ in manager.calls] == ["scale", "remove"]


def test_the_journal_is_read_with_a_bounded_number_of_lines(
    store: NoustStore, manager: type[FakeManager]
) -> None:
    ok = client(elevated=False).get(f"/api/apps/{DOMAIN}/processes/worker/logs?lines=50")
    too_many = client(elevated=False).get(f"/api/apps/{DOMAIN}/processes/worker/logs?lines=99999")

    assert ok.json()["logs"] == "worker started\n"
    assert too_many.status_code == 422


def test_reading_is_reading_the_application_and_changing_is_managing_it() -> None:
    from noust.web.permissions.routes_processes import ROUTES

    assert ROUTES[("GET", "/api/apps/{domain}/processes")] == "apps.read"
    assert ROUTES[("GET", "/api/apps/{domain}/processes/{name}/logs")] == "apps.read"
    for key in (
        ("PUT", "/api/apps/{domain}/processes/{name}"),
        ("POST", "/api/apps/{domain}/processes/{name}/scale"),
        ("DELETE", "/api/apps/{domain}/processes/{name}"),
    ):
        assert ROUTES[key] == "apps.manage"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_manager(monkeypatch: pytest.MonkeyPatch) -> type[FakeManager]:
    FakeManager.calls = []
    monkeypatch.setattr("noust.cli.commands.app_process.ProcessManager", FakeManager)
    return FakeManager


def test_the_command_lists_as_json(store: NoustStore, cli_manager: type[FakeManager]) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "process", "list", DOMAIN])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["processes"][0]["name"] == "worker"


def test_the_command_defines_a_process(store: NoustStore, cli_manager: type[FakeManager]) -> None:
    result = CliRunner().invoke(
        root_cli,
        [
            "app",
            "process",
            "set",
            DOMAIN,
            "worker",
            "node worker.js",
            "--instances",
            "3",
            "--memory",
            "256",
        ],
    )

    assert result.exit_code == 0, result.output
    [(_, (_, record, _))] = cli_manager.calls
    assert (record.command, record.instances, record.memory_max_mb) == ("node worker.js", 3, 256)


def test_the_command_scales_and_refuses_a_count_out_of_range(
    store: NoustStore, cli_manager: type[FakeManager]
) -> None:
    ok = CliRunner().invoke(root_cli, ["app", "process", "scale", DOMAIN, "worker", "4"])
    bad = CliRunner().invoke(root_cli, ["app", "process", "scale", DOMAIN, "worker", "99"])

    assert ok.exit_code == 0, ok.output
    assert bad.exit_code == 2
    assert [kind for kind, _ in cli_manager.calls] == ["scale"]


def test_the_command_asks_before_removing(
    store: NoustStore, cli_manager: type[FakeManager]
) -> None:
    no = CliRunner().invoke(root_cli, ["app", "process", "remove", DOMAIN, "worker"], input="n\n")
    yes = CliRunner().invoke(root_cli, ["app", "process", "remove", DOMAIN, "worker", "--yes"])

    assert no.exit_code != 0
    assert yes.exit_code == 0, yes.output
    assert [kind for kind, _ in cli_manager.calls] == ["remove"]


def test_the_command_prints_the_journal(store: NoustStore, cli_manager: type[FakeManager]) -> None:
    result = CliRunner().invoke(root_cli, ["app", "process", "logs", DOMAIN, "worker", "-n", "20"])

    assert result.exit_code == 0 and "worker started" in result.output
    assert cli_manager.calls == [("logs", (DOMAIN, "worker", 20))]


def test_stopping_the_application_goes_to_the_manager_too(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    acted: list[tuple[str, str]] = []

    class Processes:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def control(self, domain: str, action: str) -> list[str]:
            acted.append((domain, action))
            return ["shop-example-com-worker@1"]

    class Services:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get_status(self, name: str) -> dict[str, Any]:
            return {"exists": True}

        def stop(self, name: str) -> bool:
            return True

    from noust.web.api import apps as apps_api

    monkeypatch.setattr(apps_api, "ProcessManager", Processes)
    monkeypatch.setattr(apps_api, "ServiceManager", Services)
    monkeypatch.setattr(apps_api, "scoped_store", lambda: ScopedStore(store=store))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(apps_api.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "s", "scope": "admin"}

    response = TestClient(app).post(f"/api/apps/{DOMAIN}/stop")

    assert response.status_code == 200, response.text
    assert acted == [(DOMAIN, "stop")]
