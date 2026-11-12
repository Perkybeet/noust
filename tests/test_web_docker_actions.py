# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing Docker over HTTP: start, stop, restart and remove, and the plan of a cleanup.

An action is (owner, action, object), judged by the gate before a process starts; the route is the
HTTP around it. What is held here is what the console is written against and what the notices of 2026
against the tools that manage Docker were about.

- **The owner is resolved before anything is done**: what an application, a database engine or Noust
  owns is refused with the door to use, and the refusal is on record; the id of a container is the same
  container as its name, whoever's it is.
- **Changing needs the permission and sudo mode**, a team never has it, and removing is something a second
  person may be asked to approve.
- **What is typed never reaches a command**: Docker is given the id it listed.
- **A cleanup is applied as it was shown**: a plan that changed is shown again, a volume is named, and
  nothing is touched before all of that is checked.
- **Everything that changes something, and everything refused, is in the audit trail.**
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.docker_machine import full_id
from tests.support.docker_web import ACME, DockerWorld, build


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    built = build(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        JobManager.reset_instance()
        NoustStore.reset_instance()


def events(action: str) -> list[dict]:
    return audit.get_log().read(action=action)


class TestWhoMayChangeIt:
    @pytest.mark.parametrize("who", ["admin", "master"])
    def test_whoever_manages_the_server_changes_what_nobody_made(
        self, docker: DockerWorld, who: str
    ) -> None:
        response = docker.send(who, "POST", "/containers/adminer/start")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "kind": "container",
            "name": "adminer",
            "action": "start",
            "size_bytes": None,
        }

    def test_an_auditor_who_only_reads_docker_is_refused(self, docker: DockerWorld) -> None:
        response = docker.send("aud", "POST", "/containers/adminer/start")

        assert response.status_code == 403
        assert docker.machine.changes() == []

    @pytest.mark.parametrize("who", ["alice", "omar", "bob"])
    def test_a_person_of_a_team_is_refused_whatever_role_it_holds(
        self, docker: DockerWorld, who: str
    ) -> None:
        for method, path in (
            ("POST", "/containers/adminer/start"),
            ("POST", "/containers/adminer/stop"),
            ("DELETE", "/containers/adminer"),
            ("DELETE", "/images/sha256:" + "3" * 64),
            ("DELETE", "/networks/scratch"),
            ("DELETE", "/volumes/wiki_data"),
            ("POST", "/cleanup"),
        ):
            response = docker.send(
                who, method, path, json={"token": "x", "select": []} if path == "/cleanup" else None
            )
            assert response.status_code == 403, (method, path)
        assert docker.machine.changes() == []

    def test_changing_needs_sudo_mode(self, docker: DockerWorld) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            "/api/docker/containers/adminer/start",
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert docker.machine.changes() == []

    def test_looking_does_not(self, docker: DockerWorld) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.get(
            "/api/docker/containers", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 200


class TestContainers:
    def test_stopping_and_starting_go_to_docker_with_the_id_it_listed(
        self, docker: DockerWorld
    ) -> None:
        docker.send("admin", "POST", "/containers/portainer/stop")
        docker.send("admin", "POST", "/containers/adminer/start")
        docker.send("admin", "POST", "/containers/portainer/restart")

        assert docker.machine.changes() == [
            ("docker", "stop", "--time", "30", full_id("portainer")),
            ("docker", "start", full_id("adminer")),
            ("docker", "restart", "--time", "30", full_id("portainer")),
        ]

    def test_a_container_is_removed_and_a_running_one_needs_force(
        self, docker: DockerWorld
    ) -> None:
        refused = docker.send("admin", "DELETE", "/containers/portainer")
        done = docker.send("admin", "DELETE", "/containers/adminer")
        forced = docker.send("admin", "DELETE", "/containers/portainer", params={"force": "true"})

        assert refused.status_code == 409
        assert refused.json()["error"] == "docker_refused"
        assert "cannot remove a running container" in refused.json()["output"]
        assert done.json()["name"] == "adminer"
        assert forced.status_code == 200
        assert {"container:adminer", "container:portainer"} <= set(docker.machine.removed)

    def test_the_id_of_a_container_is_the_container_whoever_it_belongs_to(
        self, docker: DockerWorld
    ) -> None:
        by_name = docker.send("admin", "POST", "/containers/acme-web-1/stop")
        by_id = docker.send("admin", "POST", f"/containers/{full_id('acme-web-1')}/stop")
        short = docker.send("admin", "POST", f"/containers/{full_id('acme-web-1')[:12]}/stop")

        for response in (by_name, by_id, short):
            assert response.status_code == 409
            assert response.json()["error"] == "docker_owned"
        assert docker.machine.changes() == []

    def test_what_is_not_there_is_not_found(self, docker: DockerWorld) -> None:
        assert docker.send("admin", "POST", "/containers/nothing/start").status_code == 404

    @pytest.mark.parametrize(
        "ref", ["--all", "adminer;reboot", "..%2F..%2Fetc", "a%20b", "x" * 300]
    )
    def test_what_is_typed_never_reaches_a_command(self, docker: DockerWorld, ref: str) -> None:
        response = docker.send("admin", "POST", f"/containers/{ref}/stop")

        assert response.status_code in (404, 400)
        assert docker.machine.changes() == []

    def test_the_answer_is_the_same_for_what_is_not_there_and_what_a_team_may_not_see(
        self, docker: DockerWorld
    ) -> None:
        missing = docker.send("alice", "POST", "/containers/nothing/stop")
        anothers = docker.send("alice", "POST", "/containers/globex-web-1/stop")

        assert missing.status_code == anothers.status_code == 403
        assert missing.json() == anothers.json()


class TestWhatIsSomebodysElse:
    def test_the_container_of_an_application_is_refused_with_the_door_to_use(
        self, docker: DockerWorld
    ) -> None:
        response = docker.send("admin", "POST", "/containers/acme-web-1/restart")

        body = response.json()
        assert response.status_code == 409 and body["error"] == "docker_owned"
        assert body["owner"] == {"kind": "app", "subject": ACME}
        assert f"noust restart {ACME}" in body["hint"]
        assert docker.machine.changes() == []

    def test_the_image_an_application_keeps_to_go_back_is_not_removed_from_under_it(
        self, docker: DockerWorld
    ) -> None:
        image = next(
            i for i in docker.get("admin", "/images").json()["items"] if i["owner"]["kind"] == "app"
        )

        response = docker.send("admin", "DELETE", f"/images/{image['id']}")

        assert response.status_code == 409 and response.json()["error"] == "docker_owned"
        assert docker.machine.changes() == []

    def test_the_volume_of_an_application_is_its_data_and_goes_with_the_application(
        self, docker: DockerWorld
    ) -> None:
        volume = next(
            v
            for v in docker.get("admin", "/volumes").json()["items"]
            if v["owner"]["kind"] == "app"
        )

        response = docker.send(
            "admin", "DELETE", f"/volumes/{volume['name']}", json={"confirm_name": volume["name"]}
        )

        assert response.status_code == 409 and response.json()["error"] == "docker_owned"
        assert "--remove-volumes" in response.json()["hint"]
        assert docker.machine.changes() == []

    def test_a_refusal_is_on_record_with_who_asked_and_whose_it_is(
        self, docker: DockerWorld
    ) -> None:
        docker.send("admin", "POST", "/containers/acme-web-1/stop")

        denied = events("docker.denied")[0]

        assert denied["actor"] == "token:root-admin-token"
        assert denied["resource"] == "container:acme-web-1"
        assert denied["details"] == {"action": "stop", "owner": "app", "subject": ACME}
        assert denied["result"] == "denied"

    def test_the_container_noust_runs_in_is_never_stopped_from_inside_itself(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import owners

        monkeypatch.setattr(
            owners, "own_container_ids", lambda *a, **k: frozenset({full_id("portainer")})
        )

        response = docker.send("admin", "POST", "/containers/portainer/stop")

        assert response.status_code == 409 and response.json()["owner"]["kind"] == "noust"


class TestImagesNetworksAndVolumes:
    def test_an_image_nobody_uses_is_removed_by_the_id_docker_listed(
        self, docker: DockerWorld
    ) -> None:
        image = next(i for i in docker.get("admin", "/images").json()["items"] if not i["tags"])

        response = docker.send("admin", "DELETE", f"/images/{image['id']}")

        assert response.status_code == 200 and response.json()["size_bytes"] == 380_000_000
        assert f"image:{image['id']}" in docker.machine.removed

    def test_an_image_a_container_uses_stays(self, docker: DockerWorld) -> None:
        response = docker.send("admin", "DELETE", "/images/sha256:" + "1" * 64)

        assert response.status_code == 400
        assert "portainer" in response.json()["detail"]
        assert docker.machine.changes() == []

    def test_a_network_nobody_is_on_is_removed_and_dockers_own_is_not(
        self, docker: DockerWorld
    ) -> None:
        gone = docker.send("admin", "DELETE", "/networks/scratch")
        builtin = docker.send("admin", "DELETE", "/networks/bridge")

        assert gone.status_code == 200 and gone.json()["name"] == "scratch"
        assert builtin.status_code == 409
        assert "Docker's own" in builtin.json()["detail"]

    def test_a_volume_is_not_removed_unless_its_name_is_typed_and_says_what_is_at_stake(
        self, docker: DockerWorld
    ) -> None:
        asked = docker.send("admin", "DELETE", "/volumes/wiki_data")
        wrong = docker.send("admin", "DELETE", "/volumes/wiki_data", json={"confirm_name": "wiki"})

        for response in (asked, wrong):
            assert response.status_code == 400
            assert response.json()["error"] == "confirmation_required"
            assert "3.4 GB" in response.json()["warnings"][0]
        assert docker.machine.changes() == []

    def test_a_volume_whose_name_was_typed_goes_with_its_size(self, docker: DockerWorld) -> None:
        response = docker.send(
            "admin", "DELETE", "/volumes/wiki_data", json={"confirm_name": "wiki_data"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "kind": "volume",
            "name": "wiki_data",
            "action": "remove",
            "size_bytes": 3_400_000_000,
        }

    def test_a_volume_a_container_mounts_stays(self, docker: DockerWorld) -> None:
        response = docker.send(
            "admin", "DELETE", "/volumes/portainer_data", json={"confirm_name": "portainer_data"}
        )

        assert response.status_code == 400 and "portainer" in response.json()["detail"]


class TestWhatIsOnRecord:
    def test_an_action_is_a_change_with_who_did_it_and_what(self, docker: DockerWorld) -> None:
        docker.send("admin", "POST", "/containers/adminer/start")

        event = events("docker.action")[0]

        assert event["actor"] == "token:root-admin-token"
        assert event["resource"] == "container:adminer"
        assert event["details"] == {"action": "start"}

    def test_a_removal_says_how_much_went(self, docker: DockerWorld) -> None:
        docker.send("admin", "DELETE", "/volumes/wiki_data", json={"confirm_name": "wiki_data"})

        event = events("docker.remove")[0]

        assert event["resource"] == "volume:wiki_data"
        assert event["details"] == {"action": "remove", "size_bytes": 3_400_000_000}

    def test_what_docker_refused_is_on_record_as_a_failure(self, docker: DockerWorld) -> None:
        docker.send("admin", "DELETE", "/containers/portainer")

        event = events("docker.remove")[0]

        assert event["result"] == "failure"
        assert event["details"]["action"] == "remove"

    def test_looking_leaves_nothing_in_the_trail_of_changes(self, docker: DockerWorld) -> None:
        docker.get("admin", "/containers")
        docker.get("admin", "/summary")

        assert not events("docker.action") and not events("docker.remove")


class TestWithoutDocker:
    def test_an_action_is_refused_with_the_doctors_words_and_nothing_runs(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.installed = False

        response = docker.send("admin", "POST", "/containers/adminer/start")

        assert response.status_code == 409 and response.json()["error"] == "docker_unavailable"
        assert docker.machine.calls == []

    def test_an_engine_under_what_noust_speaks_is_not_changed_either(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.engine.update(api="1.40", min_api="1.12")

        response = docker.send("admin", "DELETE", "/containers/adminer")

        assert response.status_code == 409
        assert docker.machine.changes() == []


class TestTheCleanup:
    def plan(self, docker: DockerWorld) -> dict:
        return docker.get("admin", "/cleanup/plan").json()

    def test_it_says_what_would_go_whose_it_is_and_what_it_holds(self, docker: DockerWorld) -> None:
        plan = self.plan(docker)

        assert plan["available"] is True and plan["token"]
        by_kind: dict[str, list[dict]] = {}
        for item in plan["items"]:
            by_kind.setdefault(item["kind"], []).append(item)
        assert [i["name"] for i in by_kind["container"]] == ["adminer"]
        assert {i["name"] for i in by_kind["volume"]} == {"wiki_data"}
        assert by_kind["volume"][0]["type_name"] is True and by_kind["volume"][0]["tick"] is False
        assert by_kind["volume"][0]["size_bytes"] == 3_400_000_000
        assert all(i["owner"]["kind"] == "nobody" for i in plan["items"])
        dangling = next(i for i in plan["items"] if i["why"] == "dangling")
        assert dangling["tick"] is True and dangling["size_bytes"] == 380_000_000

    def test_what_stays_is_said_with_why(self, docker: DockerWorld) -> None:
        from noust.core.docker import names

        project = names.project_of_directory(ACME)
        docker.machine.image(
            "5", names.repository_name(project, "web"), "noust-20261001-100000-aaaaaaa", "290MB"
        )

        plan = self.plan(docker)

        kept = {k["name"]: k for k in plan["kept"]}
        way_back = kept[f"{names.repository_name(project, 'web')}:noust-20261001-100000-aaaaaaa"]
        assert (way_back["why"], way_back["owner"]["subject"]) == ("rollback", ACME)
        assert way_back["size_bytes"] == 290_000_000
        assert all(i["name"] != way_back["name"] for i in plan["items"])

    def test_a_person_who_only_reads_docker_reads_the_plan_and_applies_nothing(
        self, docker: DockerWorld
    ) -> None:
        plan = docker.get("aud", "/cleanup/plan")
        applied = docker.send(
            "aud", "POST", "/cleanup", json={"token": plan.json()["token"], "select": ["x"]}
        )

        assert plan.status_code == 200 and applied.status_code == 403

    @staticmethod
    def wait_for_job(docker: DockerWorld, job_id: str) -> dict:
        import time

        for _ in range(500):
            job = docker.world.client.get(
                f"/api/jobs/{job_id}", headers=docker.world.as_("admin")
            ).json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        raise AssertionError("the cleanup never finished")

    def test_the_plan_is_applied_as_a_job_exactly_as_it_was_shown(
        self, docker: DockerWorld
    ) -> None:
        plan = self.plan(docker)
        chosen = [i["id"] for i in plan["items"] if i["tick"]]

        response = docker.send(
            "admin", "POST", "/cleanup", json={"token": plan["token"], "select": chosen}
        )

        assert response.status_code == 202, response.text
        job = self.wait_for_job(docker, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert sorted(r["kind"] for r in job["result"]["removed"]) == ["image", "network"]
        assert job["result"]["failed"] == []
        assert job["result"]["freed_bytes"] >= 380_000_000
        # What was not ticked is where it was.
        assert "container:adminer" in {f"container:{n}" for n in docker.machine.boxes}
        assert any(v["name"] == "wiki_data" for v in docker.machine.volumes)
        finished = [e for e in events("docker.cleanup") if e["details"].get("stage") == "finished"]
        assert finished and finished[0]["details"]["removed"] == 2
        assert finished[0]["actor"] == "token:root-admin-token"

    def test_a_plan_that_changed_since_it_was_shown_is_not_applied_and_comes_back_whole(
        self, docker: DockerWorld
    ) -> None:
        plan = self.plan(docker)
        docker.machine.image("c", "<none>", "<none>", "5MB")

        response = docker.send(
            "admin",
            "POST",
            "/cleanup",
            json={"token": plan["token"], "select": [plan["items"][0]["id"]]},
        )

        body = response.json()
        assert response.status_code == 409 and body["error"] == "plan_changed"
        assert body["plan"]["token"] != plan["token"]
        assert len(body["plan"]["items"]) == len(plan["items"]) + 1
        assert docker.machine.changes() == []

    def test_a_volume_that_was_not_named_stops_it_before_a_job_is_queued(
        self, docker: DockerWorld
    ) -> None:
        plan = self.plan(docker)
        wiki = next(i for i in plan["items"] if i["name"] == "wiki_data")

        response = docker.send(
            "admin", "POST", "/cleanup", json={"token": plan["token"], "select": [wiki["id"]]}
        )

        assert response.status_code == 400 and response.json()["error"] == "confirmation_required"
        assert docker.machine.changes() == []

    def test_what_the_plan_did_not_offer_cannot_be_asked_for(self, docker: DockerWorld) -> None:
        plan = self.plan(docker)

        response = docker.send(
            "admin",
            "POST",
            "/cleanup",
            json={"token": plan["token"], "select": [full_id("acme-web-1")]},
        )

        assert response.status_code == 400
        assert docker.machine.changes() == []

    def test_nothing_chosen_is_not_a_cleanup(self, docker: DockerWorld) -> None:
        plan = self.plan(docker)

        response = docker.send(
            "admin", "POST", "/cleanup", json={"token": plan["token"], "select": []}
        )

        assert response.status_code == 400

    def test_a_request_without_a_token_is_not_a_request(self, docker: DockerWorld) -> None:
        assert docker.send("admin", "POST", "/cleanup", json={"select": ["x"]}).status_code == 422

    def test_a_cleanup_is_on_record_when_it_is_queued(self, docker: DockerWorld) -> None:
        plan = self.plan(docker)
        chosen = [i["id"] for i in plan["items"] if i["tick"]]

        docker.send("admin", "POST", "/cleanup", json={"token": plan["token"], "select": chosen})

        queued = events("docker.cleanup")[0]
        assert queued["details"]["stage"] == "queued" and queued["details"]["items"] == len(chosen)

    def test_without_docker_the_plan_is_the_doctors_words(self, docker: DockerWorld) -> None:
        docker.machine.installed = False

        body = docker.get("admin", "/cleanup/plan").json()

        assert body["available"] is False and body["engine"]["state"] == "missing"
        assert body["items"] == []
