# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What this machine has, as far as what Noust offers goes: ``noust.central.machine``.

The facts the table of functions reads (systemd, a web server, Docker, root, the journal) are not
detected here a second time: each is asked of the code that already knows it (the platform's
detection, the web server managers, the door to Docker). What is held here is that the answer of
each is taken as it is, that a container like the one a NAS runs says "none of it" with the words
of the engine's doctor for Docker, and that the one slow fact, the engine, is asked once in a
while and never makes a request wait for it twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.central import machine
from noust.central.machine import MachineFacts, read_machine_facts, reset_machine_facts
from noust.core.runner import FakeRunner
from noust.managers.server.facts import FactCache
from noust.managers.server.host import HostPaths

VERSION = json.dumps(
    {
        "Client": {"Version": "29.1.3", "ApiVersion": "1.52"},
        "Server": {"Version": "29.1.3", "ApiVersion": "1.52", "MinAPIVersion": "1.44"},
    }
)
INFO = json.dumps({"ServerVersion": "29.1.3", "Driver": "overlay2", "SecurityOptions": []})
CONTEXT = json.dumps(
    [{"Name": "default", "Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
)


@pytest.fixture(autouse=True)
def fresh_facts() -> None:
    """Each test asks the machine itself, not what an earlier one learned."""
    reset_machine_facts()


@pytest.fixture
def host(tmp_path: Path) -> HostPaths:
    """A machine booted with systemd."""
    (tmp_path / "run/systemd/system").mkdir(parents=True)
    return HostPaths(root=tmp_path)


def docker_runner(*programs: str, compose: bool = True, daemon: bool = True) -> FakeRunner:
    """A runner whose machine has Docker, and nothing else unless asked."""
    runner = FakeRunner().only_knows("docker", *programs)
    if daemon:
        runner.script(["docker", "version"], stdout=VERSION)
        runner.script(["docker", "info"], stdout=INFO)
    else:
        runner.script(
            ["docker", "version"],
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            exit_code=1,
        )
    if compose:
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.40.3")
    else:
        runner.script(["docker", "compose", "version"], stderr="unknown command", exit_code=1)
    runner.script(["docker", "context", "inspect"], stdout=CONTEXT)
    return runner


def asked_docker(runner: FakeRunner) -> int:
    """How many times the engine was asked its version."""
    return sum(1 for call in runner.calls if call[:2] == ("docker", "version"))


class TestAServer:
    def test_a_machine_with_everything_says_so(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(machine, "check_root", lambda: True)
        runner = docker_runner("nginx", "journalctl")

        facts = read_machine_facts(runner, host=host)

        assert facts == MachineFacts(
            systemd=True, web_server=True, docker=True, root=True, journal=True
        )

    def test_apache_is_a_web_server_too(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(machine, "check_root", lambda: True)

        facts = read_machine_facts(docker_runner("apache2"), host=host)

        assert facts.web_server is True


class TestTheContainerOfANas:
    """Python, tini, ssh, openssl and git: the central's image, run by an account that is not root."""

    def test_it_has_none_of_it_and_the_engine_says_why(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(machine, "check_root", lambda: False)
        runner = FakeRunner().only_knows("git", "openssl", "ssh")

        facts = read_machine_facts(runner, host=HostPaths(root=tmp_path))

        assert facts.systemd is False
        assert facts.web_server is False
        assert facts.docker is False
        assert facts.root is False
        assert facts.journal is False
        assert facts.docker_problem is not None
        assert facts.docker_problem.code == "docker.engine.missing"

    def test_nothing_is_asked_of_an_engine_that_is_not_there(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("git")

        read_machine_facts(runner, host=HostPaths(root=tmp_path))

        assert not any(call[0] == "docker" for call in runner.calls)


class TestDocker:
    def test_a_daemon_that_does_not_answer_is_no_docker_and_says_its_own_words(
        self, host: HostPaths
    ) -> None:
        facts = read_machine_facts(docker_runner(daemon=False), host=host)

        assert facts.docker is False
        assert facts.docker_problem is not None
        assert facts.docker_problem.code == "docker.engine.unreachable"
        assert "Cannot connect to the Docker daemon" in str(facts.docker_problem.params["reason"])

    def test_an_engine_without_compose_v2_is_not_one_noust_can_use(self, host: HostPaths) -> None:
        facts = read_machine_facts(docker_runner(compose=False), host=host)

        assert facts.docker is False
        assert facts.docker_problem is not None
        assert facts.docker_problem.code == "docker.compose.missing"

    def test_a_usable_engine_has_no_problem(self, host: HostPaths) -> None:
        facts = read_machine_facts(docker_runner(), host=host)

        assert facts.docker is True
        assert facts.docker_problem is None


class TestTheEngineIsAskedOnceInAWhile:
    def test_a_second_look_inside_the_time_is_not_another_question(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr(machine, "_cache", FactCache(background=False, clock=lambda: now[0]))
        runner = docker_runner()

        read_machine_facts(runner, host=host)
        now[0] += machine.TTL_DOCKER - 1
        read_machine_facts(runner, host=host)

        assert asked_docker(runner) == 1

    def test_after_that_time_it_is_asked_again(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr(machine, "_cache", FactCache(background=False, clock=lambda: now[0]))
        runner = docker_runner()

        read_machine_facts(runner, host=host)
        now[0] += machine.TTL_DOCKER + 1
        read_machine_facts(runner, host=host)

        assert asked_docker(runner) == 2

    def test_the_first_look_waits_for_the_answer_instead_of_saying_nothing_yet(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With the background on, which is how the console runs: a session that said "no Docker"
        # because the engine had not been asked yet would hide what is there until the next load.
        monkeypatch.setattr(machine, "_cache", FactCache(background=True))

        facts = read_machine_facts(docker_runner(), host=host)

        assert facts.docker is True

    def test_a_stale_answer_is_given_while_a_new_one_is_asked_for(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        cache = FactCache(background=True, clock=lambda: now[0])
        monkeypatch.setattr(machine, "_cache", cache)
        started: list[object] = []
        monkeypatch.setattr(cache, "_start", lambda entry, loader: started.append(loader))
        runner = docker_runner()
        read_machine_facts(runner, host=host)
        now[0] += machine.TTL_DOCKER + 1

        facts = read_machine_facts(runner, host=host)

        assert facts.docker is True, "the old answer stands while the new one is being asked"
        assert len(started) == 1
        assert asked_docker(runner) == 1

    def test_a_question_that_fails_is_no_docker_with_the_reason_and_not_an_error(
        self, host: HostPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(machine, "_cache", FactCache(background=False))

        def broken(*args: object, **kwargs: object) -> object:
            raise OSError("too many open files")

        monkeypatch.setattr("noust.core.docker.engine.read_engine", broken)

        facts = read_machine_facts(docker_runner(), host=host)

        assert facts.docker is False
        assert facts.docker_problem is not None
        assert facts.docker_problem.code == "docker.engine.unreachable"
        assert "too many open files" in str(facts.docker_problem.params["reason"])


class TestTheRootFact:
    def test_it_is_whether_noust_is_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("noust.core.utils.os.geteuid", lambda: 0)
        assert machine.is_root() is True
        monkeypatch.setattr("noust.core.utils.os.geteuid", lambda: 10001)
        assert machine.is_root() is False
