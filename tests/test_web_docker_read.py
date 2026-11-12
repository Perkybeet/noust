# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking at Docker over HTTP: what the Docker zone of the console reads.

The routes are a client of the gate (rule 3): a request becomes a look at the machine through
``DockerGate`` and the answer is what the gate resolved, every object with its owner and, for the one
who asks, what it may be offered. What is held here is the contract the console is written against.

- **The zone is the machine's**: a person of a team is refused every route, whatever role they hold,
  and a viewer of the whole server sees all of it and is offered nothing to change.
- **A machine without Docker answers**: the routes say what the doctor says, they do not fail.
- **An object comes with its owner and with what may be done to it**, by the same judgement an action
  goes through.
- **What it says of a container leaks no secret**: no environment, and credentials in what it wrote are
  masked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker.models import OwnerKind
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.web.api import docker as docker_api
from tests.support.docker_machine import full_id
from tests.support.docker_web import ACME, DockerWorld, build


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    built = build(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        NoustStore.reset_instance()


def by_name(items: list[dict]) -> dict[str, dict]:
    return {item["name"]: item for item in items}


class TestWhoMayLook:
    @pytest.mark.parametrize("who", ["admin", "aud", "master"])
    def test_every_principal_that_sees_the_whole_server_and_reads_it_reads_docker(
        self, docker: DockerWorld, who: str
    ) -> None:
        assert docker.get(who, "/summary").status_code == 200
        assert docker.get(who, "/containers").status_code == 200

    @pytest.mark.parametrize("who", ["alice", "dana", "bob", "omar"])
    def test_a_person_of_a_team_is_refused_every_route_it_is_the_machines(
        self, docker: DockerWorld, who: str
    ) -> None:
        for path in ("/summary", "/engine", "/containers", "/containers/acme-web-1", "/images"):
            response = docker.get(who, path)
            assert response.status_code == 403, path
            assert response.json()["error"] == "instance_scope_required"

    def test_a_credential_without_the_permission_is_refused(self, docker: DockerWorld) -> None:
        from tests.support.teams_support import make_person

        blind = make_person(docker.world.teams, "blind", "admin", permissions=["apps.read"])
        docker.world.people["blind"] = blind

        assert docker.get("blind", "/summary").status_code == 403

    def test_there_is_no_way_in_without_a_credential(self, docker: DockerWorld) -> None:
        assert docker.world.client.get("/api/docker/summary").status_code == 401


class TestAMachineWithoutDocker:
    def test_no_docker_is_the_doctors_words_and_not_an_error(self, docker: DockerWorld) -> None:
        docker.machine.installed = False

        body = docker.get("admin", "/containers").json()

        assert body["available"] is False
        assert body["engine"]["state"] == "missing"
        assert body["engine"]["limits"][0]["code"] == "docker.engine.missing"
        assert body["engine"]["limits"][0]["text"]
        assert (body["items"], body["total"]) == ([], 0)

    def test_every_listing_does_the_same(self, docker: DockerWorld) -> None:
        docker.machine.installed = False

        for path in ("/containers", "/stacks", "/images", "/volumes", "/networks"):
            body = docker.get("admin", path).json()
            assert body["available"] is False and body["items"] == [], path

    def test_the_summary_and_the_rest_say_it_too(self, docker: DockerWorld) -> None:
        docker.machine.installed = False

        summary = docker.get("admin", "/summary").json()
        lost = docker.get("admin", "/restart-loss").json()
        engine = docker.get("admin", "/engine").json()

        assert summary["available"] is False and summary["engine"]["state"] == "missing"
        assert summary["attention"] == [] and summary["counts"] == {}
        assert lost["available"] is False and lost["items"] == []
        assert engine["state"] == "missing" and engine["usable"] is False

    def test_a_daemon_that_does_not_answer_is_said_with_its_own_words(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.down = True

        body = docker.get("admin", "/containers").json()

        assert body["available"] is False and body["engine"]["state"] == "unreachable"
        assert "Cannot connect to the Docker daemon" in str(body["engine"]["limits"][0]["params"])

    def test_an_engine_under_what_noust_speaks_is_not_asked_to_list_anything(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.engine.update(api="1.40", min_api="1.12")

        body = docker.get("admin", "/containers").json()

        assert body["available"] is False and body["engine"]["state"] == "too_old"
        assert not [c for c in docker.machine.calls if c[:2] == ("docker", "ps")]

    def test_a_machine_without_compose_still_lists_its_containers(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.engine["compose"] = ""

        body = docker.get("admin", "/containers").json()

        assert body["available"] is True and body["total"] == 5

    def test_the_zone_and_the_sessions_functions_share_one_reading_of_the_engine(
        self, docker: DockerWorld
    ) -> None:
        from noust.central import machine

        docker.get("admin", "/containers")
        seen = machine.engine_report(docker.machine)

        assert seen.state == "ok"
        assert len([c for c in docker.machine.calls if c[:2] == ("docker", "version")]) == 1

    def test_the_engine_page_is_the_doctor_and_asks_again_each_time(
        self, docker: DockerWorld
    ) -> None:
        docker.get("admin", "/containers")
        docker.get("admin", "/engine")
        docker.get("admin", "/engine")

        versions = [c for c in docker.machine.calls if c[:2] == ("docker", "version")]
        assert len(versions) == 3

    def test_what_a_fresh_reading_finds_is_what_the_next_requests_use(
        self, docker: DockerWorld
    ) -> None:
        assert docker.get("admin", "/containers").json()["available"] is True
        docker.machine.installed = False

        # Cached: the zone has not noticed yet.
        assert docker.get("admin", "/containers").status_code == 200
        engine = docker.get("admin", "/engine").json()
        after = docker.get("admin", "/containers").json()

        assert engine["state"] == "missing"
        assert after["available"] is False and after["engine"]["state"] == "missing"

    def test_the_engine_is_asked_once_for_a_few_requests(self, docker: DockerWorld) -> None:
        docker.get("admin", "/containers")
        docker.get("admin", "/images")
        docker.get("admin", "/volumes")

        versions = [c for c in docker.machine.calls if c[:2] == ("docker", "version")]
        assert len(versions) == 1


class TestTheSummary:
    def test_it_says_what_asks_for_attention_worst_first(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/summary").json()

        assert body["available"] is True and body["engine"]["state"] == "ok"
        assert [(a["kind"], a["subject"]) for a in body["attention"]] == [
            ("loop", "api-loop"),
            ("stopped", "adminer"),
        ]
        loop = body["attention"][0]
        assert loop["owner"]["kind"] == "nobody"
        assert loop["facts"]["oom"] is True and loop["facts"]["restarts_recent"] == 6
        assert loop["facts"]["memory_limit_bytes"] == 256 * 2**20

    def test_it_counts_what_there_is(self, docker: DockerWorld) -> None:
        counts = docker.get("admin", "/summary").json()["counts"]

        assert counts["containers"] == {"total": 5, "running": 3, "failing": 1, "stopped": 1}
        assert counts["images"] == {"total": 4}
        assert counts["volumes"] == {"total": 3} and counts["networks"] == {"total": 3}
        assert counts["stacks"]["total"] == 2

    def test_the_disk_is_in_it_and_a_disk_that_is_filling_is_the_last_thing_it_asks_for(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import gate as gate_module

        monkeypatch.setattr(
            gate_module, "_free_space", lambda path: (160 * 2**30, 132 * 2**30, 28 * 2**30)
        )

        body = docker.get("admin", "/summary").json()

        assert body["attention"][-1]["kind"] == "disk"
        assert (
            body["disk"]["free_bytes"] == 28 * 2**30 and body["disk"]["total_bytes"] == 160 * 2**30
        )
        assert body["disk"]["path"] == "/var/lib/docker"

    def test_it_says_whether_the_one_who_asks_may_change_anything(
        self, docker: DockerWorld
    ) -> None:
        assert docker.get("admin", "/summary").json()["can_manage"] is True
        assert docker.get("master", "/summary").json()["can_manage"] is True
        assert docker.get("aud", "/summary").json()["can_manage"] is False


class TestTheEngine:
    def test_it_says_which_docker_there_is_and_what_does_not_fit(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/engine").json()

        assert (body["state"], body["usable"], body["ok"]) == ("ok", True, True)
        assert body["info"]["server_version"] == "29.1.3" and body["info"]["api"] == "1.52"
        assert body["info"]["compose_version"] == "2.40.3"
        assert {fact["code"] for fact in body["facts"]} >= {
            "docker.engine.version",
            "docker.compose.version",
        }
        assert body["limits"] == []

    def test_a_limit_says_it_with_a_code_its_words_and_its_fix(self, docker: DockerWorld) -> None:
        docker.machine.engine["compose"] = ""

        body = docker.get("admin", "/engine").json()

        assert body["ok"] is False and body["usable"] is True
        missing = body["limits"][0]
        assert missing["code"] == "docker.compose.missing"
        assert missing["text"] and missing["hint"]

    def test_what_would_not_come_back_after_a_restart_is_listed_with_how_to_fix_it(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.boxes["portainer"].policy = "no"

        items = {i["name"]: i for i in docker.get("admin", "/restart-loss").json()["items"]}

        assert items["portainer"]["why"] == "policy" and items["portainer"]["fix"] == "set_policy"
        assert (
            items["adminer"]["why"] == "stopped" and items["adminer"]["policy"] == "unless-stopped"
        )
        assert items["adminer"]["owner"]["kind"] == "nobody"


class TestTheContainers:
    def test_each_comes_with_its_owner_and_its_state_as_one_word(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/containers").json()

        rows = by_name(body["items"])
        assert body["total"] == 5 and body["offset"] == 0
        assert rows["acme-web-1"]["owner"] == {
            "kind": "app",
            "subject": ACME,
            "project": rows["acme-web-1"]["project"],
        }
        assert rows["portainer"]["owner"]["kind"] == "nobody"
        assert (rows["api-loop"]["state_kind"], rows["api-loop"]["problem"]) == ("loop", True)
        assert (rows["adminer"]["state_kind"], rows["adminer"]["exit_code"]) == ("terminated", 143)
        assert (rows["acme-web-1"]["state_kind"], rows["acme-web-1"]["problem"]) == (
            "running",
            False,
        )

    def test_each_says_what_it_may_be_offered_for_the_one_who_asks(
        self, docker: DockerWorld
    ) -> None:
        rows = by_name(docker.get("admin", "/containers").json()["items"])

        assert rows["portainer"]["can"] == {
            "start": True,
            "stop": True,
            "restart": True,
            "remove": True,
            "console": True,
            "via": "",
            "blocked": "",
        }
        mine = rows["acme-web-1"]["can"]
        assert (mine["start"], mine["stop"], mine["restart"], mine["remove"]) == (False,) * 4
        assert mine["blocked"] == "owner"
        assert "noust restart shop.acme.example" in mine["via"]

    def test_a_person_who_may_only_look_is_offered_nothing(self, docker: DockerWorld) -> None:
        rows = by_name(docker.get("aud", "/containers").json()["items"])

        assert rows["portainer"]["can"] == {
            "start": False,
            "stop": False,
            "restart": False,
            "remove": False,
            "console": False,
            "via": "",
            "blocked": "permission",
        }

    def test_the_listing_is_cut_by_owner_state_application_and_words(
        self, docker: DockerWorld
    ) -> None:
        def names_of(**query: str) -> list[str]:
            return [c["name"] for c in docker.get("admin", "/containers", **query).json()["items"]]

        assert names_of(owner="nobody") == ["adminer", "api-loop", "portainer"]
        assert names_of(state="exited") == ["adminer"]
        assert names_of(app=ACME) == ["acme-web-1"]
        assert names_of(search="PORT") == ["portainer"]

    def test_a_page_is_a_window_and_says_how_many_there_are_in_all(
        self, docker: DockerWorld
    ) -> None:
        body = docker.get("admin", "/containers", offset=1, limit=2).json()

        assert [c["name"] for c in body["items"]] == ["adminer", "api-loop"]
        assert (body["total"], body["offset"], body["limit"]) == (5, 1, 2)

    @pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 5000}, {"offset": -1}])
    def test_a_window_that_makes_no_sense_is_refused(
        self, docker: DockerWorld, query: dict
    ) -> None:
        assert docker.get("admin", "/containers", **query).status_code == 400

    def test_an_owner_that_is_not_one_is_refused(self, docker: DockerWorld) -> None:
        assert docker.get("admin", "/containers", owner="everybody").status_code == 400

    def test_the_whole_machine_is_one_look_at_the_containers(self, docker: DockerWorld) -> None:
        docker.get("admin", "/containers")

        assert [c[:2] for c in docker.machine.calls if c[:2] == ("docker", "ps")] == [
            ("docker", "ps")
        ]


class TestOneContainerFromTheInside:
    def test_it_says_why_it_keeps_falling_as_docker_says_it(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/containers/api-loop").json()

        assert body["available"] is True
        assert body["container"]["name"] == "api-loop"
        assert body["state"] == {"kind": "loop", "problem": True, "exit_code": 137}
        why = body["why"]
        assert (why["exit_code"], why["oom_killed"], why["restart_count"]) == (137, True, 31)
        assert (why["restarts_recent"], why["window_minutes"], why["looping"]) == (6, 10, True)
        assert why["memory_limit_bytes"] == 256 * 2**20
        assert why["last_lines"] == "FATAL ERROR: JavaScript heap out of memory"
        assert why["finished_at"].startswith("2026-11-12T11:59:40")

    def test_it_says_where_it_comes_from_what_it_touches_and_its_policy(
        self, docker: DockerWorld
    ) -> None:
        body = docker.get("admin", "/containers/acme-web-1").json()

        assert body["owner"]["subject"] == ACME
        assert body["origin"]["service"] == "web" and body["origin"]["project"]
        assert body["restart_policy"] == {"name": "unless-stopped", "max_retries": 0}
        assert [m["kind"] for m in body["touches"]["mounts"]] == ["volume"]
        assert body["touches"]["networks"]
        assert body["container"]["can"]["restart"] is False and body["container"]["can"]["via"]

    def test_what_it_uses_is_a_reading_or_the_reason_there_is_none(
        self, docker: DockerWorld
    ) -> None:
        running = docker.get("admin", "/containers/portainer").json()["usage"]
        looping = docker.get("admin", "/containers/api-loop").json()["usage"]

        assert running["available"] is True and running["cpu_percent"] == 14.5
        assert running["memory_bytes"] == 212 * 2**20
        assert (looping["available"], looping["reason"], looping["cpu_percent"]) == (
            False,
            "restarting",
            None,
        )

    def test_it_is_found_by_the_id_docker_shows(self, docker: DockerWorld) -> None:
        body = docker.get("admin", f"/containers/{full_id('portainer')[:12]}").json()

        assert body["container"]["name"] == "portainer"

    def test_what_is_not_there_is_not_found(self, docker: DockerWorld) -> None:
        assert docker.get("admin", "/containers/nothing").status_code == 404

    @pytest.mark.parametrize("ref", ["--all", "..%2F..%2Fetc", "a%20b", "x;reboot"])
    def test_what_is_typed_never_reaches_a_command(self, docker: DockerWorld, ref: str) -> None:
        assert docker.get("admin", f"/containers/{ref}").status_code in (404, 400)
        assert not [
            c for c in docker.machine.calls if c[:2] in {("docker", "inspect"), ("docker", "logs")}
        ]

    def test_nothing_of_its_environment_is_in_the_answer(self, docker: DockerWorld) -> None:
        text = docker.get("admin", "/containers/portainer").text.lower()

        assert '"env"' not in text and "environment" not in text

    def test_its_last_lines_are_masked_of_credentials_and_nothing_else(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.boxes["portainer"].state = "exited"
        docker.machine.boxes["portainer"].status = "Exited (1) 5 minutes ago"
        docker.machine.boxes["portainer"].exit_code = 1

        lines = docker.get("admin", "/containers/portainer").json()["why"]["last_lines"]

        assert "hunter2hunter2" not in lines and "level=info msg=started" in lines


class TestTheLogsOfAMoment:
    def test_the_end_of_a_log_is_read_as_the_container_wrote_it(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/containers/adminer/logs", tail=50).json()

        assert body == {
            "available": True,
            "engine": None,
            "container": "adminer",
            "tail": 50,
            "logs": "Received SIGTERM, shutting down",
        }

    def test_the_credentials_in_it_are_masked(self, docker: DockerWorld) -> None:
        logs = docker.get("admin", "/containers/portainer/logs").json()["logs"]

        assert "hunter2hunter2" not in logs and "level=info" in logs

    @pytest.mark.parametrize("tail", [0, 5001, -3])
    def test_a_tail_that_makes_no_sense_is_refused(self, docker: DockerWorld, tail: int) -> None:
        assert docker.get("admin", "/containers/adminer/logs", tail=tail).status_code == 400

    def test_the_log_of_what_is_not_there_is_not_found(self, docker: DockerWorld) -> None:
        assert docker.get("admin", "/containers/nothing/logs").status_code == 404


class TestTheStacks:
    def test_each_says_its_state_its_workers_and_whose_it_is(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/stacks").json()

        stacks = by_name(body["items"])
        assert body["total"] == 2
        acme = next(s for s in stacks.values() if s["owner"]["subject"] == ACME)
        assert (acme["state"], acme["running"], acme["workers"], acme["total"]) == (
            "running",
            1,
            1,
            1,
        )
        assert acme["containers"] == ["acme-web-1"]

    def test_one_stack_is_its_containers_in_full(self, docker: DockerWorld) -> None:
        name = next(
            s["name"]
            for s in docker.get("admin", "/stacks").json()["items"]
            if s["owner"]["subject"] == ACME
        )

        body = docker.get("admin", f"/stacks/{name}").json()

        assert body["stack"]["name"] == name
        assert [c["name"] for c in body["containers"]] == ["acme-web-1"]
        assert body["containers"][0]["owner"]["subject"] == ACME

    def test_a_stack_there_is_not_is_not_found(self, docker: DockerWorld) -> None:
        assert docker.get("admin", "/stacks/nothing").status_code == 404


class TestImagesVolumesAndNetworks:
    def test_an_image_says_who_runs_it_whether_it_is_a_way_back_and_what_may_be_done(
        self, docker: DockerWorld
    ) -> None:
        body = docker.get("admin", "/images").json()

        rows = {(r["tags"][0] if r["tags"] else r["id"]): r for r in body["items"]}
        assert rows["portainer/portainer-ce:latest"]["used_by"] == ["portainer"]
        assert (
            rows["portainer/portainer-ce:latest"]["can"]["remove"] is False
        )  # a container uses it
        mine = next(r for r in body["items"] if r["owner"]["kind"] == "app")
        assert mine["way_back"] is True and mine["can"]["remove"] is False
        dangling = next(r for r in body["items"] if not r["tags"])
        assert dangling["dangling"] is True and dangling["can"]["remove"] is True
        assert dangling["size_bytes"] == 380_000_000

    def test_a_volume_says_who_mounts_it_and_does_not_measure_itself(
        self, docker: DockerWorld
    ) -> None:
        body = docker.get("admin", "/volumes").json()

        rows = by_name(body["items"])
        assert rows["portainer_data"]["used_by"] == ["portainer"]
        assert rows["wiki_data"]["used_by"] == [] and rows["wiki_data"]["owner"]["kind"] == "nobody"
        assert all(row["size_bytes"] is None for row in body["items"])
        assert not [c for c in docker.machine.calls if c[:2] == ("docker", "system")]

    def test_the_sizes_are_one_call_of_their_own(self, docker: DockerWorld) -> None:
        body = docker.get("admin", "/volumes/sizes").json()

        assert body["available"] is True
        assert body["sizes"]["wiki_data"] == 3_400_000_000
        assert body["sizes"]["portainer_data"] == 48_000_000

    def test_a_network_says_who_is_on_it_and_whether_docker_made_it(
        self, docker: DockerWorld
    ) -> None:
        rows = by_name(docker.get("admin", "/networks").json()["items"])

        assert rows["bridge"]["builtin"] is True and rows["bridge"]["can"]["remove"] is False
        assert rows["bridge"]["containers"] == ["portainer"]
        assert rows["scratch"]["containers"] == [] and rows["scratch"]["can"]["remove"] is True

    def test_each_listing_is_paged(self, docker: DockerWorld) -> None:
        for path in ("/images", "/volumes", "/networks", "/stacks"):
            body = docker.get("admin", path, limit=1).json()
            assert len(body["items"]) == 1 and body["total"] >= 1 and body["limit"] == 1, path


def test_the_owner_kinds_the_api_says_are_the_ones_of_the_gate() -> None:
    assert {kind.value for kind in OwnerKind} == {"app", "database", "noust", "nobody"}
