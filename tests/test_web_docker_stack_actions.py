# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Starting, stopping and restarting a whole stack over HTTP.

The same things the container actions hold, for a stack: the owner is resolved before anything is done, so what
an application or a database engine owns is refused with the door to use and the refusal is on record; changing
needs the permission and sudo mode and a team never has it; what is typed never reaches a command; and every
row of the stacks says what it may be offered, by the judgement an action goes through.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.docker import names
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.docker_web import ACME, DockerWorld, build

NEXTCLOUD = "/opt/stacks/nextcloud"


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    for name, service in (("nextcloud-app-1", "app"), ("nextcloud-db-1", "db")):
        built.machine.box(
            name,
            image="nextcloud:29-apache" if service == "app" else "postgres:16",
            project="nextcloud",
            service=service,
            working_dir=NEXTCLOUD,
            policy="unless-stopped",
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


class TestAStackNobodyMade:
    @pytest.mark.parametrize("who", ["admin", "master"])
    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_whoever_manages_the_server_does_it_as_a_whole(
        self, docker: DockerWorld, who: str, verb: str
    ) -> None:
        response = docker.send(who, "POST", f"/stacks/nextcloud/{verb}")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "kind": "stack",
            "name": "nextcloud",
            "action": verb,
            "size_bytes": None,
        }
        assert any(call[-1] == verb or verb in call for call in docker.machine.changes())

    def test_stopping_stops_every_container_of_it_and_nothing_else(
        self, docker: DockerWorld
    ) -> None:
        docker.send("admin", "POST", "/stacks/nextcloud/stop")

        states = {name: box.state for name, box in docker.machine.boxes.items()}
        assert states["nextcloud-app-1"] == states["nextcloud-db-1"] == "exited"
        assert states["portainer"] == "running"

    def test_it_is_on_record(self, docker: DockerWorld) -> None:
        docker.send("admin", "POST", "/stacks/nextcloud/restart")

        recorded = events("docker.action")[0]
        assert recorded["resource"] == "stack:nextcloud"
        assert recorded["details"]["action"] == "restart"

    def test_a_stack_that_is_not_there_is_not_there(self, docker: DockerWorld) -> None:
        response = docker.send("admin", "POST", "/stacks/nothing/stop")

        assert response.status_code == 404
        assert docker.machine.changes() == []

    def test_what_is_typed_does_not_reach_a_command(self, docker: DockerWorld) -> None:
        response = docker.send("admin", "POST", "/stacks/nextcloud%3B%20rm%20-rf/stop")

        assert response.status_code == 404
        assert docker.machine.changes() == []


class TestAStackAnApplicationOwns:
    def test_it_is_changed_through_the_application_and_the_refusal_is_on_record(
        self, docker: DockerWorld
    ) -> None:
        project = names.project_of_directory(ACME)
        # The project of the application's container; it has none in this world until it has one.
        docker.machine.boxes["acme-web-1"].project = project

        response = docker.send("admin", "POST", f"/stacks/{project}/stop")

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "docker_owned"
        assert body["owner"] == {"kind": "app", "subject": "shop.acme.example"}
        assert "noust stop shop.acme.example" in body["hint"]
        assert docker.machine.boxes["acme-web-1"].state == "running"
        denied = events("docker.denied")[0]
        assert denied["details"]["action"] == "stop"


class TestWhoMayChangeIt:
    @pytest.mark.parametrize("who", ["aud", "alice", "omar", "bob"])
    def test_whoever_may_not_change_docker_is_refused(self, docker: DockerWorld, who: str) -> None:
        for verb in ("start", "stop", "restart"):
            assert docker.send(who, "POST", f"/stacks/nextcloud/{verb}").status_code == 403
        assert docker.machine.changes() == []

    def test_changing_needs_sudo_mode(self, docker: DockerWorld) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            "/api/docker/stacks/nextcloud/stop",
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert docker.machine.changes() == []


class TestWhatTheRowsOffer:
    def stacks(self, docker: DockerWorld, who: str = "admin") -> dict[str, dict]:
        return {item["name"]: item for item in docker.get(who, "/stacks").json()["items"]}

    def test_a_stack_nobody_made_offers_to_start_stop_and_restart_it_and_not_to_remove_it(
        self, docker: DockerWorld
    ) -> None:
        can = self.stacks(docker)["nextcloud"]["can"]

        assert (can["start"], can["stop"], can["restart"]) == (True, True, True)
        assert can["remove"] is False
        assert can["blocked"] == ""

    def test_a_stack_of_an_application_says_where_it_is_done(self, docker: DockerWorld) -> None:
        project = names.project_of_directory(ACME)
        docker.machine.boxes["acme-web-1"].project = project

        can = self.stacks(docker)[project]["can"]

        assert (can["start"], can["stop"], can["restart"]) == (False, False, False)
        assert can["blocked"] == "owner"
        assert "noust restart shop.acme.example" in can["via"]

    def test_whoever_may_only_read_is_offered_nothing(self, docker: DockerWorld) -> None:
        can = self.stacks(docker, who="aud")["nextcloud"]["can"]

        assert (can["start"], can["stop"], can["restart"]) == (False, False, False)
        assert can["blocked"] == "permission"

    def test_the_detail_of_a_stack_says_it_too(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/stacks/nextcloud").json()

        assert body["stack"]["can"]["restart"] is True
