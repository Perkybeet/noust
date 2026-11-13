# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a stack that already runs to Noust, on the central of a NAS and on a server.

The first thing the owner of a NAS wants is to take the stacks he already runs there under Noust. On a
server adopting registers the stack and writes a unit that is enabled and not started; the central of a NAS
has no systemd and no web server, so what keeps the stack alive is Docker's own restart policy, and what
reaches it is a port of the machine. These tests hold the two together: the server does exactly what it
did, and a container host registers the stack without a unit, without looking for a site, with the compose
file and the project recorded where the unit used to record them, and says before anything is done what
would not come back after the NAS restarts, which Noust does not change because the document is somebody's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core import app_compose_store
from noust.core.config import Config
from noust.core.docker.models import NOBODY
from noust.core.docker.restart_loss import Lost
from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.deployers import compose_adopt
from noust.managers.nginx_manager import NginxManager
from tests.support.compose_adopt import (  # noqa: F401
    DOMAIN,
    PROJECT,
    UNIT,
    Docker,
    adopt,
    adopt_client,
    docker,
    root,
    store,
    units,
    web,
)

# See tests/test_compose_adopt.py: a fixture imported from the support module is found by name.
# ruff: noqa: F811


@pytest.fixture
def a_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def a_container_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docker: Docker
) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    docker.script(["docker", "info", "--format", "{{.Name}}"], stdout="nas-oficina\n")
    # What the machine has: no nginx. A test that builds a web server manager here fails on it.
    monkeypatch.setattr(
        compose_adopt,
        "NginxManager",
        lambda *a, **k: pytest.fail("a container host has no web server to look for a site in"),
    )
    yield
    Config.reset_instance()


def no_systemd(docker: Docker) -> bool:
    return not any(call[0] in ("systemctl", "journalctl") for call in docker.calls)


# -- A server is exactly what it was ------------------------------------------------------


def test_on_a_server_the_unit_is_made_enabled_and_not_started(
    a_server: None,
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
) -> None:
    result = adopt(root, web)

    assert result.adopted and result.unit == UNIT
    assert result.plan.kept_by == "unit"
    assert result.plan.address is None and result.plan.restart_loss == ()
    assert (units / f"{UNIT}.service").exists()
    assert ("systemctl", "enable", UNIT) in docker.calls or any(
        call[:2] == ("systemctl", "enable") for call in docker.calls
    )
    assert not any(call[:2] == ("systemctl", "start") for call in docker.calls)
    assert result.plan.site == "proggest"


# -- A container host registers the stack without a unit and without a site -----------------


def test_on_a_container_host_the_stack_is_registered_with_no_unit_and_no_site(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore, units: Path
) -> None:
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    result = adopt(root, None)

    assert result.adopted and result.unit == ""
    assert result.plan.kept_by == "docker"
    row = store.get_app(DOMAIN)
    assert row is not None
    assert (row.webserver, row.ssl_enabled, row.compose_project) == ("", False, PROJECT)
    assert no_systemd(docker) and list(units.iterdir()) == []
    assert store.get_service(UNIT) is None
    # What the unit used to say of the stack (its file) is where an update reads it now.
    options = app_compose_store.get_options(DOMAIN)
    assert options.compose_file == "docker-compose.prod.yml"
    # Nothing of the stack was touched.
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert docker.mutating() == []


def test_on_a_container_host_the_address_is_the_machines_and_no_site_is_looked_for(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore
) -> None:
    plan = compose_adopt.plan_adoption(DOMAIN, root)

    assert plan.site is None and plan.site_name is None and plan.ssl is False
    assert plan.address == "http://nas-oficina:3001"
    assert not any("sites-available" in warning for warning in plan.warnings)


def test_a_stack_published_on_loopback_only_is_said_to_be_unreachable_from_the_network(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore
) -> None:
    plan = compose_adopt.plan_adoption(DOMAIN, root)

    said = [warning for warning in plan.warnings if "127.0.0.1" in warning]
    assert said, plan.warnings
    assert "not from your network" in said[0]


def test_a_stack_published_on_every_address_is_not_said_to_be_loopback_only(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore
) -> None:
    compose = root / "docker-compose.prod.yml"
    compose.write_text(compose.read_text().replace("'127.0.0.1:", "'"))
    docker.dry_run = docker.dry_run  # the rehearsal does not read the file's text

    plan = compose_adopt.plan_adoption(DOMAIN, root)

    assert not any("127.0.0.1" in warning for warning in plan.warnings)


def test_what_would_not_come_back_after_the_nas_restarts_is_said_and_not_changed(
    a_container_host: None,
    root: Path,
    docker: Docker,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lost = (
        Lost("proggest-backend", NOBODY, "policy", "no", "bring_first", PROJECT),
        Lost("proggest-redis", NOBODY, "stopped", "unless-stopped", "bring_first", PROJECT),
        Lost("another-web", NOBODY, "policy", "no", "set_policy", "another"),
    )
    monkeypatch.setattr(compose_adopt.DockerGate, "restart_loss", lambda self: lost, raising=True)
    before = (root / "docker-compose.prod.yml").read_bytes()

    plan = compose_adopt.plan_adoption(DOMAIN, root)

    assert [(item.container, item.policy, item.why) for item in plan.restart_loss] == [
        ("proggest-backend", "no", "policy"),
        ("proggest-redis", "unless-stopped", "stopped"),
    ]
    text = " ".join(plan.warnings)
    assert "proggest-backend" in text and "restart: unless-stopped" in text
    assert "another-web" not in text
    assert (root / "docker-compose.prod.yml").read_bytes() == before


def test_a_server_does_not_say_what_a_restart_would_lose_because_its_unit_brings_the_stack_back(
    a_server: None,
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        compose_adopt.DockerGate,
        "restart_loss",
        lambda self: pytest.fail("a server's unit starts the stack again"),
    )

    plan = compose_adopt.plan_adoption(DOMAIN, root, web=web)

    assert plan.restart_loss == ()


# -- The folder: seen, and readable --------------------------------------------------------


def test_a_folder_the_central_does_not_see_says_what_to_mount_where(
    a_container_host: None, docker: Docker, store: NoustStore, tmp_path: Path
) -> None:
    missing = tmp_path / "volume1" / "docker" / "shop"

    with pytest.raises(DeploymentError) as caught:
        compose_adopt.plan_adoption("shop.example.com", missing)

    assert "does not see" in caught.value.message
    assert f"- {missing}:{missing}" in (caught.value.details or "")


def test_on_a_server_a_missing_folder_says_what_it_always_said(
    a_server: None, docker: Docker, store: NoustStore, tmp_path: Path
) -> None:
    with pytest.raises(DeploymentError) as caught:
        compose_adopt.plan_adoption("shop.example.com", tmp_path / "nowhere")

    assert caught.value.message.endswith("does not exist")


def test_a_folder_the_central_s_user_cannot_read_is_said_before_compose_is_asked(
    a_container_host: None,
    root: Path,
    docker: Docker,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        compose_adopt.os, "access", lambda path, mode: not str(path).endswith("prod.yml")
    )

    with pytest.raises(DeploymentError) as caught:
        compose_adopt.plan_adoption(DOMAIN, root)

    assert "cannot read" in caught.value.message
    assert "docker-compose.prod.yml" in caught.value.message
    assert not any("--dry-run" in call for call in docker.calls)


# -- The three doors -----------------------------------------------------------------------


def test_the_command_adopts_on_a_container_host_and_says_docker_keeps_it_up(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore, units: Path
) -> None:
    from noust.cli.app import cli as root_cli

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root)], input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert "Docker keeps it running" in result.output
    assert "http://nas-oficina:3001" in result.output
    assert ".service" not in result.output
    assert store.get_app(DOMAIN) is not None and no_systemd(docker)


def test_the_command_still_says_the_unit_on_a_server(
    a_server: None,
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli.app import cli as root_cli

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root)], input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert f"{UNIT}.service" in result.output


def test_the_default_web_server_manager_is_none_where_there_is_no_site(
    a_container_host: None,
) -> None:
    assert compose_adopt._default_web() is None


def test_the_api_adopts_on_a_container_host(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore, units: Path
) -> None:
    client = adopt_client(elevated=True)

    preview = client.post(
        "/api/apps/adopt", json={"domain": DOMAIN, "path": str(root), "preview": True}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["adopted"], body["unit"], body["kept_by"]) == (False, "", "docker")
    assert body["address"] == "http://nas-oficina:3001"
    assert store.get_app(DOMAIN) is None

    done = client.post("/api/apps/adopt", json={"domain": DOMAIN, "path": str(root)})
    assert done.status_code == 200, done.text
    assert done.json()["adopted"] is True
    assert store.get_app(DOMAIN) is not None and no_systemd(docker)


def test_the_bring_of_the_docker_zone_registers_the_stack_without_a_unit(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore, units: Path
) -> None:
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.owners import Registry
    from noust.managers.docker_bring import StackBringer
    from tests.support.docker_machine import DockerMachine

    box = DockerMachine()
    for name, service in (("proggest-backend", "backend"), ("proggest-redis", "redis")):
        box.box(
            name,
            project=PROJECT,
            service=service,
            working_dir=str(root),
            config_files=str(root / "docker-compose.prod.yml"),
            policy="unless-stopped",
        )
    gate = DockerGate(box, store=store, registry=lambda: Registry(), disk=lambda path: None)
    # The zone lists the stack, and adopting reads and proves it through the runner it is given.
    bringer = StackBringer(gate, store=store, runner=docker)

    preview = bringer.preview(PROJECT, name=DOMAIN)

    assert preview.to_dict()["unit"] == "" and preview.to_dict()["kept_by"] == "docker"
    assert store.get_app(DOMAIN) is None
    done = bringer.bring(PROJECT, name=DOMAIN)
    assert done.adopted is True
    assert store.get_app(DOMAIN) is not None
    assert no_systemd(docker) and not any(
        call[0] in ("systemctl", "journalctl") for call in box.calls
    )


def test_the_json_the_command_prints_carries_what_keeps_the_stack_up(
    a_container_host: None, root: Path, docker: Docker, store: NoustStore, units: Path
) -> None:
    from noust.cli.app import cli as root_cli

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root), "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    body: dict[str, Any] = json.loads(result.output.strip().splitlines()[-1])
    assert body["kept_by"] == "docker" and body["unit"] == ""
    assert body["address"] == "http://nas-oficina:3001"
