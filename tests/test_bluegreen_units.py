# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for what blue/green changes in the managers, the CLI and the API.

The service and nginx managers run for real over temporary directories with
the fake runner, so what is pinned is what reaches systemd, nginx and the
disk:

- The template and its instances are the application's units everywhere
  (``app_units``, ``managed_units``, ownership), and the application's own
  name resolves to the instance that serves.
- Limits written to an instance land in the template, for both; deleting an
  instance leaves its sibling's template alone.
- The site of an application in the mode proxies to its upstream; every
  other site renders exactly as before.
- Deleting an application removes its instances, template and upstream.
- The diagnosis says which instance serves and catches an upstream that
  points elsewhere.
- ``wasm app zero-downtime`` and ``/api/apps/{d}/zero-downtime`` are thin.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wasm.core.runner import FakeRunner, set_runner
from wasm.core.store import App, Service, WASMStore, get_store
from wasm.core.utils import domain_to_app_name
from wasm.managers.nginx_manager import NginxManager
from wasm.managers.service_manager import WASM_UNIT_MARKER, ResourceLimits, ServiceManager
from wasm.managers.webserver import NGINX_BACKEND

DOMAIN = "bg.example.com"
BASE = domain_to_app_name(DOMAIN)
PORT = 3100


@pytest.fixture
def store() -> Iterator[WASMStore]:
    """The process-wide store, at the location conftest redirects it to."""
    WASMStore.reset_instance()
    instance = get_store()
    yield instance
    WASMStore.reset_instance()


@pytest.fixture
def unit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private managed unit directory, and nothing in the system ones."""
    managed = tmp_path / "etc/systemd/system"
    managed.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed,))
    return managed


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
            upstreams_dir=tmp_path / "nginx/wasm-upstreams",
        )
    )


def bg_app(store: WASMStore, root: Path, *, color: str | None = "green", on: bool = True) -> App:
    """Record an application on releases, in zero-downtime mode unless asked."""
    row = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=PORT,
            app_path=str(root),
            layout="releases",
            webserver="nginx",
        )
    )
    store.create_service(
        Service(
            app_id=row.id,
            name=BASE,
            unit_file=f"/etc/systemd/system/{BASE}.service",
            working_directory=str(root / "current"),
            command="/usr/bin/npm run start",
            environment={"PORT": str(PORT), "NODE_ENV": "production"},
        )
    )
    if on:
        store.set_zero_downtime(DOMAIN, True)
        store.set_active_color(DOMAIN, color)
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


def install_template(root: Path) -> ServiceManager:
    """Write the template the way enabling the mode does."""
    manager = ServiceManager()
    manager.install_instance_template(
        BASE,
        command=f"{root}/colors/%i/venv/bin/python -m gunicorn app:app -b 0.0.0.0:${{PORT}}",
        colors_directory=str(root / "colors"),
        environment={"NODE_ENV": "production"},
        environment_file=str(root / "shared/.env"),
        description=f"WASM: {DOMAIN} (python)",
        limits=ResourceLimits(memory_max_mb=512),
    )
    return manager


# ---------------------------------------------------------------------------
# The template and its instances
# ---------------------------------------------------------------------------


def test_the_template_runs_each_instance_from_its_own_link_and_port(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """WorkingDirectory and the port file are per instance; the port file is read last."""
    root = tmp_path / "apps" / BASE
    install_template(root)

    body = (unit_dir / f"{BASE}@.service").read_text()
    assert WASM_UNIT_MARKER in body
    assert f"WorkingDirectory={root}/colors/%i\n" in body
    shared = body.index(f"EnvironmentFile=-{root}/shared/.env")
    own = body.index(f"EnvironmentFile={root}/colors/%i.env")
    assert shared < own, "the instance's PORT must win over the shared file"
    assert "ExecStart=" + f"{root}/colors/%i/venv/bin/python -m gunicorn" in body
    assert "-b 0.0.0.0:${PORT}" in body
    assert "PORT=" not in body.split("EnvironmentFile")[0].split("# Environment")[1]
    assert "MemoryMax=512M" in body
    assert ("systemctl", "daemon-reload") in runner.calls


def test_both_instances_are_the_applications_units_the_serving_one_first(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """app_units lists both; serving_units only the one whose state is the app's."""
    app = bg_app(store, tmp_path / "apps" / BASE, color="blue")
    manager = ServiceManager()

    assert manager.app_units(app) == [f"{BASE}@blue", f"{BASE}@green"]
    assert manager.serving_units(app) == [f"{BASE}@blue"]
    store.set_active_color(DOMAIN, "green")
    app = store.get_app(DOMAIN)
    assert app is not None
    assert manager.app_units(app) == [f"{BASE}@green", f"{BASE}@blue"]


def test_the_listing_shows_both_instances_even_when_systemd_unloaded_the_idle_one(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """The status bar, the Services page, logs and metrics see both."""
    root = tmp_path / "apps" / BASE
    bg_app(store, root)
    install_template(root)
    runner.script(
        ["systemctl", "list-units"],
        stdout="UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        f"{BASE}@green.service loaded active running WASM\n",
    )

    units = {unit.name: unit for unit in ServiceManager().managed_units()}

    assert units[f"{BASE}@green"].active == "active"
    assert units[f"{BASE}@green"].app == DOMAIN
    assert units[f"{BASE}@blue"].load == "not-loaded"
    assert units[f"{BASE}@blue"].app == DOMAIN
    assert f"{BASE}@" not in units
    assert BASE not in units, "the retired unit is not listed"


def test_an_instance_is_wasms_through_its_template(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """No file of its own: the template's marker is the signal, and it is managed."""
    install_template(tmp_path / "apps" / BASE)

    info = ServiceManager().inspect_unit(f"{BASE}@blue")

    assert info.exists and info.managed
    assert info.path == unit_dir / f"{BASE}@.service"


def test_an_instance_of_a_template_the_system_ships_is_not_wasms(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore, monkeypatch: Any
) -> None:
    """getty@tty1 lives in /usr/lib: a marked copy in /etc does not make it WASM's."""
    distro = tmp_path / "usr/lib/systemd/system"
    distro.mkdir(parents=True)
    (distro / "getty@.service").write_text("[Service]\n")
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (unit_dir, distro))
    (unit_dir / "getty@.service").write_text(f"# {WASM_UNIT_MARKER}\n[Service]\n")

    assert not ServiceManager().inspect_unit("getty@tty1").managed


def test_the_applications_own_name_reaches_the_instance_that_serves(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """wasm restart, the Services page and the logs name the application; green answers."""
    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="green")
    install_template(root)

    ServiceManager().restart(BASE)

    assert ("systemctl", "restart", f"{BASE}@green.service") in runner.calls


def test_limits_on_an_instance_land_in_the_template_for_both(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """One file, so one change; the previous body comes back to put back."""
    install_template(tmp_path / "apps" / BASE)
    manager = ServiceManager()

    previous = manager.set_resource_limits(f"{BASE}@blue", ResourceLimits(cpu_quota_percent=50))

    body = (unit_dir / f"{BASE}@.service").read_text()
    assert "CPUQuota=50%" in body and "MemoryMax" not in body
    assert "MemoryMax=512M" in previous


def test_deleting_an_instance_leaves_the_template_of_its_sibling(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """Stopped and disabled; the template goes only with remove_template."""
    install_template(tmp_path / "apps" / BASE)
    manager = ServiceManager()

    manager.delete_service(f"{BASE}@blue", keep_record=True)

    assert ("systemctl", "stop", f"{BASE}@blue.service") in runner.calls
    assert ("systemctl", "disable", f"{BASE}@blue.service") in runner.calls
    assert (unit_dir / f"{BASE}@.service").exists()
    assert manager.remove_template(BASE)
    assert not (unit_dir / f"{BASE}@.service").exists()


def test_a_template_wasm_did_not_write_is_never_removed(
    runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """The marker is the licence to delete."""
    from wasm.core.exceptions import ServiceError

    (unit_dir / f"{BASE}@.service").write_text("[Service]\nExecStart=/bin/true\n")

    with pytest.raises(ServiceError, match="not generated by WASM"):
        ServiceManager().remove_template(BASE)


def test_creating_the_applications_own_unit_is_not_confused_with_its_instance(
    tmp_path: Path, runner: FakeRunner, unit_dir: Path, store: WASMStore
) -> None:
    """Turning the mode off writes <name>.service while <name> still resolves to green."""
    root = tmp_path / "apps" / BASE
    bg_app(store, root)
    install_template(root)

    ServiceManager().create_service(
        name=BASE, command="/usr/bin/npm run start", working_directory=str(root / "current")
    )

    assert (unit_dir / f"{BASE}.service").is_file()


# ---------------------------------------------------------------------------
# nginx
# ---------------------------------------------------------------------------


def test_the_site_of_an_app_in_the_mode_proxies_to_its_upstream(
    tmp_path: Path, nginx: NginxManager, store: WASMStore
) -> None:
    """The upstream file names one port; the site includes it and names the upstream."""
    bg_app(store, tmp_path / "apps" / BASE)
    assert nginx.write_upstream(DOMAIN, PORT + 1) is None

    nginx.create_site(DOMAIN, template="proxy", context={"port": PORT})

    site = nginx.get_site_config(DOMAIN) or ""
    upstream = nginx.upstream_path(DOMAIN)
    assert f"include {upstream};" in site
    assert "proxy_pass http://wasm_bg_bg_example_com;" in site
    assert f"127.0.0.1:{PORT}" not in site
    assert upstream.read_text().count("server 127.0.0.1:") == 1
    assert nginx.upstream_port(DOMAIN) == PORT + 1


def test_the_site_of_any_other_app_renders_as_before(
    tmp_path: Path, nginx: NginxManager, store: WASMStore
) -> None:
    """Off, or on without an upstream file: the direct proxy_pass."""
    bg_app(store, tmp_path / "apps" / BASE, on=False)
    nginx.write_upstream(DOMAIN, PORT + 1)

    nginx.create_site(DOMAIN, template="proxy", context={"port": PORT})

    site = nginx.get_site_config(DOMAIN) or ""
    assert f"proxy_pass http://127.0.0.1:{PORT};" in site
    assert "include" not in site


def test_an_upstream_is_put_back_exactly_and_removed_with_its_site(
    tmp_path: Path, nginx: NginxManager, store: WASMStore
) -> None:
    """restore_upstream brings back the old file; delete_site takes the upstream too."""
    bg_app(store, tmp_path / "apps" / BASE)
    nginx.write_upstream(DOMAIN, PORT)
    before = nginx.read_upstream(DOMAIN)
    previous = nginx.write_upstream(DOMAIN, PORT + 1)

    nginx.restore_upstream(DOMAIN, previous)

    assert nginx.read_upstream(DOMAIN) == before
    nginx.create_site(DOMAIN, template="proxy", context={"port": PORT})
    nginx.delete_site(DOMAIN)
    assert not nginx.upstream_path(DOMAIN).exists()


def test_an_upstream_is_never_written_through_a_symlink(
    tmp_path: Path, nginx: NginxManager, store: WASMStore
) -> None:
    """A planted link would make root write anywhere."""
    from wasm.core.exceptions import NginxError

    path = nginx.upstream_path(DOMAIN)
    path.parent.mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere", path)

    with pytest.raises(NginxError, match="symlink"):
        nginx.write_upstream(DOMAIN, PORT)


# ---------------------------------------------------------------------------
# Deletion and diagnosis
# ---------------------------------------------------------------------------


def test_deleting_an_app_in_the_mode_removes_instances_template_links_and_upstream(
    tmp_path: Path,
    runner: FakeRunner,
    unit_dir: Path,
    nginx: NginxManager,
    store: WASMStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing of the mode outlives the application."""
    from wasm.deployers import lifecycle

    root = tmp_path / "apps" / BASE
    (root / "colors").mkdir(parents=True)
    bg_app(store, root)
    install_template(root)
    nginx.write_upstream(DOMAIN, PORT + 1)
    nginx.create_site(DOMAIN, template="proxy", context={"port": PORT})
    monkeypatch.setattr(lifecycle, "NginxManager", lambda **kwargs: nginx)
    monkeypatch.setattr(lifecycle, "get_store", lambda: store)

    outcome = lifecycle.delete_app(DOMAIN, remove_certificate=False)

    assert outcome.warnings == ()
    for color in ("blue", "green"):
        assert ("systemctl", "stop", f"{BASE}@{color}.service") in runner.calls
    assert not (unit_dir / f"{BASE}@.service").exists()
    assert not nginx.upstream_path(DOMAIN).exists()
    assert not root.exists()
    assert store.get_app(DOMAIN) is None


def roomy_disk(path: str) -> SimpleNamespace:
    """A disk with room to spare."""
    return SimpleNamespace(total=100, used=1, free=99)


def test_the_diagnosis_names_the_serving_instance_and_an_upstream_that_points_elsewhere(
    tmp_path: Path,
    runner: FakeRunner,
    unit_dir: Path,
    nginx: NginxManager,
    store: WASMStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Green serves on 3101 but nginx proxies to 3100: that is the cause, first."""
    from wasm.managers import diagnose as diagnose_module

    root = tmp_path / "apps" / BASE
    bg_app(store, root, color="green")
    install_template(root)
    nginx.write_upstream(DOMAIN, PORT)
    monkeypatch.setattr(diagnose_module, "NginxManager", lambda **kwargs: nginx)

    result = diagnose_module.diagnose(
        DOMAIN,
        runner=runner,
        store=store,
        http_get=lambda url, headers: (200, None),
        disk_usage=roomy_disk,  # type: ignore[arg-type]
    )

    names = [check.name for check in result.checks]
    assert names[0] == "blue_green"
    assert result.checks[0].status == "fail"
    assert result.verdict == "down"
    assert result.probable_cause is not None and "3100" in result.probable_cause
    assert "green" in result.probable_cause

    nginx.write_upstream(DOMAIN, PORT + 1)
    result = diagnose_module.diagnose(
        DOMAIN,
        runner=runner,
        store=store,
        http_get=lambda url, headers: (200, None),
        disk_usage=roomy_disk,  # type: ignore[arg-type]
    )
    assert result.checks[0].status == "ok"
    assert result.checks[0].summary.startswith("green serves")
    assert any(
        call[:2] == ("journalctl", "-u") and call[2] == f"{BASE}@green.service"
        for call in runner.calls
    ), "the journal read is the serving instance's"


# ---------------------------------------------------------------------------
# CLI and API
# ---------------------------------------------------------------------------


@pytest.fixture
def real_runner_after() -> Iterator[None]:
    """--dry-run installs a rehearsing runner process-wide; put the default back."""
    yield
    set_runner(None)


def test_the_cli_shows_the_mode_as_json(
    tmp_path: Path, store: WASMStore, real_runner_after: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off and eligible: the console and scripts read the same fields."""
    from wasm.cli.app import cli as root_cli

    root = tmp_path / "apps" / BASE
    bg_app(store, root, on=False)

    result = CliRunner().invoke(root_cli, ["app", "zero-downtime", DOMAIN, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["enabled"] is False and payload["eligible"] is True
    assert payload["instances"] == []
    assert payload["drain_seconds"] == 10


def test_the_cli_rehearses_turning_it_on(
    tmp_path: Path, store: WASMStore, real_runner_after: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run checks the application and changes nothing."""
    from wasm.cli.app import cli as root_cli
    from wasm.deployers import bluegreen

    monkeypatch.setattr(bluegreen, "is_port_available", lambda port: True)
    bg_app(store, tmp_path / "apps" / BASE, on=False)

    result = CliRunner().invoke(
        root_cli, ["--json", "--dry-run", "app", "zero-downtime", DOMAIN, "on", "--drain", "3"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["rehearsed"] is True and payload["enabled"] is True
    assert payload["drain_seconds"] == 3
    row = store.get_app(DOMAIN)
    assert row is not None and not row.zero_downtime


@pytest.fixture
def api(store: WASMStore, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[dict]]:
    """The router with authentication stubbed, and the jobs it queues."""
    from wasm.web.api import zero_downtime as api_module
    from wasm.web.api.auth import get_current_session
    from wasm.web.api.deps import install_error_handlers, require_elevated

    queued: list[dict[str, Any]] = []

    class Job:
        id = "job1"
        status = type("S", (), {"value": "pending"})()

        def to_dict(self) -> dict[str, Any]:
            return {"id": self.id}

    class Jobs:
        def create_job(self, **kwargs: Any) -> Job:
            queued.append(kwargs)
            return Job()

    monkeypatch.setattr(api_module, "get_job_manager", lambda: Jobs())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api_module.router, prefix="/api/apps")
    session = {"sid": "t", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app, raise_server_exceptions=False), queued


def test_the_api_reads_the_mode_and_queues_the_switch(
    tmp_path: Path, store: WASMStore, api: tuple[TestClient, list[dict]]
) -> None:
    """GET answers at once; PUT is a job, like every long action."""
    client, queued = api
    bg_app(store, tmp_path / "apps" / BASE, on=False)

    read = client.get(f"/api/apps/{DOMAIN}/zero-downtime")
    put = client.put(
        f"/api/apps/{DOMAIN}/zero-downtime", json={"enabled": True, "drain_seconds": 20}
    )

    assert read.status_code == 200
    assert read.json()["enabled"] is False and read.json()["eligible"] is True
    assert put.status_code == 202, put.text
    assert put.json()["job_id"] == "job1"
    assert queued[0]["kwargs"] == {"domain": DOMAIN, "enabled": True, "drain_seconds": 20}


def test_the_api_refuses_what_cannot_run_twice_before_queuing(
    tmp_path: Path, store: WASMStore, api: tuple[TestClient, list[dict]]
) -> None:
    """An in-place application is a 400 with the way forward, and no job."""
    client, queued = api
    row = bg_app(store, tmp_path / "apps" / BASE, on=False)
    row.layout = "inplace"
    store.update_app(row)

    put = client.put(f"/api/apps/{DOMAIN}/zero-downtime", json={"enabled": True})
    missing = client.get("/api/apps/nothing.example.com/zero-downtime")
    drain = client.put(
        f"/api/apps/{DOMAIN}/zero-downtime", json={"enabled": False, "drain_seconds": 999}
    )

    assert put.status_code == 400
    assert "migrate" in (put.json().get("hint") or "")
    assert missing.status_code == 404
    assert drain.status_code == 422
    assert queued == []
