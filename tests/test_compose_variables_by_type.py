# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack has the variables a process application has (checklist B-43, B-46, B-48).

A variable only the build is given reaches ``docker compose build`` through the command's
environment and is written to no file; one only the containers are given is blanked for the build,
which Compose would otherwise fill from the ``.env``; a reference is resolved before the build and
again at every update; and the stack is told its own URL, domain, branch, commit and release in the
``.env`` its containers load, put back when an update does not pass the gate and written for the
release a rollback goes to. Over the real deployer and the real store, with only processes faked.
"""

# The Proggest fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.core.variables.model import SERVER
from noust.core.variables.predefined import RESERVED
from noust.core.variables.store import VariableStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.app_env import read_app_env, read_environment_of, write_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.validators.environment import EnvironmentValidationError
from tests.test_docker_compose_sites import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    certs,
    root,
    stack,
    store,
    web,
)


def file_values(root: Path) -> dict[str, str]:
    return EnvManager().read_env_file(root / ".env")


def build_call(runner: FakeRunner) -> int:
    """Index of the ``docker compose ... build`` call."""
    return next(
        i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "compose") and "build" in call
    )


def up_calls(runner: FakeRunner) -> list[int]:
    return [
        i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "compose") and "up" in call
    ]


def reconfigure(
    stack: DockerComposeDeployer, root: Path, **options: object
) -> DockerComposeDeployer:
    """Configure the fixture's deployer again, with variables."""
    stack.configure(DOMAIN, "https://example.com/proggest.git", app_path=root, **options)
    # configure() forgets the claim the fixture made of a tree that is already in place.
    stack.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    return stack


class TestTheStackIsToldWhatItIs:
    def test_the_env_its_containers_load_carries_the_facts_of_the_deployment(
        self, stack: DockerComposeDeployer, root: Path
    ) -> None:
        stack.deploy()

        values = file_values(root)
        assert values["NOUST_DOMAIN"] == DOMAIN
        assert values["NOUST_URL"].endswith(f"://{DOMAIN}")
        # The operator's own line is where it was.
        assert values["PERSONNEL_ENCRYPTION_KEY"] == "0123456789abcdef"
        assert (root / ".env").read_text().startswith("PERSONNEL_ENCRYPTION_KEY=0123456789abcdef")

    def test_what_an_operator_reads_back_does_not_hold_them(
        self, stack: DockerComposeDeployer, root: Path, store: NoustStore
    ) -> None:
        stack.deploy()

        app = store.get_app(DOMAIN)
        assert app is not None
        assert set(read_app_env(app)).isdisjoint(RESERVED)

    def test_a_name_noust_sets_itself_is_refused_before_anything_is_built(
        self, stack: DockerComposeDeployer, root: Path, runner: FakeRunner
    ) -> None:
        reconfigure(stack, root, env_vars={"NOUST_URL": "https://elsewhere.example"})

        with pytest.raises(EnvironmentValidationError):
            stack.deploy()

        assert not any("build" in call for call in runner.calls)


class TestAVariableOnlyTheBuildIsGiven:
    def test_it_reaches_the_build_and_neither_the_file_nor_the_containers(
        self, stack: DockerComposeDeployer, root: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        reconfigure(
            stack, root, env_vars={"NPM_TOKEN": "tok-123"}, env_audience={"NPM_TOKEN": "build"}
        )

        stack.deploy()

        assert "NPM_TOKEN" not in file_values(root)
        assert runner.envs[build_call(runner)]["NPM_TOKEN"] == "tok-123"
        for index in up_calls(runner):
            assert "NPM_TOKEN" not in (runner.envs[index] or {})
        app = store.get_app(DOMAIN)
        assert app is not None
        assert read_environment_of(app).build_only == {"NPM_TOKEN": "tok-123"}

    def test_the_value_is_kept_out_of_what_is_recorded_of_the_build(
        self, stack: DockerComposeDeployer, root: Path, runner: FakeRunner
    ) -> None:
        reconfigure(
            stack, root, env_vars={"NPM_TOKEN": "tok-123"}, env_audience={"NPM_TOKEN": "build"}
        )

        stack.deploy()

        assert all("tok-123" not in " ".join(call) for call in runner.calls)

    def test_it_is_not_among_the_values_the_row_keeps(
        self, stack: DockerComposeDeployer, root: Path, store: NoustStore
    ) -> None:
        reconfigure(
            stack, root, env_vars={"NPM_TOKEN": "tok-123", "OTHER": "1"},
            env_audience={"NPM_TOKEN": "build"},
        )

        stack.deploy()

        app = store.get_app(DOMAIN)
        assert app is not None
        assert "NPM_TOKEN" not in app.env_vars
        assert app.env_vars["OTHER"] == "1"


class TestAVariableOnlyTheContainersAreGiven:
    def test_the_build_sees_it_as_empty_and_the_file_keeps_it(
        self, stack: DockerComposeDeployer, root: Path, runner: FakeRunner
    ) -> None:
        reconfigure(
            stack, root, env_vars={"SENTRY_DSN": "https://k@sentry"},
            env_audience={"SENTRY_DSN": "runtime"},
        )

        stack.deploy()

        assert file_values(root)["SENTRY_DSN"] == "https://k@sentry"
        # Compose fills a name from the .env unless the process says otherwise: empty is "set".
        assert runner.envs[build_call(runner)]["SENTRY_DSN"] == ""


class TestReferences:
    def test_a_reference_given_at_creation_is_resolved_and_remembered(
        self, stack: DockerComposeDeployer, root: Path, store: NoustStore
    ) -> None:
        reconfigure(stack, root, env_vars={"PUBLIC_URL": "{{app.URL}}"})

        stack.deploy()

        values = file_values(root)
        assert values["PUBLIC_URL"] == values["NOUST_URL"]
        app = store.get_app(DOMAIN)
        assert app is not None
        assert read_environment_of(app).expressions == {"PUBLIC_URL": "{{app.URL}}"}

    def test_a_reference_that_resolves_to_nothing_stops_the_deploy_before_the_build(
        self, stack: DockerComposeDeployer, root: Path, runner: FakeRunner
    ) -> None:
        reconfigure(stack, root, env_vars={"X": "{{server.MISSING}}"})

        with pytest.raises(Exception, match="MISSING"):
            stack.deploy()

        assert not any("build" in call for call in runner.calls)

    def test_a_rotated_value_reaches_the_file_at_the_next_update(
        self,
        stack: DockerComposeDeployer,
        root: Path,
        store: NoustStore,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "TOKEN", "v1")
        reconfigure(stack, root, env_vars={"API_TOKEN": "{{server.TOKEN}}"})
        stack.deploy()
        assert file_values(root)["API_TOKEN"] == "v1"
        variables.set(SERVER, 0, "TOKEN", "v2")
        monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: True)
        monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)
        updater = DockerComposeDeployer(runner=runner)
        updater.configure(DOMAIN, "https://example.com/proggest.git", app_path=root)

        updater.update()

        assert file_values(root)["API_TOKEN"] == "v2"
        assert "build" in " ".join(runner.calls[-30:][0]) or any(
            "build" in call for call in runner.calls
        )

    def test_an_update_stops_when_a_reference_no_longer_resolves(
        self,
        stack: DockerComposeDeployer,
        root: Path,
        store: NoustStore,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "TOKEN", "v1")
        reconfigure(stack, root, env_vars={"API_TOKEN": "{{server.TOKEN}}"})
        stack.deploy()
        variables.delete(SERVER, 0, "TOKEN")
        runner.calls.clear()
        updater = DockerComposeDeployer(runner=runner)
        updater.configure(DOMAIN, "https://example.com/proggest.git", app_path=root)

        with pytest.raises((DeploymentError, Exception), match="TOKEN"):
            updater.update()

        assert not any("build" in call for call in runner.calls)
