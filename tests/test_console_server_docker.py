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


class TestWhatThePagesThatChangeSomethingWorkWith:
    """The seed of the zone's pages that change something (item 189): a history, a folder and three freshness states."""

    @pytest.fixture
    def seeded(self, tmp_path: Path):
        from noust.core.docker.gate import DockerGate
        from noust.core.docker.owners import registry_from_store
        from noust.core.store import NoustStore, get_store
        from scripts.console_server.sandbox import Sandbox

        NoustStore.reset_instance()
        sandbox = Sandbox(root=tmp_path)
        store = get_store(sandbox.store_file)
        nextcloud = docker_host.seed_docker_state(sandbox)
        apps = {app.domain: app.app_path for app in store.list_apps() if app.app_path}
        machine = docker_host.model_docker_host(apps, nextcloud)
        gate = DockerGate(
            machine, store=store, registry=lambda: registry_from_store(store), disk=lambda p: None
        )
        yield type(
            "Seeded", (), {"store": store, "machine": machine, "gate": gate, "nextcloud": nextcloud}
        )
        NoustStore.reset_instance()

    def test_the_stack_noust_keeps_has_a_document_to_edit_and_a_history_to_go_back_through(
        self, seeded
    ) -> None:
        from noust.deployers.compose_stack_edit import StackEditor

        editor = StackEditor(seeded.gate)
        project = docker_host.STACK_DOMAIN.replace(".", "-")

        view = editor.view(project)
        history = editor.history_of(project)

        assert (view.editable, view.kind) == (True, "document")
        assert view.text == docker_host.STACK_DOCUMENTS[-1]
        assert history.editable is True
        assert [(v.version, v.source) for v in history.items] == [
            (3, "edit"),
            (2, "edit"),
            (1, "found"),
        ]
        assert history.items[0].actor == "marta" and history.items[0].reason

    def test_the_stack_nobody_of_noust_made_runs_from_a_folder_that_is_there(self, seeded) -> None:
        from noust.managers.docker_bring import StackBringer

        bringer = StackBringer(seeded.gate, store=seeded.store, runner=seeded.machine)

        project, path, relative, domain = bringer._prepare("nextcloud", None)

        assert path == Path(seeded.nextcloud) and path.is_dir()
        assert relative == "compose.yaml" and (path / relative).is_file()
        assert domain == "nextcloud"

    def test_the_images_in_use_are_up_to_date_newer_or_not_looked_at(self, seeded) -> None:
        from noust.managers.docker_image_versions import ImageVersions
        from noust.managers.docker_updates import ImageUpdates

        updates = ImageUpdates(
            seeded.gate,
            versions=ImageVersions(seeded.gate, store=seeded.store, runner=seeded.machine),
            store=seeded.store,
        )
        found = {
            (image.tags[0] if image.tags else image.id): plan
            for image, plan in ((i, updates.plan(i)) for i in seeded.gate.images().items)
        }

        states = {name: plan.state for name, plan in found.items()}
        assert states["mariadb:11"] == "current"
        assert states["nextcloud:29-apache"] == states["nginx:1.27"] == "newer"
        assert states["postgres:16"] == "newer"
        assert states["redis:7"] == states["adminer:4"] == "not_looked"
        assert found["nextcloud:29-apache"].mode == "bring_first"
        assert found["nginx:1.27"].mode == "with_net" and found["nginx:1.27"].change == "minor"
        assert found["postgres:16"].change == "major" and found["postgres:16"].needs is not None

    def test_seeding_twice_changes_nothing(self, seeded, tmp_path: Path) -> None:
        from noust.core.docker_stack_versions import StackVersionStore
        from scripts.console_server.sandbox import Sandbox

        docker_host.seed_docker_state(Sandbox(root=tmp_path))

        assert len(StackVersionStore(seeded.store).versions(docker_host.STACK_DOMAIN)) == 3
