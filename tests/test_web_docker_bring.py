# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a stack to Noust over HTTP.

What the console is written against: a preview says what would be registered and what is not changed and
registers nothing; bringing does it, as the application the person named; what cannot be adopted is refused
first with the reason as a code, and Compose's own words, when its rehearsal says starting the stack the way
Noust would changes something, are returned verbatim; and changing needs the permission, the one for
applications, sudo mode, and is in the audit trail.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.docker_bring import Adoption
from tests.support.docker_web import DockerWorld, build


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "stacks" / "nextcloud"
    path.mkdir(parents=True)
    (path / "compose.yaml").write_text("services: {}\n")
    return path


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: Path) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    for name, service, policy in (
        ("nextcloud-app-1", "app", "unless-stopped"),
        ("nextcloud-db-1", "db", "on-failure"),
    ):
        built.machine.box(
            name,
            image="nextcloud:29-apache" if service == "app" else "postgres:16",
            project="nextcloud",
            service=service,
            working_dir=str(folder),
            config_files=str(folder / "compose.yaml"),
            policy=policy,
        )
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        JobManager.reset_instance()
        NoustStore.reset_instance()


def events(action: str) -> list[dict]:
    return audit.get_log().read(action=action)


class TestThePreview:
    def test_it_says_what_would_be_registered_and_registers_nothing(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={"preview": True})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["stack"], body["name"], body["adopted"]) == ("nextcloud", "nextcloud", False)
        assert body["folder"] == str(folder)
        assert body["compose_file"] == "compose.yaml"
        assert body["recreates"] == []
        assert body["rehearsal"].startswith("[+] Running")
        assert [item["container"] for item in body["restart_loss"]] == ["nextcloud-db-1"]
        assert adoption.adopted == []
        assert events("docker.bring") == []

    def test_the_name_is_the_only_thing_typed(
        self, docker: DockerWorld, folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        docker.send(
            "admin",
            "POST",
            "/stacks/nextcloud/bring",
            json={"preview": True, "name": "cloud.example.com"},
        )

        assert adoption.planned[0]["domain"] == "cloud.example.com"
        assert adoption.planned[0]["path"] == folder


class TestBringing:
    def test_it_registers_the_stack_as_the_application_named_and_is_on_record(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={"name": "cloud"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["adopted"], body["deployment_id"], body["name"]) == (True, 7, "cloud")
        assert [plan.domain for plan in adoption.adopted] == ["cloud"]
        recorded = events("docker.bring")[0]
        assert recorded["resource"] == "app:cloud"
        assert recorded["details"]["stack"] == "nextcloud"

    def test_nothing_that_runs_is_touched(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert docker.machine.changes() == []


class TestWhatCannotBeBrought:
    def test_a_stack_that_is_already_nousts_says_so_as_a_code(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import names
        from tests.support.docker_web import ACME

        Adoption(monkeypatch)
        project = names.project_of_directory(ACME)
        docker.machine.boxes["acme-web-1"].project = project

        response = docker.send("admin", "POST", f"/stacks/{project}/bring", json={})

        assert response.status_code == 409
        assert response.json()["error"] == "docker_blocked"
        assert response.json()["reason"] == "already_noust"

    def test_a_folder_that_is_not_on_this_server_says_so_as_a_code(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)
        for box in docker.machine.boxes.values():
            if box.project == "nextcloud":
                box.working_dir = "/var/lib/portainer/compose/12"
                box.config_files = "/var/lib/portainer/compose/12/docker-compose.yml"

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 409
        assert response.json()["reason"] == "no_folder"

    def test_composes_own_words_are_returned_verbatim_when_its_rehearsal_would_change_something(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: object, **kwargs: object) -> None:
            raise AdoptionRefusedError(
                "Starting the stack as Noust would changes what runs",
                details="Compose would recreate nextcloud-app-1.",
                output=" ✔ DRY-RUN MODE -  Container nextcloud-app-1  Recreated   0.0s",
                changes=("Container nextcloud-app-1 Recreated",),
            )

        monkeypatch.setattr(compose_adopt, "plan_adoption", refuse)

        response = docker.send("admin", "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 409
        assert "Recreated" in response.json()["output"]

    def test_a_stack_that_is_not_there_is_not_there(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        assert docker.send("admin", "POST", "/stacks/nothing/bring", json={}).status_code == 404


class TestWhoMayDoIt:
    @pytest.mark.parametrize("who", ["aud", "alice", "omar", "bob"])
    def test_whoever_may_not_change_docker_is_refused(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch, who: str
    ) -> None:
        adoption = Adoption(monkeypatch)

        response = docker.send(who, "POST", "/stacks/nextcloud/bring", json={})

        assert response.status_code == 403
        assert adoption.adopted == []

    def test_changing_needs_sudo_mode(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.server import get_token_manager

        Adoption(monkeypatch)
        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            "/api/docker/stacks/nextcloud/bring",
            json={},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
