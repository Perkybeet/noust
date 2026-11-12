# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What asks for the operator, what runs and what would not come back: the gate's account of a machine.

The summary of the Docker zone is the machine said in the order of how much it hurts: a container that
keeps falling, one that was stopped when it should run, the disk running out. A stack is its containers
and its state is decided here once (a job that ended well is not a failure). A container seen from the
inside says why it is the way it is, as Docker says it, with the credentials in its last lines masked.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from noust.core.docker import names
from noust.core.docker.errors import DockerNotFoundError, DockerUnavailableError
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.teams import Scope
from tests.support.docker_machine import BLOG, SHOP, DockerMachine, full_id

API = "/var/www/apps/api-example-org"
NEXTCLOUD = "/opt/stacks/nextcloud"
NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)

REGISTRY = Registry(
    apps=(
        AppRecord("shop.example.com", SHOP),
        AppRecord("api.example.org", API),
        AppRecord("blog.example.com", BLOG),
    ),
    engines=("pg16",),
)


def web_01() -> DockerMachine:
    """The server the pages open on: stacks of Noust's, one somebody ran by hand, a loop, a stop."""
    machine = DockerMachine()
    machine.box(
        "shop-web-1",
        project="shop-example-com",
        service="web",
        working_dir=SHOP,
        health="healthy",
        status="Up 2 days (healthy)",
        policy="unless-stopped",
        mounts="shop-example-com_uploads",
        networks="shop-example-com_default",
        started_at="2026-11-10T12:00:00Z",
    )
    machine.box(
        "shop-db-1",
        project="shop-example-com",
        service="db",
        working_dir=SHOP,
        image="postgres:16",
        policy="unless-stopped",
        started_at="2026-11-03T12:00:00Z",
    )
    machine.box(
        "api-example-org-api-1",
        project="api-example-org",
        service="api",
        working_dir=API,
        state="restarting",
        status="Restarting (137) 40 seconds ago",
        exit_code=137,
        oom=True,
        restart_count=31,
        restarts_recent=6,
        policy="unless-stopped",
        memory=256 * 2**20,
        started_at="2026-11-12T11:59:20Z",
        finished_at="2026-11-12T11:59:40Z",
        log="[api] warming the product cache\nFATAL ERROR: JavaScript heap out of memory",
    )
    machine.box(
        "nextcloud-app-1",
        project="nextcloud",
        service="app",
        working_dir=NEXTCLOUD,
        image="nextcloud:29-apache",
        policy="unless-stopped",
        started_at="2026-10-13T12:00:00Z",
    )
    machine.box(
        "nextcloud-db-1",
        project="nextcloud",
        service="db",
        working_dir=NEXTCLOUD,
        image="postgres:latest",
        policy="on-failure",
        started_at="2026-10-13T12:00:00Z",
    )
    machine.box(
        "nextcloud-setup-1",
        project="nextcloud",
        service="setup",
        working_dir=NEXTCLOUD,
        state="exited",
        status="Exited (0) 30 days ago",
        exit_code=0,
        policy="no",
    )
    machine.box(
        "adminer",
        image="adminer:latest",
        state="exited",
        status="Exited (143) 3 days ago",
        exit_code=143,
        policy="unless-stopped",
        finished_at="2026-11-09T09:00:00Z",
        log="Received SIGTERM, shutting down",
    )
    machine.box(
        "old-test",
        image="hello-world:latest",
        state="exited",
        status="Exited (0) 41 days ago",
        exit_code=0,
        policy="no",
    )
    machine.box(
        names.instance_container("pg16"),
        project=names.instance_project("pg16"),
        image="postgres:16",
        policy="unless-stopped",
        instance="pg16",
        started_at="2026-10-22T12:00:00Z",
    )
    machine.image("1", "shop-example-com-web", "release-41")
    machine.image("2", "postgres", "16")
    machine.image("3", "nextcloud", "29-apache")
    machine.volume("shop-example-com_uploads", project="shop-example-com", volume="uploads")
    machine.network("shop-example-com_default", project="shop-example-com")
    machine.network("bridge")
    return machine


def gate(
    machine: DockerMachine,
    *,
    free: int = 100 * 2**30,
    total: int = 160 * 2**30,
    scope: Scope | None = None,
    sees: set[str] | None = None,
) -> DockerGate:
    return DockerGate(
        machine,
        registry=lambda: REGISTRY,
        scope=scope or Scope.everything(),
        sees_app=(lambda domain: domain in sees) if sees is not None else None,
        disk=lambda path: (total, total - free, free),
        clock=lambda: NOW.timestamp(),
    )


class TestAStackAndItsState:
    def state_of(self, machine: DockerMachine, name: str) -> tuple[str, int, int]:
        found = {p.name: p for p in gate(machine).projects().items}[name]
        return found.state, found.running, found.workers

    def test_a_stack_whose_containers_run_is_running(self) -> None:
        assert self.state_of(web_01(), "shop-example-com") == ("running", 2, 2)

    def test_a_stack_with_a_container_that_keeps_falling_is_failed(self) -> None:
        machine = web_01()
        machine.box(
            "api-example-org-redis-1",
            project="api-example-org",
            working_dir=API,
            policy="unless-stopped",
        )

        assert self.state_of(machine, "api-example-org") == ("failed", 1, 2)

    def test_a_job_that_ended_well_is_not_a_failure_and_is_not_counted_as_a_worker(self) -> None:
        assert self.state_of(web_01(), "nextcloud") == ("running", 2, 2)

    def test_a_job_that_failed_is_a_failure(self) -> None:
        machine = web_01()
        machine.boxes["nextcloud-setup-1"].status = "Exited (1) 30 days ago"
        machine.boxes["nextcloud-setup-1"].exit_code = 1

        assert self.state_of(machine, "nextcloud")[0] == "failed"

    def test_a_stack_with_nothing_running_is_stopped(self) -> None:
        machine = web_01()
        for name in ("nextcloud-app-1", "nextcloud-db-1"):
            machine.boxes[name].state = "exited"
            machine.boxes[name].status = "Exited (143) 3 days ago"
            machine.boxes[name].exit_code = 143

        assert self.state_of(machine, "nextcloud")[0] == "stopped"

    def test_a_stack_with_some_of_its_services_down_is_a_warning(self) -> None:
        machine = web_01()
        machine.boxes["nextcloud-db-1"].state = "exited"
        machine.boxes["nextcloud-db-1"].status = "Exited (143) 3 days ago"
        machine.boxes["nextcloud-db-1"].exit_code = 143

        assert self.state_of(machine, "nextcloud") == ("warning", 1, 2)

    def test_a_service_whose_health_check_fails_is_a_warning(self) -> None:
        machine = web_01()
        machine.boxes["shop-web-1"].health = "unhealthy"
        machine.boxes["shop-web-1"].status = "Up 2 days (unhealthy)"

        assert self.state_of(machine, "shop-example-com")[0] == "warning"

    def test_the_stack_says_whose_it_is_and_where_it_runs_from(self) -> None:
        found = {p.name: p for p in gate(web_01()).projects().items}

        assert found["shop-example-com"].owner.subject == "shop.example.com"
        assert found["nextcloud"].owner.unowned and found["nextcloud"].working_dir == NEXTCLOUD
        assert found["nextcloud"].to_dict()["state"] == "running"

    def test_one_stack_is_found_by_its_name_with_its_containers_in_full(self) -> None:
        project, members = gate(web_01()).project("nextcloud")

        assert project.name == "nextcloud"
        assert [c.name for c in members] == [
            "nextcloud-app-1",
            "nextcloud-db-1",
            "nextcloud-setup-1",
        ]
        assert {c.state_kind for c in members} == {"running", "ended"}

    def test_a_stack_there_is_not_is_not_found(self) -> None:
        with pytest.raises(DockerNotFoundError):
            gate(web_01()).project("nothing")


class TestAContainerFromTheInside:
    def test_a_container_that_keeps_falling_says_why_as_docker_says_it(self) -> None:
        found = gate(web_01()).container_detail("api-example-org-api-1")

        assert found.state.kind == "loop" and found.state.problem
        assert found.inspected is not None
        assert (found.inspected.exit_code, found.inspected.oom_killed) == (137, True)
        assert found.inspected.restart_count == 31
        assert found.restarts_recent == 6
        assert found.inspected.memory_limit_bytes == 256 * 2**20
        assert found.last_lines == (
            "[api] warming the product cache\nFATAL ERROR: JavaScript heap out of memory"
        )

    def test_a_container_that_ran_out_of_memory_and_ended_is_oom_and_not_a_generic_kill(
        self,
    ) -> None:
        machine = web_01()
        box = machine.boxes["api-example-org-api-1"]
        box.state, box.status = "exited", "Exited (137) 40 seconds ago"

        found = gate(machine).container_detail("api-example-org-api-1")

        assert found.state.kind == "oom"

    def test_the_last_lines_have_their_credentials_masked_and_nothing_else_changed(self) -> None:
        machine = web_01()
        machine.boxes[
            "adminer"
        ].log = "connecting with Authorization: Bearer abcdefghijklmnop123456\nplain line"

        found = gate(machine).container_detail("adminer")

        assert "abcdefghijklmnop123456" not in found.last_lines
        assert found.last_lines.endswith("\nplain line")

    def test_the_last_twenty_lines_are_asked_for_and_never_the_whole_log(self) -> None:
        machine = web_01()

        gate(machine).container_detail("adminer")

        logs = [c for c in machine.calls if c[:2] == ("docker", "logs")]
        assert logs == [("docker", "logs", "--tail", "20", full_id("adminer"))]

    def test_a_container_that_runs_well_has_no_last_lines_to_show_as_the_reason(self) -> None:
        machine = web_01()

        found = gate(machine).container_detail("shop-web-1")

        assert found.state.kind == "running" and found.last_lines == ""
        assert not [c for c in machine.calls if c[:2] == ("docker", "logs")]

    def test_a_container_that_runs_is_read_once_for_what_it_uses(self) -> None:
        machine = web_01()

        found = gate(machine).container_detail("shop-web-1")

        assert found.usage.available and found.usage.cpu_percent == 14.5
        stats = [c for c in machine.calls if c[:2] == ("docker", "stats")]
        assert stats == [
            ("docker", "stats", "--no-stream", "--format", "{{json .}}", full_id("shop-web-1"))
        ]

    def test_one_that_keeps_falling_has_no_stable_reading_and_says_so(self) -> None:
        found = gate(web_01()).container_detail("api-example-org-api-1")

        assert (found.usage.available, found.usage.reason) == (False, "restarting")

    def test_one_that_started_a_moment_ago_has_no_stable_reading_either(self) -> None:
        machine = web_01()
        machine.boxes["shop-web-1"].started_at = "2026-11-12T11:59:55Z"

        found = gate(machine).container_detail("shop-web-1")

        assert found.usage.reason == "too_young"

    def test_a_container_without_a_memory_limit_is_not_given_the_machines_as_one(self) -> None:
        found = gate(web_01()).container_detail("shop-web-1")

        assert found.usage.memory_limit_bytes is None

    def test_the_restart_policy_the_volumes_and_the_networks_are_said(self) -> None:
        found = gate(web_01()).container_detail("shop-web-1")

        assert found.inspected is not None
        assert found.inspected.restart_policy.name == "unless-stopped"
        assert [m.name for m in found.inspected.mounts] == ["shop-example-com_uploads"]
        assert found.inspected.networks == ("shop-example-com_default",)

    def test_a_container_is_found_by_the_id_docker_shows_too(self) -> None:
        found = gate(web_01()).container_detail(full_id("shop-web-1")[:12])

        assert found.container.name == "shop-web-1"

    def test_the_owner_is_resolved_with_it(self) -> None:
        found = gate(web_01()).container_detail("api-example-org-api-1")

        assert found.container.owner.subject == "api.example.org"

    def test_what_a_team_may_not_see_is_not_there(self) -> None:
        mine = gate(web_01(), scope=Scope.nothing(), sees={"shop.example.com"})

        with pytest.raises(DockerNotFoundError):
            mine.container_detail("adminer")
        assert mine.container_detail("shop-web-1").container.name == "shop-web-1"

    def test_what_was_typed_never_reaches_a_command(self) -> None:
        machine = web_01()

        for ref in ("--all", "../etc", "adminer; reboot", "shop-web-1 adminer"):
            with pytest.raises(DockerNotFoundError):
                gate(machine).container_detail(ref)

        assert not [
            c for c in machine.calls if c[:2] in {("docker", "inspect"), ("docker", "logs")}
        ]

    def test_a_container_that_went_away_between_the_listing_and_the_look_is_still_answered(
        self,
    ) -> None:
        machine = web_01()
        machine.script(["docker", "inspect"], stdout="", exit_code=1, stderr="No such object")

        found = gate(machine).container_detail("shop-web-1")

        assert found.inspected is None
        assert found.state.kind == "running"

    def test_what_it_comes_from_and_touches_is_in_the_answer(self) -> None:
        found = gate(web_01()).container_detail("nextcloud-app-1").to_dict()

        assert found["origin"]["image"] == "nextcloud:29-apache"
        assert found["origin"]["project"] == "nextcloud"
        assert found["origin"]["working_dir"] == NEXTCLOUD
        assert found["owner"]["kind"] == "nobody"
        assert found["restart_policy"] == {"name": "unless-stopped", "max_retries": 0}

    def test_the_answer_carries_no_environment(self) -> None:
        text = str(gate(web_01()).container_detail("shop-web-1").to_dict()).lower()

        assert "env" not in text.replace("environment", "")


class TestThePageOfLogs:
    def test_the_tail_of_a_container_is_asked_with_timestamps_and_a_bound(self) -> None:
        machine = web_01()

        text = gate(machine).container_logs("adminer", tail=50)

        assert text == "Received SIGTERM, shutting down"
        assert [c for c in machine.calls if c[:2] == ("docker", "logs")] == [
            ("docker", "logs", "--tail", "50", "--timestamps", full_id("adminer"))
        ]

    @pytest.mark.parametrize("tail", [0, -1, 5001])
    def test_a_tail_that_makes_no_sense_is_refused(self, tail: int) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            gate(web_01()).container_logs("adminer", tail=tail)

    def test_the_credentials_of_an_application_are_masked_in_the_lines(self) -> None:
        machine = web_01()
        machine.boxes["shop-web-1"].log = "password=hunter2hunter2 ok"

        text = gate(machine).container_logs("shop-web-1")

        assert "hunter2hunter2" not in text

    def test_logs_of_what_is_not_there_are_not_found(self) -> None:
        with pytest.raises(DockerNotFoundError):
            gate(web_01()).container_logs("nothing")

    def test_following_a_container_streams_what_docker_prints_and_resolves_the_owner_first(
        self,
    ) -> None:
        machine = web_01()
        seen: list[str] = []

        gate(machine).follow_container_logs("adminer", seen.append, tail=10, seconds=30)

        assert seen == ["Received SIGTERM, shutting down"]
        assert [c for c in machine.calls if c[:2] == ("docker", "logs")] == [
            (
                "docker",
                "logs",
                "--follow",
                "--tail",
                "10",
                "--timestamps",
                full_id("adminer"),
            )
        ]

    def test_following_what_a_team_may_not_see_starts_nothing(self) -> None:
        machine = web_01()
        mine = gate(machine, scope=Scope.nothing(), sees={"shop.example.com"})

        with pytest.raises(DockerNotFoundError):
            mine.follow_container_logs("adminer", lambda line: None, tail=10, seconds=30)

        assert not [c for c in machine.calls if c[:2] == ("docker", "logs")]


class TestWhatAsksForAttention:
    def test_the_worst_comes_first_and_each_says_who_and_why(self) -> None:
        found = gate(web_01()).summary()

        assert [(a.kind, a.subject) for a in found.attention] == [
            ("loop", "api-example-org-api-1"),
            ("stopped", "adminer"),
        ]

    def test_a_loop_says_how_it_ended_how_often_and_what_it_was_given(self) -> None:
        loop = gate(web_01()).summary().attention[0].to_dict()

        assert loop["owner"] == {
            "kind": "app",
            "subject": "api.example.org",
            "project": "api-example-org",
        }
        assert loop["facts"] == {
            "exit_code": 137,
            "oom": True,
            "memory_limit_bytes": 256 * 2**20,
            "restarts_recent": 6,
            "window_minutes": 10,
            "restart_count": 31,
        }

    def test_a_container_stopped_when_its_policy_says_it_should_run_is_told_with_its_policy(
        self,
    ) -> None:
        stopped = gate(web_01()).summary().attention[1].to_dict()

        assert stopped["facts"]["policy"] == "unless-stopped"
        assert stopped["facts"]["exit_code"] == 143
        assert stopped["facts"]["finished_at"] == "2026-11-09T09:00:00+00:00"

    def test_a_job_that_ended_and_a_container_that_ended_for_good_are_not_asked_about(self) -> None:
        subjects = {a.subject for a in gate(web_01()).summary().attention}

        assert "old-test" not in subjects and "nextcloud-setup-1" not in subjects

    def test_a_container_that_failed_asks_for_attention_whatever_its_policy(self) -> None:
        machine = web_01()
        old = machine.boxes["old-test"]
        old.status, old.exit_code = "Exited (1) 2 hours ago", 1

        subjects = [a.subject for a in gate(machine).summary().attention]

        assert "old-test" in subjects

    def test_what_noust_runs_for_itself_is_never_asked_about(self) -> None:
        machine = web_01()
        machine.box(
            "noust-copy",
            helper="copy",
            state="exited",
            status="Exited (1) 1 minute ago",
            exit_code=1,
        )

        assert "noust-copy" not in {a.subject for a in gate(machine).summary().attention}

    def test_a_container_restarted_often_in_the_window_is_a_loop_even_while_it_runs(self) -> None:
        machine = web_01()
        shop = machine.boxes["shop-web-1"]
        shop.restarts_recent = 4

        loops = [a for a in gate(machine).summary().attention if a.kind == "loop"]

        assert {a.subject for a in loops} == {"api-example-org-api-1", "shop-web-1"}

    def test_two_restarts_are_not_a_loop(self) -> None:
        machine = web_01()
        machine.boxes["shop-web-1"].restarts_recent = 2

        loops = [a for a in gate(machine).summary().attention if a.kind == "loop"]

        assert {a.subject for a in loops} == {"api-example-org-api-1"}

    def test_the_disk_running_out_comes_last_with_what_can_be_recovered(self) -> None:
        machine = web_01()
        machine.df = [
            {"Type": "Images", "TotalCount": "20", "Size": "70GB", "Reclaimable": "1.8GB (2%)"},
            {"Type": "Build Cache", "TotalCount": "4", "Size": "1GB", "Reclaimable": "0B"},
        ]

        found = gate(machine, free=20 * 2**30).summary()

        last = found.attention[-1].to_dict()
        assert last["kind"] == "disk" and last["subject"] is None
        assert last["facts"] == {
            "free_bytes": 20 * 2**30,
            "total_bytes": 160 * 2**30,
            "reclaimable_bytes": 1_800_000_000,
        }

    def test_with_room_there_is_no_disk_item(self) -> None:
        kinds = [a.kind for a in gate(web_01(), free=100 * 2**30).summary().attention]

        assert "disk" not in kinds

    def test_a_disk_nobody_can_measure_is_said_and_not_guessed(self) -> None:
        found = DockerGate(
            web_01(),
            registry=lambda: REGISTRY,
            disk=lambda path: None,
            clock=lambda: NOW.timestamp(),
        ).summary()

        assert found.disk is not None and found.disk.free_bytes is None
        assert "disk" not in [a.kind for a in found.attention]

    def test_nothing_wrong_is_an_empty_list(self) -> None:
        machine = DockerMachine()
        machine.box("web", policy="unless-stopped")

        assert gate(machine).summary().attention == ()


class TestTheCounters:
    def test_each_kind_of_thing_is_counted(self) -> None:
        counts = gate(web_01()).summary().counts

        assert counts["containers"] == {"total": 9, "running": 5, "failing": 1, "stopped": 3}
        assert counts["stacks"] == {"total": 4, "unowned": 1}
        assert counts["images"] == {"total": 3}
        assert counts["volumes"] == {"total": 1}
        assert counts["networks"] == {"total": 2}

    def test_what_is_counted_is_what_the_principal_sees(self) -> None:
        mine = gate(web_01(), scope=Scope.nothing(), sees={"shop.example.com"})

        counts = mine.summary().counts

        assert counts["containers"]["total"] == 2
        assert counts["stacks"] == {"total": 1, "unowned": 0}

    def test_the_disk_docker_takes_and_how_much_is_free_where_it_lives(self) -> None:
        machine = web_01()
        machine.df = [
            {"Type": "Images", "TotalCount": "20", "Size": "60GB", "Reclaimable": "1.8GB (3%)"},
            {"Type": "Containers", "TotalCount": "9", "Size": "2GB", "Reclaimable": "0B"},
            {"Type": "Local Volumes", "TotalCount": "10", "Size": "8GB", "Reclaimable": "0B"},
        ]

        disk = gate(machine, free=28 * 2**30).summary().disk

        assert disk is not None
        assert disk.docker_bytes == 70_000_000_000
        assert disk.reclaimable_bytes == 1_800_000_000
        assert (disk.free_bytes, disk.total_bytes) == (28 * 2**30, 160 * 2**30)
        assert disk.path == "/var/lib/docker"

    def test_the_summary_is_a_handful_of_processes_however_many_containers_there_are(self) -> None:
        machine = web_01()

        gate(machine).summary()

        programs = [c[:3] for c in machine.calls]
        assert len(programs) <= 12
        assert sum(1 for c in programs if c[:2] == ("docker", "ps")) == 1
        assert sum(1 for c in programs if c[:2] == ("docker", "inspect")) == 1

    def test_without_docker_the_summary_is_unavailable_and_not_an_empty_machine(self) -> None:
        machine = DockerMachine()
        machine.installed = False

        with pytest.raises(DockerUnavailableError):
            gate(machine).summary()


class TestWhatWouldNotComeBackAfterARestart:
    def lost(self, machine: DockerMachine) -> dict[str, tuple[str, str, str]]:
        found = gate(machine).restart_loss()
        return {item.name: (item.why, item.policy, item.fix) for item in found}

    def test_a_container_whose_policy_does_not_bring_it_back_is_lost(self) -> None:
        found = self.lost(web_01())

        assert found["nextcloud-db-1"] == ("policy", "on-failure", "bring_first")

    def test_one_somebody_stopped_under_a_policy_that_would_is_lost_when_the_policy_is_unless_stopped(
        self,
    ) -> None:
        found = self.lost(web_01())

        assert found["adminer"] == ("stopped", "unless-stopped", "set_policy")

    def test_a_container_that_always_restarts_comes_back_even_if_somebody_stopped_it(self) -> None:
        machine = web_01()
        machine.boxes["adminer"].policy = "always"

        assert "adminer" not in self.lost(machine)

    def test_a_job_and_what_noust_starts_when_it_has_to_are_left_out(self) -> None:
        machine = web_01()
        machine.box("noust-copy", helper="copy", policy="no")

        found = self.lost(machine)

        assert "nextcloud-setup-1" not in found and "old-test" not in found
        assert "noust-copy" not in found

    def test_what_belongs_to_an_application_is_fixed_through_the_application(self) -> None:
        machine = web_01()
        machine.boxes["shop-db-1"].policy = "no"

        assert self.lost(machine)["shop-db-1"] == ("policy", "no", "via_owner")

    def test_a_loose_container_with_no_policy_is_fixed_by_setting_one(self) -> None:
        machine = web_01()
        machine.box("pg-legacy", policy="no")

        assert self.lost(machine)["pg-legacy"] == ("policy", "no", "set_policy")

    def test_a_machine_whose_containers_all_come_back_loses_nothing(self) -> None:
        machine = DockerMachine()
        machine.box("web", policy="always")

        assert gate(machine).restart_loss() == ()


class TestWhatARowIsOffered:
    """The console draws a button for what the gate says may be done, and says why when not."""

    def row(self, machine: DockerMachine, name: str):
        return next(c for c in gate(machine).containers().items if c.name == name)

    def test_what_nobody_made_is_offered_everything_and_says_nothing(self) -> None:
        offered = gate(web_01()).can(self.row(web_01(), "adminer"))

        assert (offered.start, offered.stop, offered.restart, offered.remove) == (True,) * 4
        assert offered.blocked == "" and offered.via == ""

    def test_what_an_application_owns_is_offered_nothing_and_the_door_is_said(self) -> None:
        machine = web_01()

        offered = gate(machine).can(self.row(machine, "shop-web-1"))

        assert not (offered.start or offered.stop or offered.restart or offered.remove)
        assert offered.blocked == "owner" and "noust restart shop.example.com" in offered.via

    def test_a_person_who_may_not_change_docker_is_offered_nothing_and_told_nothing_of_owners(
        self,
    ) -> None:
        machine = web_01()

        offered = gate(machine).can(self.row(machine, "adminer"), manage=False)

        assert not (offered.start or offered.remove)
        assert offered.blocked == "permission" and offered.via == ""

    def test_an_image_a_container_uses_is_not_offered_for_removal(self) -> None:
        machine = web_01()
        found = {i.tags[0]: i for i in gate(machine).images().items if i.tags}

        offered = gate(machine).can(found["postgres:16"])

        assert offered.remove is False
        assert offered.blocked in ("in_use", "owner")

    def test_an_unused_image_nobody_owns_is_offered_for_removal(self) -> None:
        machine = web_01()
        machine.image("c", "debian", "11")
        found = {i.tags[0]: i for i in gate(machine).images().items if i.tags}

        offered = gate(machine).can(found["debian:11"])

        assert offered.remove is True and offered.blocked == ""

    def test_an_image_nobody_owns_that_a_stopped_container_uses_is_in_use_and_says_so(self) -> None:
        machine = web_01()
        found = {i.tags[0]: i for i in gate(machine).images().items if i.tags}
        machine.image("d", "adminer", "latest")
        found = {i.tags[0]: i for i in gate(machine).images().items if i.tags}

        offered = gate(machine).can(found["adminer:latest"])

        assert offered.remove is False and offered.blocked == "in_use"

    def test_a_volume_a_container_mounts_and_a_network_somebody_is_on_are_in_use(self) -> None:
        machine = web_01()
        machine.box("keeper", mounts="loose_data", networks="loose_net")
        machine.volume("loose_data")
        machine.network("loose_net")
        g = gate(machine)
        volume = next(v for v in g.volumes().items if v.name == "loose_data")
        network = next(n for n in g.networks().items if n.name == "loose_net")

        assert g.can(volume).blocked == "in_use" and g.can(volume).remove is False
        assert g.can(network).blocked == "in_use" and g.can(network).remove is False

    def test_a_network_docker_makes_is_builtin(self) -> None:
        machine = web_01()
        bridge = next(n for n in gate(machine).networks().items if n.name == "bridge")

        offered = gate(machine).can(bridge)

        assert offered.remove is False and offered.blocked == "builtin"
