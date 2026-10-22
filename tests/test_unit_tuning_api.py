# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tuning of an application's unit through the API and the command line (checklist B-57, B-60).

``tests/test_unit_tuning.py`` pins what the options are and how they are changed; here, how they
travel: ``GET /api/apps/{domain}/tuning`` shows them, ``PUT`` queues the change as a job behind
sudo mode and refuses what a unit cannot have before queueing, and ``noust app tuning`` is a
client of the same engine.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import unit_tuning_store as tuning_store
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.deployers import unit_tuning as engine
from noust.web.api import tuning as tuning_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store with one Node application, where the API and the engine look for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(tuning_api, "scoped_store", lambda: ScopedStore(store=instance))
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=3100,
            app_path="/var/www/apps/shop-example-com",
            memory_max_mb=512,
        )
    )
    instance.create_app(
        App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s")
    )
    yield instance
    NoustStore.reset_instance()


def client(*, elevated: bool) -> TestClient:
    """A client for the router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(tuning_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for the job manager, keeping what is queued."""
    jobs: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            jobs.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(tuning_api, "get_job_manager", lambda: Jobs())
    return jobs


def test_the_api_shows_how_an_application_is_tuned(store: NoustStore) -> None:
    tuning_store.set_record(DOMAIN, {"cpu_weight": 30}, changed_by="ana", store=store)

    body = client(elevated=False).get(f"/api/apps/{DOMAIN}/tuning").json()

    assert body["configured"] is True and body["eligible"] is True
    assert body["options"]["cpu_weight"] == 30
    assert body["directives"] == ["CPUWeight=30"]
    assert "NET_BIND_SERVICE" in body["capabilities"]
    assert body["gpu_presets"]["nvidia"][0] == "char-nvidia"
    assert body["cpus"] >= 1


def test_an_application_never_tuned_says_so_and_an_unknown_one_is_404(store: NoustStore) -> None:
    body = client(elevated=False).get(f"/api/apps/{DOMAIN}/tuning").json()

    assert (body["configured"], body["directives"]) == (False, [])
    assert client(elevated=False).get("/api/apps/nope.example.com/tuning").status_code == 404


def test_the_api_says_why_a_stack_has_none(store: NoustStore) -> None:
    body = client(elevated=False).get("/api/apps/stack.example.com/tuning").json()

    assert body["eligible"] is False
    assert "Docker" in body["reason"]


def test_a_change_needs_sudo_mode_and_is_a_job(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    body = {
        "options": {
            "memory_high_mb": 256,
            "capabilities": ["NET_BIND_SERVICE"],
            "devices": [{"path": "char-drm", "mode": "rw"}],
        },
        "restart": True,
    }

    refused = client(elevated=False).put(f"/api/apps/{DOMAIN}/tuning", json=body)
    accepted = client(elevated=True).put(f"/api/apps/{DOMAIN}/tuning", json=body)

    assert refused.status_code == 403
    assert accepted.status_code == 202
    [job] = queued
    assert job["job_type"].value == "tuning"
    assert job["kwargs"] == {
        "domain": DOMAIN,
        "options": {
            "memory_high_mb": 256,
            "capabilities": ["NET_BIND_SERVICE"],
            "devices": [{"path": "char-drm", "mode": "rw"}],
        },
        "restart": True,
        "actor": job["actor"],
    }
    assert job["func"] is tuning_api.tuning_job


def test_an_empty_set_is_queued_as_removing_the_tuning(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/tuning", json={"options": {}})

    assert response.status_code == 202
    assert queued[0]["kwargs"]["options"] == {}


def test_a_stack_is_refused_before_anything_is_queued(
    store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    response = client(elevated=True).put(
        "/api/apps/stack.example.com/tuning", json={"options": {"cpu_weight": 5}}
    )

    assert response.status_code == 400
    assert queued == []


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"cpu_weight": 0}, "cpu_weight"),
        ({"capabilities": ["SYS_ADMIN"]}, "capabilities"),
        ({"devices": [{"path": "/etc/shadow", "mode": "r"}]}, "devices"),
        ({"allowed_cpus": "9999"}, "allowed_cpus"),
        ({"memory_high_mb": 4096}, "memory_high_mb"),
    ],
)
def test_what_a_unit_cannot_have_is_refused_before_anything_is_queued(
    store: NoustStore, queued: list[dict[str, Any]], options: dict[str, Any], field: str
) -> None:
    response = client(elevated=True).put(f"/api/apps/{DOMAIN}/tuning", json={"options": options})

    assert response.status_code == 400
    assert field in response.json()["fields"]
    assert queued == []


def test_the_job_calls_the_engine_with_who_asked(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def fake(domain: str, options: dict[str, Any], **kwargs: Any) -> Any:
        calls.append((domain, options, kwargs))
        return engine.TuningChange(domain=domain, options=options, units=("shop",), restarted=True)

    monkeypatch.setattr(engine, "set_tuning", fake)

    result = tuning_api.tuning_job(DOMAIN, {"cpu_weight": 7}, True, "alice")

    assert calls[0][0] == DOMAIN and calls[0][1] == {"cpu_weight": 7}
    assert calls[0][2]["actor"] == "alice" and calls[0][2]["restart"] is True
    assert result == {
        "domain": DOMAIN,
        "options": {"cpu_weight": 7},
        "units": ["shop"],
        "restarted": True,
    }


def test_the_routes_are_the_application_managers() -> None:
    from noust.web.permissions.routes_hardening import ROUTES

    assert ROUTES[("PUT", "/api/apps/{domain}/tuning")] == "apps.manage"
    assert ROUTES[("GET", "/api/apps/{domain}/tuning")] == "apps.read"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_shows_the_status_as_json(store: NoustStore) -> None:
    tuning_store.set_record(DOMAIN, {"io_weight": 40}, changed_by="ana", store=store)

    result = CliRunner().invoke(root_cli, ["--json", "app", "tuning", "status", DOMAIN])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["options"] == {"io_weight": 40} and body["directives"] == ["IOWeight=40"]


def test_the_status_in_words_lists_each_option_and_the_lines(store: NoustStore) -> None:
    tuning_store.set_record(
        DOMAIN,
        {"cpu_weight": 30, "devices": [{"path": "char-drm", "mode": "rw"}]},
        changed_by="ana",
        store=store,
    )

    result = CliRunner().invoke(root_cli, ["app", "tuning", "status", DOMAIN])

    assert result.exit_code == 0, result.output
    assert "cpu_weight" in result.output and "char-drm:rw" in result.output
    assert "CPUWeight=30" in result.output and "DeviceAllow=char-drm rw" in result.output


def test_the_status_of_an_application_never_tuned_points_at_set(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["app", "tuning", "status", DOMAIN])

    assert result.exit_code == 0, result.output
    assert f"noust app tuning set {DOMAIN}" in result.output


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Stand in for the engine's change, keeping what was asked."""
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def fake(domain: str, options: dict[str, Any], **kwargs: Any) -> Any:
        calls.append((domain, dict(options), kwargs))
        return engine.TuningChange(
            domain=domain, options=options, units=("shop",), restarted=kwargs["restart"]
        )

    monkeypatch.setattr(engine, "set_tuning", fake)
    return calls


def test_set_lays_what_is_typed_over_what_the_unit_has(
    store: NoustStore, applied: list[Any]
) -> None:
    tuning_store.set_record(
        DOMAIN, {"cpu_weight": 30, "io_weight": 40}, changed_by="ana", store=store
    )

    result = CliRunner().invoke(
        root_cli,
        [
            "--json",
            "app",
            "tuning",
            "set",
            DOMAIN,
            "--memory-high",
            "256",
            "--cpu-weight",
            "60",
            "--cap",
            "NET_BIND_SERVICE",
            "--memlock",
            "infinity",
            "--no-core-dumps",
        ],
    )

    assert result.exit_code == 0, result.output
    [(domain, options, kwargs)] = applied
    assert domain == DOMAIN
    assert options == {
        "cpu_weight": 60,
        "io_weight": 40,
        "memory_high_mb": 256,
        "capabilities": ["NET_BIND_SERVICE"],
        "limit_memlock_mb": "infinity",
        "core_dumps": False,
    }
    assert kwargs["restart"] is True


def test_set_can_remove_one_option_and_leave_the_rest(
    store: NoustStore, applied: list[Any]
) -> None:
    tuning_store.set_record(
        DOMAIN, {"cpu_weight": 30, "io_weight": 40}, changed_by="ana", store=store
    )

    result = CliRunner().invoke(
        root_cli, ["--json", "app", "tuning", "set", DOMAIN, "--unset", "cpu_weight"]
    )

    assert result.exit_code == 0, result.output
    assert applied[0][1] == {"io_weight": 40}


def test_a_gpu_is_the_device_groups_its_vendor_needs(store: NoustStore, applied: list[Any]) -> None:
    result = CliRunner().invoke(
        root_cli,
        ["--json", "app", "tuning", "set", DOMAIN, "--gpu", "nvidia", "--device", "/dev/kvm:r"],
    )

    assert result.exit_code == 0, result.output
    assert applied[0][1]["devices"] == [
        "/dev/kvm:r",
        "char-nvidia:rw",
        "char-nvidia-uvm:rw",
        "char-nvidia-caps:rw",
    ]


def test_a_gpu_that_is_not_a_preset_is_a_usage_error_naming_the_ones_there_are(
    store: NoustStore, applied: list[Any]
) -> None:
    result = CliRunner().invoke(root_cli, ["app", "tuning", "set", DOMAIN, "--gpu", "tpu"])

    assert result.exit_code != 0
    assert "nvidia" in result.output and applied == []


def test_an_option_that_does_not_exist_cannot_be_unset(
    store: NoustStore, applied: list[Any]
) -> None:
    result = CliRunner().invoke(root_cli, ["app", "tuning", "set", DOMAIN, "--unset", "shm"])

    assert result.exit_code != 0 and applied == []


def test_a_stack_is_a_usage_error(store: NoustStore, applied: list[Any]) -> None:
    result = CliRunner().invoke(
        root_cli, ["app", "tuning", "set", "stack.example.com", "--cpu-weight", "5", "--yes"]
    )

    assert result.exit_code != 0 and "cannot be tuned" in result.output and applied == []


def test_no_restart_is_passed_on_and_said(store: NoustStore, applied: list[Any]) -> None:
    result = CliRunner().invoke(
        root_cli, ["app", "tuning", "set", DOMAIN, "--cpu-weight", "5", "--no-restart"]
    )

    assert result.exit_code == 0, result.output
    assert applied[0][2]["restart"] is False
    assert "was not restarted" in result.output


def test_clear_sets_nothing(store: NoustStore, applied: list[Any]) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "tuning", "clear", DOMAIN])

    assert result.exit_code == 0, result.output
    assert applied[0][1] == {}


def test_a_restart_asks_first_unless_told_not_to(store: NoustStore, applied: list[Any]) -> None:
    result = CliRunner().invoke(
        root_cli, ["app", "tuning", "set", DOMAIN, "--cpu-weight", "5"], input="n\n"
    )

    assert result.exit_code != 0 and applied == []
