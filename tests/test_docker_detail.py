# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container from the inside: why it is the way it is, where it comes from, and what it uses.

What the Docker zone shows of one container is Docker's own account of it: the state and how it
ended (the exit code, whether memory ran out, how many times it restarted and when), its restart
policy and limits, what it touches, and its last lines as it wrote them. Four properties are held
here, each from a way this goes wrong.

- **A state is a word of a closed list**, decided in one place, so that a stack, the summary and a
  container say the same of one that keeps falling.
- **Nothing secret is read.** ``inspect`` of a container carries its environment; the question asked
  of Docker names the fields wanted, and the environment is not one of them.
- **A reading that is not stable is not shown as one.** A container that is not running, that is
  restarting or that started a moment ago has no consumption worth showing, and Docker is not asked.
- **What it says last is said as it was written**, with the credentials in it masked and nothing else
  changed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from noust.core.docker import events, inspect, states, stats
from noust.core.docker.models import Container
from noust.core.docker.owners import Registry
from tests.support.docker_machine import DockerMachine, full_id

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


class TestTheStateOfAContainer:
    @pytest.mark.parametrize(
        ("state", "health", "code", "oom", "kind", "problem"),
        [
            ("running", "", None, False, "running", False),
            ("running", "healthy", None, False, "running", False),
            ("running", "starting", None, False, "running", False),
            ("running", "unhealthy", None, False, "unhealthy", True),
            ("restarting", "", 1, False, "loop", True),
            ("paused", "", None, False, "paused", False),
            ("created", "", None, False, "created", False),
            ("dead", "", None, False, "dead", True),
            ("exited", "", 0, False, "ended", False),
            ("exited", "", 143, False, "terminated", False),
            ("exited", "", 137, False, "killed", True),
            ("exited", "", 137, True, "oom", True),
            ("exited", "", 1, False, "error", True),
            ("exited", "", 255, False, "error", True),
            ("exited", "", None, False, "stopped", False),
        ],
    )
    def test_each_state_is_one_word_and_says_whether_it_asks_for_the_operator(
        self, state: str, health: str, code: int | None, oom: bool, kind: str, problem: bool
    ) -> None:
        found = states.classify(state=state, health=health, exit_code=code, oom=oom)

        assert (found.kind, found.problem) == (kind, problem)

    def test_memory_running_out_wins_over_the_exit_code_it_left(self) -> None:
        assert states.classify(state="exited", exit_code=0, oom=True).kind == "oom"

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            ("Exited (137) 3 days ago", 137),
            ("Exited (0) About an hour ago", 0),
            ("Up 2 hours (healthy)", None),
            ("Restarting (1) 5 seconds ago", 1),
            ("Created", None),
            ("", None),
        ],
    )
    def test_the_exit_code_is_read_out_of_the_sentence_docker_prints(
        self, status: str, code: int | None
    ) -> None:
        assert states.exit_code_of(status) == code

    def test_a_listed_container_is_judged_by_what_the_listing_says(self) -> None:
        box = Container(id="a" * 64, name="web", state="exited", status="Exited (1) 2 minutes ago")

        found = states.classify_container(box)

        assert (found.kind, found.problem, found.exit_code) == ("error", True, 1)

    def test_a_container_that_ended_well_under_no_policy_is_a_job_that_finished(self) -> None:
        assert states.is_job(kind="ended", policy="no")
        assert not states.is_job(kind="ended", policy="always")
        assert not states.is_job(kind="error", policy="no")


class TestReadingInspect:
    def machine(self) -> DockerMachine:
        machine = DockerMachine()
        machine.box(
            "api-1",
            state="restarting",
            status="Restarting (137) 40 seconds ago",
            exit_code=137,
            oom=True,
            restart_count=31,
            policy="unless-stopped",
            memory=256 * 1024 * 1024,
            started_at="2026-11-12T11:59:20.123456789Z",
            finished_at="2026-11-12T11:59:40.987654321Z",
            mounts="api_redis",
            networks="api_default",
        )
        machine.box(
            "db-1", policy="on-failure", max_retries=5, health="healthy", nano_cpus=1_500_000_000
        )
        return machine

    def test_the_question_names_the_fields_it_wants_and_the_environment_is_not_one(self) -> None:
        asked = set(re.findall(r"\{\{(?:json )?\.([A-Za-z.]+)\}\}", inspect.INSPECT_FORMAT))

        assert asked == {
            "Id",
            "Name",
            "Config.Image",
            "Image",
            "State",
            "RestartCount",
            "HostConfig.RestartPolicy",
            "HostConfig.Memory",
            "HostConfig.NanoCpus",
            "HostConfig.Privileged",
            "Mounts",
            "NetworkSettings.Networks",
        }
        assert "Env" not in inspect.INSPECT_FORMAT

    def test_how_it_ended_is_read_with_the_times_to_the_second(self) -> None:
        machine = self.machine()

        found = inspect.read_inspect(machine, [full_id("api-1")])[full_id("api-1")]

        assert found.state == "restarting"
        assert (found.exit_code, found.oom_killed, found.restart_count) == (137, True, 31)
        assert found.started_at == datetime(2026, 11, 12, 11, 59, 20, tzinfo=timezone.utc)
        assert found.finished_at == datetime(2026, 11, 12, 11, 59, 40, tzinfo=timezone.utc)

    def test_the_restart_policy_and_the_limits_are_read(self) -> None:
        found = inspect.read_inspect(self.machine(), [full_id("api-1"), full_id("db-1")])

        api, db = found[full_id("api-1")], found[full_id("db-1")]
        assert (api.restart_policy.name, api.memory_limit_bytes) == ("unless-stopped", 256 * 2**20)
        assert (db.restart_policy.name, db.restart_policy.max_retries) == ("on-failure", 5)
        assert db.memory_limit_bytes is None
        assert db.cpu_limit == 1.5 and api.cpu_limit is None

    def test_no_limit_is_none_and_not_zero(self) -> None:
        found = inspect.read_inspect(self.machine(), [full_id("db-1")])[full_id("db-1")]

        assert found.memory_limit_bytes is None

    def test_a_health_check_is_said_with_what_the_last_probe_printed(self) -> None:
        machine = DockerMachine()
        machine.box("web", health="unhealthy", health_output="curl: (7) Failed to connect")

        found = inspect.read_inspect(machine, [full_id("web")])[full_id("web")]

        assert found.health is not None
        assert (found.health.status, found.health.failing_streak) == ("unhealthy", 3)
        assert found.health.last_output == "curl: (7) Failed to connect"

    def test_the_volumes_and_the_bind_mounts_it_touches_are_told_apart(self) -> None:
        machine = DockerMachine()
        machine.box(
            "web",
            mount_list=[
                {
                    "Type": "volume",
                    "Name": "shop_data",
                    "Source": "/x",
                    "Destination": "/data",
                    "RW": True,
                },
                {"Type": "bind", "Source": "/srv/files", "Destination": "/files", "RW": False},
            ],
        )

        found = inspect.read_inspect(machine, [full_id("web")])[full_id("web")]

        assert [(m.kind, m.name, m.source, m.destination, m.read_only) for m in found.mounts] == [
            ("volume", "shop_data", "/x", "/data", False),
            ("bind", "", "/srv/files", "/files", True),
        ]

    def test_many_containers_are_one_process(self) -> None:
        machine = self.machine()

        inspect.read_inspect(machine, [full_id("api-1"), full_id("db-1")])

        assert [call[:2] for call in machine.calls] == [("docker", "inspect")]

    def test_only_the_ids_docker_listed_are_given_to_it(self) -> None:
        machine = self.machine()

        inspect.read_inspect(machine, [full_id("api-1"), "--format=x", "../etc", "a b"])

        assert machine.calls[0][6:] == (full_id("api-1"),)
        assert not any(part in machine.calls[0] for part in ("--format=x", "../etc", "a b"))

    def test_nothing_is_asked_when_there_is_nobody_to_ask_about(self) -> None:
        machine = self.machine()

        assert inspect.read_inspect(machine, []) == {}
        assert machine.calls == []

    def test_a_line_that_is_not_json_is_left_out_and_the_rest_is_read(self) -> None:
        machine = self.machine()
        machine.script(["docker", "inspect"], stdout="not json\n[1,2]\n")

        assert inspect.read_inspect(machine, [full_id("api-1")]) == {}

    def test_docker_refusing_is_no_reading_and_not_a_crash(self) -> None:
        machine = self.machine()
        machine.script(["docker", "inspect"], exit_code=1, stderr="Error: No such object")

        assert inspect.read_inspect(machine, [full_id("api-1")]) == {}

    @pytest.mark.parametrize(
        ("text", "seconds"),
        [
            ("2026-11-12T11:59:20.123456789Z", datetime(2026, 11, 12, 11, 59, 20)),
            ("2026-11-12T11:59:20Z", datetime(2026, 11, 12, 11, 59, 20)),
            ("2026-11-12T11:59:20.5+01:00", datetime(2026, 11, 12, 10, 59, 20)),
        ],
    )
    def test_docker_times_have_nine_digits_and_are_read(self, text: str, seconds: datetime) -> None:
        assert inspect.parse_time(text) == seconds.replace(tzinfo=timezone.utc)

    @pytest.mark.parametrize("text", ["", "0001-01-01T00:00:00Z", "yesterday", None])
    def test_a_time_docker_never_set_is_none(self, text: str | None) -> None:
        assert inspect.parse_time(text) is None


class TestConsumption:
    def running(self, **box: object) -> tuple[DockerMachine, str]:
        machine = DockerMachine()
        machine.box("web", started_at="2026-11-12T08:00:00Z", **box)
        return machine, full_id("web")

    def test_one_reading_is_asked_without_a_stream(self) -> None:
        machine, ident = self.running()

        stats.read_usage(machine, ident, state="running", uptime_seconds=3600)

        assert machine.calls == [
            ("docker", "stats", "--no-stream", "--format", "{{json .}}", ident)
        ]

    def test_cpu_memory_network_and_disk_are_numbers(self) -> None:
        machine, ident = self.running()

        usage = stats.read_usage(machine, ident, state="running", uptime_seconds=3600)

        assert usage.available and usage.reason == ""
        assert usage.cpu_percent == 14.5
        assert usage.memory_bytes == 212 * 2**20
        assert usage.memory_limit_bytes == 512 * 2**20
        assert usage.memory_percent == 41.41
        assert (usage.net_rx_bytes, usage.net_tx_bytes) == (38_000, 91_000)
        assert (usage.block_read_bytes, usage.block_write_bytes) == (1_200_000, 0)
        assert usage.pids == 12

    def test_a_container_with_no_memory_limit_says_docker_sees_the_whole_machine(self) -> None:
        machine, ident = self.running(
            stats={
                "CPUPerc": "1.00%",
                "MemUsage": "18MiB / 7.6GiB",
                "MemPerc": "0.23%",
                "NetIO": "1kB / 1kB",
                "BlockIO": "0B / 0B",
                "PIDs": "3",
            }
        )

        usage = stats.read_usage(
            machine, ident, state="running", uptime_seconds=3600, memory_limited=False
        )

        # Docker reports the machine's memory as the limit when there is none: that is not a limit.
        assert usage.memory_limit_bytes is None and usage.memory_percent is None

    @pytest.mark.parametrize(
        ("state", "seconds", "reason"),
        [
            ("exited", 3600, "not_running"),
            ("created", None, "not_running"),
            ("restarting", 5, "restarting"),
            ("running", 3, "too_young"),
        ],
    )
    def test_a_reading_that_cannot_be_stable_is_not_asked_for_and_says_why(
        self, state: str, seconds: float | None, reason: str
    ) -> None:
        machine, ident = self.running()

        usage = stats.read_usage(machine, ident, state=state, uptime_seconds=seconds)

        assert (usage.available, usage.reason) == (False, reason)
        assert usage.cpu_percent is None
        assert machine.calls == []

    def test_a_running_container_of_unknown_age_is_asked_about(self) -> None:
        machine, ident = self.running()

        assert stats.read_usage(machine, ident, state="running", uptime_seconds=None).available

    def test_docker_having_nothing_to_say_is_no_reading_and_not_zeros(self) -> None:
        machine, ident = self.running(
            stats={
                "CPUPerc": "--",
                "MemUsage": "0B / 0B",
                "MemPerc": "--",
                "NetIO": "--",
                "BlockIO": "--",
                "PIDs": "0",
            }
        )

        usage = stats.read_usage(machine, ident, state="running", uptime_seconds=3600)

        assert (usage.available, usage.reason) == (False, "no_reading")

    def test_docker_failing_is_no_reading(self) -> None:
        machine, ident = self.running()
        machine.script(["docker", "stats"], exit_code=1, stderr="no such container")

        usage = stats.read_usage(machine, ident, state="running", uptime_seconds=3600)

        assert (usage.available, usage.reason) == (False, "no_reading")

    @pytest.mark.parametrize(
        ("text", "size"),
        [
            ("212MiB", 212 * 2**20),
            ("1.5GiB", int(1.5 * 2**30)),
            ("38kB", 38_000),
            ("1.2MB", 1_200_000),
            ("0B", 0),
            ("512 B", 512),
            ("7.6GiB", int(7.6 * 2**30)),
        ],
    )
    def test_a_size_is_read_in_the_units_docker_means(self, text: str, size: int) -> None:
        assert stats.parse_size(text) == size

    @pytest.mark.parametrize("text", ["", "--", "lots", "12 parsecs"])
    def test_what_is_not_a_size_is_none(self, text: str) -> None:
        assert stats.parse_size(text) is None


class TestRestartsOfTheLastMinutes:
    def test_they_are_counted_per_container_from_docker_events_of_the_window(self) -> None:
        machine = DockerMachine()
        machine.box("api-1", restarts_recent=6)
        machine.box("db-1", restarts_recent=1)
        machine.box("quiet")

        found = events.recent_restarts(machine, minutes=10, now=1_700_000_600)

        assert found == {full_id("api-1"): 6, full_id("db-1"): 1}

    def test_the_window_is_given_to_docker_as_two_timestamps_and_only_restarts_are_asked_for(
        self,
    ) -> None:
        machine = DockerMachine()

        events.recent_restarts(machine, minutes=10, now=1_700_000_600)

        assert machine.calls == [
            (
                "docker",
                "events",
                "--since",
                "1700000000",
                "--until",
                "1700000600",
                "--filter",
                "type=container",
                "--filter",
                "event=restart",
                "--format",
                "{{json .}}",
            )
        ]

    def test_docker_not_saying_is_no_count_and_not_an_error(self) -> None:
        machine = DockerMachine()
        machine.script(["docker", "events"], exit_code=1, stderr="boom")

        assert events.recent_restarts(machine, minutes=10, now=1_700_000_600) == {}

    def test_a_line_that_is_not_an_event_is_left_out(self) -> None:
        machine = DockerMachine()
        machine.script(["docker", "events"], stdout="nonsense\n{}\n[]\n")

        assert events.recent_restarts(machine, minutes=10, now=1_700_000_600) == {}


class TestWhatAnInspectionMayCarry:
    def test_a_big_machine_is_asked_in_chunks_so_that_no_command_is_too_long(self) -> None:
        machine = DockerMachine()
        ids = [format(number, "064x") for number in range(450)]

        inspect.read_inspect(machine, ids)

        asked = [call for call in machine.calls if call[:2] == ("docker", "inspect")]
        assert [len(call[6:]) for call in asked] == [inspect.MAX_IDS, 450 - inspect.MAX_IDS]

    def test_what_the_health_check_printed_and_what_docker_said_of_a_failed_start_are_masked(
        self,
    ) -> None:
        from noust.core.docker.gate import DockerGate

        machine = DockerMachine()
        machine.box(
            "web",
            health="unhealthy",
            health_output="curl -H 'Authorization: Bearer abcdefghijklmnop123456' failed",
            error="OCI runtime create failed: password=hunter2hunter2",
        )

        found = DockerGate(machine, registry=lambda: Registry()).container_detail("web")

        body = found.to_dict()
        assert "abcdefghijklmnop123456" not in str(body)
        assert "hunter2hunter2" not in str(body)
        assert body["why"]["health"]["status"] == "unhealthy"
        assert "curl -H" in body["why"]["health"]["last_output"]
        assert body["why"]["error"].startswith("OCI runtime create failed")
