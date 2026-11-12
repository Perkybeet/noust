# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust create --run-as server|container`` (item 189).

The card of the new-application wizard that says "In a container" and this option do the same: they take
the way the decision of :mod:`noust.deployers.how_it_runs` says that form deploys as. ``--type auto``
resolves to it after the source is fetched; ``--type`` and ``--run-as`` must not say different things;
and without ``--run-as`` the command says, once, the other way when there is one, which it never did.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.central.machine import MachineFacts
from noust.cli.commands import webapp
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.deployers.auto import AutoDeployer
from tests.support.cli_webapp import DeployerSpy, cli_runner, deployer  # noqa: F401

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811

NODE = json.dumps({"name": "shop", "scripts": {"start": "node server.js"}})
GO_MAIN = "package main\n\nfunc main() {}\n"


class Source:
    """Stands in for the source manager: it copies a local tree instead of cloning."""

    def __init__(self, tree: Path) -> None:
        self.tree = tree

    def fetch(self, source: str, destination: Path, branch: str | None = None, **_: Any) -> bool:
        Path(destination).mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.tree, destination, dirs_exist_ok=True)
        return True


class Said:
    """Records what the deployer reports, in the places a person reads it."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def substep(self, message: str) -> None:
        self.lines.append(message)

    warning = info = debug = substep


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def an_ordinary_server(store: NoustStore) -> None:
    """Docker and git, and no Railpack."""
    set_runner(FakeRunner().only_knows("git", "docker", "node", "npm", "python3", "go"))


def repository(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "source"
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def auto(tmp_path: Path, files: dict[str, str], **options: Any) -> AutoDeployer:
    """
    An automatic deployer over a repository, configured the way ``noust create`` configures it.

    Args:
        tmp_path: The test's directory.
        files: The repository.
        **options: What ``create`` passes besides the domain and the source, such as ``run_as``.

    Returns:
        The deployer, ready to resolve.
    """
    deployer = AutoDeployer()
    tree = repository(tmp_path, files)
    deployer.configure("shop.example.com", str(tree), app_path=tmp_path / "app", **options)
    deployer.source_manager = Source(tree)  # type: ignore[assignment]
    deployer.logger = Said()  # type: ignore[assignment]
    return deployer


class TestAutoResolvesToTheFormAskedFor:
    def test_node_with_a_dockerfile_runs_in_a_container_when_asked(self, tmp_path: Path) -> None:
        delegate = auto(
            tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"}, run_as="container"
        ).resolve()

        assert delegate.APP_TYPE == "container"

    def test_go_with_a_dockerfile_runs_directly_when_asked(self, tmp_path: Path) -> None:
        delegate = auto(
            tmp_path,
            {"go.mod": "module shop\n\ngo 1.22\n", "main.go": GO_MAIN, "Dockerfile": "FROM go\n"},
            run_as="server",
        ).resolve()

        assert delegate.APP_TYPE == "go"

    def test_what_is_proposed_is_what_runs_without_the_option(self, tmp_path: Path) -> None:
        node = auto(tmp_path / "n", {"package.json": NODE, "Dockerfile": "FROM node\n"}).resolve()
        go = auto(
            tmp_path / "g",
            {"go.mod": "module shop\n\ngo 1.22\n", "main.go": GO_MAIN, "Dockerfile": "FROM go\n"},
        ).resolve()

        assert (node.APP_TYPE, go.APP_TYPE) == ("nodejs", "container")

    def test_the_form_asked_for_is_not_passed_on_to_the_deployer_it_chooses(
        self, tmp_path: Path
    ) -> None:
        delegate = auto(
            tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"}, run_as="container"
        ).resolve()

        assert not hasattr(delegate, "run_as")

    def test_a_container_that_cannot_be_built_says_what_is_missing(self, tmp_path: Path) -> None:
        deployer = auto(tmp_path, {"requirements.txt": "flask\n"}, run_as="container")

        with pytest.raises(DeploymentError) as refused:
            deployer.resolve()

        assert "Dockerfile" in refused.value.message
        assert "noust setup railpack" in (refused.value.details or "")

    def test_railpack_builds_a_container_without_a_dockerfile(self, tmp_path: Path) -> None:
        set_runner(FakeRunner().only_knows("git", "docker", "python3", "railpack"))

        delegate = auto(tmp_path, {"requirements.txt": "flask\n"}, run_as="container").resolve()

        assert delegate.APP_TYPE == "container"

    def test_running_directly_what_only_runs_in_a_container_is_refused(
        self, tmp_path: Path
    ) -> None:
        deployer = auto(tmp_path, {"Dockerfile": "FROM scratch\n"}, run_as="server")

        with pytest.raises(DeploymentError) as refused:
            deployer.resolve()

        assert "directly on the server" in refused.value.message

    def test_a_server_without_docker_refuses_a_container_with_the_doctors_words(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.server_text import say

        problem = say("docker.engine.missing", command="noust setup init")
        facts = MachineFacts(
            systemd=True,
            web_server=True,
            docker=False,
            root=True,
            journal=True,
            docker_problem=problem,
        )
        monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: facts)
        deployer = auto(
            tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"}, run_as="container"
        )

        with pytest.raises(DeploymentError) as refused:
            deployer.resolve()

        assert refused.value.message == problem.text

    def test_a_refusal_leaves_no_application_directory_behind(self, tmp_path: Path) -> None:
        deployer = auto(tmp_path, {"requirements.txt": "flask\n"}, run_as="container")

        with pytest.raises(DeploymentError):
            deployer.resolve()

        assert not (tmp_path / "app").exists()


class TestWithoutTheOptionTheOtherWayIsSaid:
    def test_a_repository_that_could_run_either_way_says_so(self, tmp_path: Path) -> None:
        deployer = auto(tmp_path, {"package.json": NODE, "Dockerfile": "FROM node\n"})

        deployer.resolve()

        said = " ".join(deployer.logger.lines)  # type: ignore[attr-defined]
        assert "--run-as container" in said

    def test_a_repository_that_only_runs_one_way_says_nothing_of_the_other(
        self, tmp_path: Path
    ) -> None:
        deployer = auto(tmp_path, {"requirements.txt": "flask\n"})

        deployer.resolve()

        assert "--run-as" not in " ".join(deployer.logger.lines)  # type: ignore[attr-defined]

    def test_a_source_nothing_can_be_built_into_a_container_from_does_not_ask_docker_anything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A create that never needed Docker does not start probing it to say nothing."""

        def never(*args: object, **kwargs: object) -> MachineFacts:
            raise AssertionError("the machine was asked about Docker")

        monkeypatch.setattr("noust.central.machine.read_machine_facts", never)
        deployer = auto(tmp_path, {"requirements.txt": "flask\n"})

        assert deployer.resolve().APP_TYPE == "python"

    def test_a_container_proposed_says_how_to_run_it_directly(self, tmp_path: Path) -> None:
        deployer = auto(
            tmp_path,
            {"go.mod": "module shop\n\ngo 1.22\n", "main.go": GO_MAIN, "Dockerfile": "FROM go\n"},
        )

        deployer.resolve()

        assert "--run-as server" in " ".join(deployer.logger.lines)  # type: ignore[attr-defined]


def create(cli_runner: CliRunner, *arguments: str) -> Any:
    return cli_runner.invoke(
        webapp.cli.commands["create"],
        ["-d", "example.com", "-s", "https://github.com/user/repo", "-p", "3100", *arguments],
    )


class TestTheCommand:
    def test_it_is_in_the_help_with_its_two_forms(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(webapp.cli.commands["create"], ["--help"])

        assert "--run-as" in result.output
        assert "server" in result.output and "container" in result.output

    def test_the_form_reaches_the_automatic_deployer(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "--run-as", "container")

        assert result.exit_code == 0, result.output
        assert deployer.configured["run_as"] == "container"

    def test_without_it_the_deployer_is_configured_as_it_always_was(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner)

        assert result.exit_code == 0, result.output
        assert "run_as" not in deployer.configured

    def test_a_type_of_the_same_form_agrees_and_is_not_passed_on(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "-t", "go", "--run-as", "server")

        assert result.exit_code == 0, result.output
        assert "run_as" not in deployer.configured

    def test_a_native_type_cannot_be_asked_to_run_in_a_container(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "-t", "nextjs", "--run-as", "container")

        assert result.exit_code != 0
        assert "contradicts --type nextjs" in result.output
        assert deployer.deployed is False

    def test_a_container_type_cannot_be_asked_to_run_directly(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "-t", "container", "--run-as", "server")

        assert result.exit_code != 0
        assert "contradicts --type container" in result.output

    def test_the_options_of_a_container_cannot_be_asked_to_run_directly(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "--builder", "railpack", "--run-as", "server")

        assert result.exit_code != 0
        assert "--run-as server" in result.output

    def test_a_recipe_brings_its_own_way_to_run(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = cli_runner.invoke(
            webapp.cli.commands["create"],
            ["-d", "example.com", "--recipe", "ghost", "--run-as", "container"],
        )

        assert result.exit_code != 0
        assert "--recipe" in result.output

    def test_an_unknown_form_is_refused_by_the_parser(
        self, cli_runner: CliRunner, deployer: DeployerSpy
    ) -> None:
        result = create(cli_runner, "--run-as", "vm")

        assert result.exit_code != 0
        assert "server" in result.output and "container" in result.output

    def test_the_readiness_check_is_made_for_what_will_run(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, deployer: DeployerSpy
    ) -> None:
        asked: list[str] = []
        monkeypatch.setattr(
            webapp,
            "check_deployment_ready",
            lambda app_type, package_manager, verbose: asked.append(app_type) or (True, [], []),
        )

        create(cli_runner, "--run-as", "container")
        create(cli_runner, "--run-as", "server")

        # A container needs Docker; what runs directly was always checked as Node.js until detected.
        assert asked == ["container", "nodejs"]
