# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker the console server models for the console's Docker zone (``--docker``).

The default machine has no Docker on purpose, so that the Software page offers to install it; the zone
needs one with stacks on it. What is held here is that the flag is off unless asked for, and that the engine
it models answers the way Docker does for what the zone lists, with the applications the store has as owners
of what runs from their directories (the owner of a container is decided from what the store registered, never
from what the container says of itself).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.console_server import docker_host
from scripts.console_server.arguments import parse_args
from scripts.console_server.machine import INSTALLED_PROGRAMS
from scripts.console_server.runner import make_runner

APPS = {
    "shop.example.net": "/var/www/apps/shop.example.net",
    "example.net": "/var/www/apps/example.net",
    "tools.example.net": "/var/www/apps/tools.example.net",
}


@pytest.fixture(autouse=True)
def no_engine_left_behind(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docker_host, "_DOCKER", None)


def test_the_engine_is_off_unless_it_is_asked_for() -> None:
    assert parse_args([]).docker is False
    assert parse_args(["--docker"]).docker is True


def test_a_machine_without_the_engine_answers_nothing_for_docker() -> None:
    assert docker_host.answer(["docker", "ps"]) is None


def test_the_modelled_engine_lists_what_the_zone_draws(tmp_path: Path) -> None:
    docker_host.model_docker_host(APPS)
    runner = make_runner(
        {}, {}, {}, [], tmp_path / "units", programs=(*INSTALLED_PROGRAMS, "docker")
    )
    listed = runner.run(["docker", "ps", "-a"])
    assert listed.exit_code == 0
    rows = [json.loads(line) for line in listed.stdout.splitlines()]
    names = {row["name"] for row in rows}
    # An application's stack, one that keeps falling, a stack nobody of Noust made and what Noust runs for itself.
    assert {
        "shop-example-net-web-1",
        "tools-example-net-api-1",
        "nextcloud-app-1",
        "adminer",
        "noust-buildkit",
    } <= names
    shop = next(row for row in rows if row["name"] == "shop-example-net-web-1")
    # It runs from the application's own directory, which is what makes it the application's.
    assert shop["working_dir"] == APPS["shop.example.net"]


def test_a_container_that_is_stopped_is_stopped_for_the_next_listing(tmp_path: Path) -> None:
    docker_host.model_docker_host(APPS)
    runner = make_runner(
        {}, {}, {}, [], tmp_path / "units", programs=(*INSTALLED_PROGRAMS, "docker")
    )
    rows = [json.loads(line) for line in runner.run(["docker", "ps", "-a"]).stdout.splitlines()]
    adminer = next(row for row in rows if row["name"] == "adminer")
    assert adminer["state"] == "exited"
    runner.run(["docker", "start", adminer["id"]])
    again = [json.loads(line) for line in runner.run(["docker", "ps", "-a"]).stdout.splitlines()]
    assert next(row for row in again if row["name"] == "adminer")["state"] == "running"


def test_an_application_the_store_lacks_has_no_stack(tmp_path: Path) -> None:
    docker_host.model_docker_host({"example.org": "/var/www/apps/example.org"})
    runner = make_runner(
        {}, {}, {}, [], tmp_path / "units", programs=(*INSTALLED_PROGRAMS, "docker")
    )
    names = {
        json.loads(line)["name"] for line in runner.run(["docker", "ps", "-a"]).stdout.splitlines()
    }
    assert not any(name.startswith(("shop-example-net", "tools-example-net")) for name in names)
    assert "nextcloud-app-1" in names
