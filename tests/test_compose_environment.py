# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What belongs to Noust never reaches Compose or a build.

Compose fills ``${NAME}`` in a document, and reads ``secrets: {x: {environment: NAME}}``, from the
environment of the process that runs it, and the runner hands a command the whole environment of
Noust's own process. A repository's file could therefore name a variable Noust reads for itself (a
token, a passphrase, the data directory) and have its value put into a container. Every command
that reads a file of a stack, ``docker compose`` and ``docker build``, is started without
``NOUST_*``: in one place, the runner, which no caller can forget. What the operator has in the
environment (``DOCKER_HOST``, ``COMPOSE_*``, a variable of their own) and what Noust passes on
purpose (``env=``) go on as before.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from noust.core.command_environment import command_environment, reads_a_file_of_a_stack
from noust.core.runner import SubprocessRunner, clean_environment
from tests.support.environment_runner import EnvironmentRunner

PARENT = {
    "NOUST_SEAL_KEY": "the-passphrase",
    "NOUST_TOKEN": "a-token",
    "NOUST_DATA_DIR": "/data",
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "DOCKER_CONFIG": "/root/.docker",
    "DOCKER_CONTEXT": "nas",
    "COMPOSE_PROFILES": "debug",
    "COMPOSE_PROJECT_NAME": "shop",
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "C",
    "OPERATORS_OWN": "theirs",
    "noust_lower": "not a noust variable: names are case sensitive",
}
KEPT = sorted(name for name in PARENT if not name.startswith("NOUST_"))


class TestWhichCommandsReadAFileOfAStack:
    @pytest.mark.parametrize(
        "argv",
        [
            ["docker", "compose", "up", "-d"],
            ["docker", "compose", "-p", "shop", "-f", "x.yml", "config", "--format", "json"],
            ["docker", "--context", "nas", "compose", "up"],
            ["docker", "-H", "unix:///x.sock", "compose", "ps"],
            ["docker", "--config", "/root/.docker", "compose", "build"],
            ["docker", "build", "-t", "x", "."],
            ["docker", "buildx", "build", "."],
            ["docker", "builder", "build", "."],
            ["docker", "image", "build", "."],
            ["docker-compose", "up"],
            ["/usr/bin/docker", "compose", "up"],
        ],
    )
    def test_these_do(self, argv: list[str]) -> None:
        assert reads_a_file_of_a_stack(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["docker", "ps"],
            ["docker", "run", "--rm", "x"],
            ["docker", "exec", "web", "compose"],
            ["docker", "image", "ls"],
            ["docker", "-H", "build", "ps"],
            ["git", "clone", "x"],
            ["systemctl", "restart", "build"],
            [],
        ],
    )
    def test_these_do_not(self, argv: list[str]) -> None:
        assert not reads_a_file_of_a_stack(argv)


class TestTheEnvironmentOfSuchACommand:
    def test_nothing_of_noust_goes_in_and_everything_else_does(self) -> None:
        found = command_environment(["docker", "compose", "up"], PARENT)

        assert not [name for name in found if name.startswith("NOUST_")]
        assert sorted(found) == KEPT

    def test_what_noust_passes_on_purpose_goes_in_even_when_it_is_named_like_noust(self) -> None:
        found = command_environment(
            ["docker", "build", "."],
            PARENT,
            {"NOUST_BUILD_SECRET_0": "for-the-build", "CHOSEN": "yes", "DOCKER_CONFIG": "/tmp/s"},
        )

        assert found["NOUST_BUILD_SECRET_0"] == "for-the-build"
        assert found["CHOSEN"] == "yes"
        assert found["DOCKER_CONFIG"] == "/tmp/s"
        assert "NOUST_SEAL_KEY" not in found

    def test_any_other_command_keeps_the_environment_it_always_had(self) -> None:
        found = command_environment(["git", "pull"], PARENT)

        assert found == PARENT

    def test_a_clean_environment_is_still_the_allowed_list(self) -> None:
        found = command_environment(["docker", "build", "."], clean_environment(PARENT))

        assert found["PATH"] == "/usr/bin:/bin"
        assert "OPERATORS_OWN" not in found and "NOUST_SEAL_KEY" not in found


class TestTheRealRunner:
    """A process that is really started, a ``docker`` that only says what it was given."""

    @pytest.fixture
    def docker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        program = bin_dir / "docker"
        program.write_text(
            "#!/bin/sh\n" + f'env > "{tmp_path}/seen-$1.txt"\n' + "exit 0\n", encoding="utf-8"
        )
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("NOUST_SEAL_KEY", "the-passphrase")
        monkeypatch.setenv("NOUST_ALGO", "a-secret")
        monkeypatch.setenv("OPERATORS_OWN", "theirs")
        monkeypatch.setenv("DOCKER_HOST", "unix:///elsewhere.sock")
        return tmp_path

    def seen(self, folder: Path, word: str) -> dict[str, str]:
        lines = (folder / f"seen-{word}.txt").read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    @pytest.mark.allow_subprocess
    def test_compose_is_started_without_what_is_noust_s(self, docker: Path) -> None:
        SubprocessRunner().run(["docker", "compose", "config"], env={"FROM_NOUST": "on purpose"})

        seen = self.seen(docker, "compose")
        assert "NOUST_ALGO" not in seen and "NOUST_SEAL_KEY" not in seen
        assert seen["OPERATORS_OWN"] == "theirs"
        assert seen["DOCKER_HOST"] == "unix:///elsewhere.sock"
        assert seen["FROM_NOUST"] == "on purpose"

    @pytest.mark.allow_subprocess
    def test_a_build_is_started_without_it_too(self, docker: Path) -> None:
        SubprocessRunner().run(["docker", "build", "."])

        assert "NOUST_ALGO" not in self.seen(docker, "build")

    @pytest.mark.allow_subprocess
    def test_what_is_streamed_is_the_same(self, docker: Path) -> None:
        SubprocessRunner().stream(["docker", "compose", "build"], on_line=lambda _line: None)

        assert "NOUST_ALGO" not in self.seen(docker, "compose")

    @pytest.mark.allow_subprocess
    def test_a_command_that_is_not_one_of_them_still_gets_the_whole_environment(
        self, docker: Path
    ) -> None:
        SubprocessRunner().run(["docker", "ps"])

        assert self.seen(docker, "ps")["NOUST_ALGO"] == "a-secret"


class TestWhatTheDeployerAsksCompose:
    """The environment of every command the Compose deployer runs, as the runner would start it."""

    def deployer(self, tmp_path: Path) -> Any:
        from noust.deployers.docker_compose import DockerComposeDeployer

        runner = EnvironmentRunner()
        compose = DockerComposeDeployer(runner=runner)
        compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
        compose.compose_path = tmp_path / "stack" / "docker-compose.yml"
        return compose

    def started_with(self, compose: Any, word: str) -> dict[str, str]:
        runner: EnvironmentRunner = compose.runner
        return runner.environment_of(("docker", "compose"), containing=word)

    def test_a_document_that_names_a_variable_of_noust_is_not_given_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_ALGO", "a-secret")
        compose = self.deployer(tmp_path)

        compose._run(compose._compose("config", "--format", "json"))

        assert "NOUST_ALGO" not in self.started_with(compose, "config")

    def test_a_variable_of_the_operator_is_still_interpolated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPERATORS_OWN", "theirs")
        compose = self.deployer(tmp_path)

        compose._run(compose._compose("config", "--format", "json"))

        assert self.started_with(compose, "config")["OPERATORS_OWN"] == "theirs"

    def test_what_the_deployer_passes_on_purpose_is_not_lost(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_ALGO", "a-secret")
        compose = self.deployer(tmp_path)

        compose._run(
            compose._compose("build"),
            env={"NOUST_URL": "https://stack.example.com", "BUILD_ONLY": "x"},
        )

        seen = self.started_with(compose, "build")
        assert seen["NOUST_URL"] == "https://stack.example.com"
        assert seen["BUILD_ONLY"] == "x"
        assert "NOUST_ALGO" not in seen
        # The project and the file travel in the command, not in the environment.
        assert ("docker", "compose", "-p", "stack", "-f") == compose.runner.calls[-1][:5]

    def test_the_guard_asks_compose_for_the_model_without_it_as_well(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.deployers.helpers.compose_guard import resolve_model

        monkeypatch.setenv("NOUST_ALGO", "a-secret")
        compose = self.deployer(tmp_path)

        resolve_model(compose.runner, [compose._compose("config", "--format", "json")], tmp_path)

        assert "NOUST_ALGO" not in self.started_with(compose, "config")
