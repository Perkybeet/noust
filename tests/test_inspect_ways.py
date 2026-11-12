# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``inspect`` says of how a source can run on this server (item 189).

The owner believed Noust only deployed natively, because the type decides alone and a Dockerfile in a
Node.js, Python, PHP, Next.js or Vite repository was ignored. ``inspect`` now says the ways a source can
run (directly on the server, in a container), which one it proposes and why, and why the other cannot be
when it cannot. These are the cases of the approved mock-up, over real sources, and the API's answer.
What is proposed is what Noust has always deployed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.central.features import FEATURES, Feature, Need
from noust.central.machine import MachineFacts
from noust.core.runner import FakeRunner, set_runner
from noust.core.server_text import say
from noust.core.store import NoustStore
from noust.deployers.inspect import SourceInspection, inspect_source
from noust.web.api.app_inspection import inspection_response
from noust.web.pydantic_compat import dump_model

NODE = json.dumps({"name": "shop", "scripts": {"start": "node server.js"}})
GO_MAIN = "package main\n\nfunc main() {}\n"
CRATE = '[package]\nname = "shop"\nversion = "0.1.0"\n'

ORDINARY = MachineFacts(systemd=True, web_server=True, docker=True, root=True, journal=True)


@pytest.fixture
def store(tmp_path: Path):
    """An isolated store, installed as the process-wide one the deployers read while detecting."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def server_with_docker_and_no_railpack(store: NoustStore) -> None:
    """The ordinary server of the mock-up: Docker and git, and no Railpack."""
    set_runner(FakeRunner().only_knows("git", "docker", "node", "npm", "python3", "go"))


def source(tmp_path: Path, files: dict[str, str]) -> str:
    """
    Write a repository and say where it is.

    Args:
        tmp_path: A directory of the test's own.
        files: The repository's files by relative path.

    Returns:
        The path, which is a source ``inspect_source`` takes.
    """
    root = tmp_path / "repository"
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return str(root)


def facts(monkeypatch: pytest.MonkeyPatch, machine: MachineFacts) -> None:
    """Give the test the machine it names, in place of the ordinary one."""
    monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: machine)


class TestTheCasesOfTheMockUp:
    def test_node_with_a_dockerfile_is_proposed_directly_and_the_container_is_offered(
        self, tmp_path: Path
    ) -> None:
        result = inspect_source(
            source(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})
        )

        ways = result.ways
        assert ways is not None
        # What was deployed yesterday is proposed today: the type did not change.
        assert result.app_type == "nodejs"
        assert ways.proposed == "server"
        assert ways.choice is True
        assert ways.server.app_type == "nodejs"
        assert ways.container.app_type == "container"
        assert ways.container.via == "dockerfile"
        assert ways.why.code == "how_it_runs.why.server_dockerfile"

    def test_the_container_says_the_port_its_dockerfile_exposes(self, tmp_path: Path) -> None:
        result = inspect_source(
            source(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\nEXPOSE 8123\n"})
        )

        assert result.ways is not None
        assert result.ways.container.port == 8123
        # The type's own port is what the inspection has always said.
        assert result.default_port == 3000

    def test_node_without_a_dockerfile_cannot_run_in_a_container_here_and_says_why(
        self, tmp_path: Path
    ) -> None:
        result = inspect_source(source(tmp_path, {"package.json": NODE}))

        ways = result.ways
        assert ways is not None
        assert ways.choice is False
        assert ways.proposed == "server"
        assert ways.container.status == "no"
        assert ways.container.reason is not None
        assert ways.container.reason.code == "how_it_runs.container.no_build"
        assert ways.container.reason.hint is not None
        assert "noust setup railpack" in ways.container.reason.hint

    def test_node_without_a_dockerfile_where_railpack_is_installed_can_be_built_by_it(
        self, tmp_path: Path
    ) -> None:
        set_runner(FakeRunner().only_knows("git", "docker", "node", "npm", "railpack"))

        result = inspect_source(source(tmp_path, {"package.json": NODE}))

        assert result.ways is not None
        assert result.ways.choice is True
        assert result.ways.container.via == "railpack"
        assert result.ways.proposed == "server"

    def test_go_with_a_dockerfile_is_proposed_as_a_container_as_it_always_was(
        self, tmp_path: Path
    ) -> None:
        result = inspect_source(
            source(
                tmp_path,
                {
                    "go.mod": "module shop\n\ngo 1.22\n",
                    "main.go": GO_MAIN,
                    "Dockerfile": "FROM go\n",
                },
            )
        )

        ways = result.ways
        assert ways is not None
        assert result.app_type == "container"
        assert ways.proposed == "container"
        assert ways.why.code == "how_it_runs.why.container_dockerfile"
        # What the page never said: Go can run directly too, and --type go always did it.
        assert ways.server.status == "yes"
        assert ways.server.app_type == "go"

    def test_python_has_only_the_one_way(self, tmp_path: Path) -> None:
        result = inspect_source(source(tmp_path, {"requirements.txt": "flask\n"}))

        assert result.ways is not None
        assert result.ways.choice is False
        assert result.ways.proposed == "server"
        assert result.ways.why.code == "how_it_runs.why.server_only"

    def test_a_compose_file_runs_in_containers(self, tmp_path: Path) -> None:
        result = inspect_source(
            source(tmp_path, {"compose.yaml": "services:\n  web:\n    image: nginx\n"})
        )

        assert result.ways is not None
        assert result.app_type == "docker-compose"
        assert result.ways.choice is False
        assert result.ways.proposed == "container"
        assert result.ways.container.via == "compose"
        assert result.ways.why.code == "how_it_runs.why.compose"

    def test_what_only_railpack_builds_is_a_container(self, tmp_path: Path) -> None:
        set_runner(FakeRunner().only_knows("git", "docker", "railpack"))

        result = inspect_source(source(tmp_path, {"Cargo.toml": CRATE, "src/lib.rs": ""}))

        assert result.app_type == "container"
        assert result.ways is not None
        assert result.ways.proposed == "container"
        assert result.ways.container.via == "railpack"
        assert result.ways.server.status == "hidden"


class TestWhenTheMachineCannot:
    def test_a_server_without_docker_offers_one_way_and_says_why_not_the_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        problem = say("docker.engine.missing", command="noust setup init")
        facts(
            monkeypatch,
            MachineFacts(
                systemd=True,
                web_server=True,
                docker=False,
                root=True,
                journal=True,
                docker_problem=problem,
            ),
        )

        result = inspect_source(
            source(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})
        )

        assert result.ways is not None
        assert result.ways.choice is False
        assert result.ways.proposed == "server"
        assert result.ways.container.status == "no"
        assert result.ways.container.reason == problem

    def test_a_machine_that_only_has_docker_is_offered_containers_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # What the table says of a container application on a machine whose Docker keeps it alive
        # (the lane that deploys from a NAS takes the unit off that line): Docker is all it needs.
        monkeypatch.setitem(
            FEATURES,
            "container_deploy",
            Feature("container_deploy", "Applications", (Need.DOCKER,)),
        )
        facts(
            monkeypatch,
            MachineFacts(systemd=False, web_server=False, docker=True, root=False, journal=False),
        )

        result = inspect_source(
            source(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})
        )

        assert result.ways is not None
        assert result.ways.server.status == "hidden"
        assert result.ways.proposed == "container"
        assert result.ways.why.code == "how_it_runs.why.docker_only"

    def test_the_verdict_is_what_it_was(self, tmp_path: Path) -> None:
        """The ways are in addition to what the page has always been told."""
        result = inspect_source(
            source(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})
        )

        assert result.compatible is True
        assert result.detected_types == ["nodejs", "container"]


class TestTheApiAnswer:
    def answer(self, tmp_path: Path, files: dict[str, str]) -> dict:
        result: SourceInspection = inspect_source(source(tmp_path, files))
        return dump_model(inspection_response(result))

    def test_both_ways_travel_with_their_type_and_how_they_are_built(self, tmp_path: Path) -> None:
        body = self.answer(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})

        ways = body["ways"]
        assert ways["choice"] is True
        assert ways["proposed"] == "server"
        assert ways["server"]["status"] == "yes"
        assert ways["server"]["app_type"] == "nodejs"
        assert ways["container"]["app_type"] == "container"
        assert ways["container"]["via"] == "dockerfile"
        assert ways["container"]["port"] is None
        assert ways["server"]["port"] is None

    def test_the_proposal_and_the_reasons_travel_as_a_code_beside_their_english(
        self, tmp_path: Path
    ) -> None:
        body = self.answer(tmp_path, {"requirements.txt": "flask\n"})

        why = body["ways"]["why"]
        assert why["code"] == "how_it_runs.why.server_only"
        assert why["params"] == {"type": "Python (Django/Flask/FastAPI)"}
        assert "Python" in why["text"]
        reason = body["ways"]["container"]["reason"]
        assert reason["code"] == "how_it_runs.container.no_build"
        assert reason["params"] == {"command": "noust setup railpack"}
        assert "Dockerfile" in reason["text"]
        assert "noust setup railpack" in reason["hint"]

    def test_a_way_that_can_be_has_no_reason(self, tmp_path: Path) -> None:
        body = self.answer(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})

        assert body["ways"]["server"]["reason"] is None
        assert body["ways"]["container"]["reason"] is None

    def test_an_inspection_without_ways_answers_none(self) -> None:
        """What a test, or a server that could not read its machine, builds by hand."""
        result = SourceInspection(
            app_type="python",
            detected_types=["python"],
            package_manager=None,
            install_command=[],
            build_command=[],
            start_command="",
            default_port=8000,
            env_keys=[],
            branch="",
            commit="",
        )

        assert inspection_response(result).ways is None
