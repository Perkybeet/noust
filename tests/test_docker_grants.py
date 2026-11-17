# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the server gave to the containers of a project, read from Docker.

The one record of what a stack runs with that neither its repository nor its ``.env`` can change is the
container itself, so the guard asks it (``noust.core.docker.grants``). The reading is proved against
what a real engine printed (``tests/support/docker_grants_samples.py``), it never asks for the
environment, it asks for the project by the label Compose writes, and an engine that cannot be asked
is said and not read as "nothing".
"""

from __future__ import annotations

import pytest

from noust.core.docker import grants, probes
from noust.core.docker.errors import DockerUnavailableError
from noust.core.docker.gate import DockerGate
from noust.core.runner import FakeRunner, is_read_only
from noust.core.teams.scope import Scope
from tests.support.docker_grants_samples import FIRST, SECOND, WEB, WORKER, ps_row

PROJECT = "shop-example-com"


def machine(*rows: str, inspected: str = "", exit_code: int = 0, stderr: str = "") -> FakeRunner:
    """A runner whose Docker lists ``rows`` and answers ``inspected`` to the inspection."""
    runner = FakeRunner()
    runner.script(["docker", "ps"], stdout="\n".join(rows))
    runner.script(["docker", "inspect"], stdout=inspected, exit_code=exit_code, stderr=stderr)
    return runner


class TestWhatARealEngineSaid:
    def test_a_container_given_the_machine_is_read_field_by_field(self) -> None:
        found = grants.parse_grants(WEB + "\n" + WORKER)

        web = found[FIRST]
        assert web.name == "noust-test-w6a3-first"
        assert web.service == "web"
        assert web.oneoff is False
        assert web.privileged is False
        assert web.cap_add == ("CAP_NET_BIND_SERVICE", "CAP_SYS_ADMIN")
        assert web.devices == ("/dev/null",)
        assert web.device_rules == ("c 13:* rwm",)
        assert (web.pid, web.ipc, web.uts, web.cgroupns) == ("host", "host", "host", "host")
        assert web.network == "bridge"
        assert web.security_opt == ("seccomp=unconfined", "label=disable")
        assert web.cgroup_parent == "/noust-test-w6a3"
        assert web.runtime == "runc"
        assert [(b.source, b.destination, b.read_only) for b in web.binds] == [
            ("/tmp", "/data", True),
            ("/var/run/docker.sock", "/var/run/docker.sock", False),
        ]

    def test_a_privileged_one_on_another_ones_network_and_volumes_says_so(self) -> None:
        worker = grants.parse_grants(WEB + "\n" + WORKER)[SECOND]

        assert worker.privileged is True
        assert worker.service == "worker"
        assert worker.oneoff is False
        assert worker.network == f"container:{FIRST}"
        assert worker.volumes_from == ("noust-test-w6a3-first",)
        assert worker.cap_add == () and worker.devices == ()

    def test_what_is_not_an_object_is_left_out_and_nothing_else_is(self) -> None:
        found = grants.parse_grants("not json\n[]\n{}\n" + WEB + "\n")

        assert list(found) == [FIRST]

    def test_the_environment_is_not_among_the_fields_asked(self) -> None:
        # The secrets of an application are in the container's environment: the question names what it
        # wants and ``Config.Env`` is not there, so nothing that is read can leak it.
        assert "Env" not in grants.GRANTS_FORMAT
        assert "Config.Labels" in grants.GRANTS_FORMAT
        assert "docker.compose.service" in grants.GRANTS_FORMAT

    def test_a_volume_docker_keeps_is_not_a_directory_of_the_server(self) -> None:
        line = (
            '{"id":"%s","name":"/x","service":"db","oneoff":"False","state":"running",'
            '"mounts":[{"Type":"volume","Name":"data","Source":"/var/lib/docker/volumes/data/_data",'
            '"Destination":"/var/lib/data","RW":true},{"Type":"tmpfs","Destination":"/run","RW":true}],'
            '"host":{}}' % ("a" * 64)
        )

        assert grants.parse_grants(line)["a" * 64].binds == ()


class TestAProject:
    def test_its_containers_are_asked_by_the_label_compose_writes_and_read(self) -> None:
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            ps_row(SECOND, "shop-worker-1", project=PROJECT, service="worker", state="exited"),
            inspected=WEB + "\n" + WORKER,
        )

        found = grants.read_project_grants(runner, PROJECT)

        assert [g.service for g in found] == ["web", "worker"]
        listing = next(call for call in runner.calls if call[:2] == ("docker", "ps"))
        assert "--all" in listing
        assert f"label=com.docker.compose.project={PROJECT}" in listing
        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert inspection[-2:] == (FIRST, SECOND)
        assert "--format" in inspection and grants.GRANTS_FORMAT in inspection

    def test_what_another_project_has_is_not_this_ones_even_if_the_engine_lists_it(self) -> None:
        # An engine that ignores the filter would hand over the machine: what is read is checked.
        runner = machine(
            ps_row(FIRST, "other-web-1", project="other-example-com", service="web"),
            inspected=WEB,
        )

        assert grants.read_project_grants(runner, PROJECT) == ()
        assert [call for call in runner.calls if call[:2] == ("docker", "inspect")] == []

    def test_a_container_made_for_run_is_not_what_the_stack_runs(self) -> None:
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            ps_row(SECOND, "shop-hook-run-1", project=PROJECT, service="web", oneoff=True),
            inspected=WEB,
        )

        found = grants.read_project_grants(runner, PROJECT)

        assert [g.id for g in found] == [FIRST]
        inspection = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert SECOND not in inspection

    def test_a_project_with_no_container_is_none_and_asks_nothing_more(self) -> None:
        runner = machine()

        assert grants.read_project_grants(runner, PROJECT) == ()
        assert [call[:2] for call in runner.calls] == [("docker", "ps")]

    @pytest.mark.parametrize("name", ["", "-x", "a b", "a;b", "$(x)", "a\nb", "../x"])
    def test_a_name_that_cannot_be_a_project_is_never_put_in_a_command(self, name: str) -> None:
        runner = machine(inspected=WEB)

        assert grants.read_project_grants(runner, name) == ()
        assert runner.calls == []

    def test_a_container_that_went_away_between_the_listing_and_the_reading_is_not_an_error(
        self,
    ) -> None:
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            exit_code=1,
            stderr=f"Error: No such object: {FIRST}",
        )

        assert grants.read_project_grants(runner, PROJECT) == ()

    def test_some_read_and_one_gone_keeps_what_was_read(self) -> None:
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            ps_row(SECOND, "shop-worker-1", project=PROJECT, service="worker"),
            inspected=WEB,
            exit_code=1,
            stderr=f"Error: No such object: {SECOND}",
        )

        assert [g.id for g in grants.read_project_grants(runner, PROJECT)] == [FIRST]

    def test_the_ids_are_asked_in_as_many_commands_as_the_system_takes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(grants, "MAX_IDS", 1)
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            ps_row(SECOND, "shop-worker-1", project=PROJECT, service="worker"),
            inspected=WEB + "\n" + WORKER,
        )

        grants.read_project_grants(runner, PROJECT)

        assert len([call for call in runner.calls if call[:2] == ("docker", "inspect")]) == 2


class TestWhenDockerCannotBeAsked:
    def test_no_docker_at_all_is_said(self) -> None:
        runner = machine().only_knows("git")

        with pytest.raises(DockerUnavailableError, match="not installed"):
            grants.read_project_grants(runner, PROJECT)

    def test_a_daemon_that_does_not_answer_is_said_in_its_own_words(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"], exit_code=1, stderr="Cannot connect to the Docker daemon at unix:///x"
        )

        with pytest.raises(DockerUnavailableError) as caught:
            grants.read_project_grants(runner, PROJECT)

        assert "Cannot connect to the Docker daemon" in caught.value.output

    def test_an_inspection_that_fails_with_nothing_readable_is_said_and_not_taken_for_none(
        self,
    ) -> None:
        runner = machine(
            ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"),
            exit_code=1,
            stderr="error during connect: i/o timeout",
        )

        with pytest.raises(DockerUnavailableError) as caught:
            grants.read_project_grants(runner, PROJECT)

        assert "i/o timeout" in caught.value.output


class TestTheDoorAndTheRehearsal:
    def test_the_gate_reads_a_project_through_its_runner(self) -> None:
        runner = machine(ps_row(FIRST, "shop-web-1", project=PROJECT, service="web"), inspected=WEB)

        found = DockerGate(runner, scope=Scope.everything()).project_grants(PROJECT)

        assert [g.service for g in found] == ["web"]

    def test_a_hub_that_was_not_told_to_deploy_here_leaves_its_machines_docker_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker.errors import DockerZoneClosedError

        monkeypatch.setattr("noust.core.docker.gate.docker_zone_is_open", lambda: False)
        runner = machine(inspected=WEB)

        with pytest.raises(DockerZoneClosedError):
            DockerGate(runner, scope=Scope.everything()).project_grants(PROJECT)

        assert runner.calls == []

    def test_a_rehearsal_may_look_at_what_the_containers_have(self) -> None:
        # ``--dry-run`` runs only what is declared read-only: a rehearsal that could not look at the
        # containers would report a refusal that the real run would not.
        listing = [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "label=a=b",
            "--format",
            "{{x}}",
        ]
        inspection = [
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            "{{x}}",
            "a" * 12,
            "b" * 12,
        ]

        assert is_read_only(listing)
        assert is_read_only(inspection)
        assert not is_read_only(["docker", "rm", FIRST])
        assert probes.READ_ONLY_PROBES
