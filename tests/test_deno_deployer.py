"""
Deno programs deployed natively (checklist A-40).

A repository with a ``deno.json`` used to be only ever a container. The tests pin the decisions
that are easy to get wrong silently: which repositories are programs and which are libraries,
what is cached and where, what the unit runs and with what, and that a Dockerfile keeps a
repository a container.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import FakeRunner
from noust.deployers.deno import (
    DenoDeployer,
    entry_point,
    installed_deno,
    is_library,
    read_config,
    start_task,
    task_entry_points,
    tasks_of,
)
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.registry import DeployerRegistry, detect_app_type

DOMAIN = "edge.example.com"


def write(root: Path, name: str, text: str = "") -> Path:
    """
    Args:
        root: The project.
        name: A path inside it.
        text: Its content.

    Returns:
        The file.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def project(root: Path, config: dict | None = None, *, entry: str | None = "main.ts") -> Path:
    """
    Args:
        root: Where to make the project.
        config: The ``deno.json``; a server with a start task when None.
        entry: An entry point to create; None for none.

    Returns:
        ``root``.
    """
    document = config if config is not None else {"tasks": {"start": "deno run -A main.ts"}}
    write(root, "deno.json", json.dumps(document))
    if entry:
        write(root, entry, "Deno.serve(() => new Response('ok'))\n")
    return root


def deployer_for(root: Path, runner: FakeRunner | None = None) -> DenoDeployer:
    """
    Args:
        root: The project.
        runner: What runs its commands.

    Returns:
        A configured deployer.
    """
    instance = DenoDeployer(runner=runner or FakeRunner())
    instance.configure(DOMAIN, "src", app_path=root)
    return instance


class TestTheConfiguration:
    def test_jsonc_is_read_with_its_comments(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "deno.jsonc",
            '{\n  // serve it\n  "tasks": {"serve": "deno run main.ts",},\n}',
        )
        assert tasks_of(read_config(tmp_path) or {}) == {"serve": "deno run main.ts"}

    def test_deno_json_wins_over_deno_jsonc(self, tmp_path: Path) -> None:
        write(tmp_path, "deno.json", '{"name": "a"}')
        write(tmp_path, "deno.jsonc", '{"name": "b"}')
        assert (read_config(tmp_path) or {})["name"] == "a"

    @pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "42"])
    def test_what_is_not_an_object_is_no_configuration(self, tmp_path: Path, text: str) -> None:
        write(tmp_path, "deno.json", text)
        assert read_config(tmp_path) is None

    def test_a_link_is_not_followed(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
        outside.write_text('{"tasks": {"start": "x"}}')
        (tmp_path / "deno.json").symlink_to(outside)
        assert read_config(tmp_path) is None

    def test_a_task_may_be_an_object(self) -> None:
        config = {"tasks": {"start": {"command": "deno run main.ts"}, "x": 3, "y": {"a": 1}}}
        assert tasks_of(config) == {"start": "deno run main.ts"}

    def test_start_is_preferred_to_serve(self) -> None:
        assert start_task({"tasks": {"serve": "a", "start": "b"}}) == "start"
        assert start_task({"tasks": {"serve": "a"}}) == "serve"
        assert start_task({"tasks": {"dev": "a"}}) is None

    def test_the_files_a_task_runs_are_read_not_executed(self) -> None:
        assert task_entry_points("deno run -A --watch main.ts") == ["main.ts"]
        assert task_entry_points("deno run --allow-net src/server.tsx extra.js") == [
            "src/server.tsx",
            "extra.js",
        ]
        assert task_entry_points("deno test") == []
        assert task_entry_points("deno run ../escape.ts") == []
        assert task_entry_points("deno run 'unterminated") == []

    def test_the_version_deno_prints(self) -> None:
        assert installed_deno("deno 2.1.4 (stable, release, x86_64-unknown-linux-gnu)\nv8 13") == (
            2,
            1,
        )
        assert installed_deno("deno 1.46.3 (release)") == (1, 46)
        assert installed_deno("nonsense") is None


class TestDetection:
    def test_it_sits_between_python_and_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("deno") < order.index("nodejs")

    def test_a_server_with_a_start_task(self, tmp_path: Path) -> None:
        project(tmp_path, entry=None)
        assert DenoDeployer().detect(tmp_path) is True
        assert detect_app_type(tmp_path) == "deno"

    def test_an_entry_point_without_a_task(self, tmp_path: Path) -> None:
        project(tmp_path, {"imports": {}}, entry="server.ts")
        assert DenoDeployer().detect(tmp_path) is True

    def test_a_published_library_has_nothing_to_run(self, tmp_path: Path) -> None:
        project(tmp_path, {"name": "@acme/lib", "version": "1.0.0", "exports": "./mod.ts"})
        write(tmp_path, "main.ts")
        assert DenoDeployer().detect(tmp_path) is False

    def test_a_library_with_a_start_task_is_a_program(self) -> None:
        config = {"name": "@acme/app", "exports": "./mod.ts", "tasks": {"start": "deno run x.ts"}}
        assert is_library(config) is False

    def test_a_configuration_with_no_program(self, tmp_path: Path) -> None:
        project(tmp_path, {"fmt": {"semiColons": False}}, entry=None)
        assert DenoDeployer().detect(tmp_path) is False

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        project(tmp_path)
        write(tmp_path, "Dockerfile", "FROM denoland/deno\n")
        assert DenoDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"

    def test_an_entry_point_that_is_a_link_is_not_one(self, tmp_path: Path) -> None:
        write(tmp_path, "deno.json", "{}")
        outside = tmp_path.parent / f"{tmp_path.name}-main.ts"
        outside.write_text("x")
        (tmp_path / "main.ts").symlink_to(outside)
        assert entry_point(tmp_path) is None


class TestCommands:
    def test_install_fetches_what_the_start_task_runs(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path))
        assert deployer.get_install_command() == ["deno", "install", "--entrypoint", "main.ts"]

    def test_install_of_a_task_that_runs_no_file_is_plain(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path, {"tasks": {"start": "deno task serve"}}))
        assert deployer.get_install_command() == ["deno", "install"]

    def test_install_fetches_the_entry_point_when_there_is_no_task(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path, {"imports": {}}, entry="src/main.ts"))
        assert deployer.get_install_command() == ["deno", "install", "--entrypoint", "src/main.ts"]

    def test_a_build_task_is_the_build(self, tmp_path: Path) -> None:
        config = {"tasks": {"start": "deno run -A main.ts", "build": "deno run -A dev.ts build"}}
        deployer = deployer_for(project(tmp_path, config))
        assert deployer.get_build_command() == ["deno", "task", "build"]

    def test_without_one_there_is_nothing_to_build(self, tmp_path: Path) -> None:
        assert deployer_for(project(tmp_path)).get_build_command() == []

    def test_the_unit_runs_the_start_task(self, tmp_path: Path) -> None:
        assert deployer_for(project(tmp_path)).get_start_command() == "deno task start"

    def test_the_unit_runs_an_entry_point_with_the_permissions_a_server_needs(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(project(tmp_path, {"imports": {}}, entry="server.ts"))
        assert deployer.get_start_command() == (
            "deno run --allow-net --allow-read --allow-env server.ts"
        )

    def test_the_build_block_replaces_the_start_command(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path))
        write(tmp_path, "noust.yaml", "build:\n  start: [deno, run, -A, other.ts]\n")
        deployer._build_block = None
        assert deployer.build_config().start == ("deno", "run", "-A", "other.ts")

    def test_runtime_is_not_a_key_of_a_deno_application(self, tmp_path: Path) -> None:
        project(tmp_path)
        write(tmp_path, "noust.yaml", "build:\n  runtime: node@22\n")
        deployer = deployer_for(tmp_path)
        with pytest.raises(ValidationError, match=r"build\.runtime"):
            deployer.build_config()

    def test_the_unit_gets_the_port_the_loopback_address_and_the_releases_cache(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(project(tmp_path))
        environment = deployer._unit_environment()
        assert environment["PORT"] == str(deployer.port)
        assert environment["HOST"] == "127.0.0.1"
        assert environment["DENO_DIR"] == str(tmp_path / ".deno")
        assert environment["DENO_NO_UPDATE_CHECK"] == "1"

    def test_on_releases_the_cache_is_read_through_current(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path))
        deployer._layout = "releases"
        release = deployer.app_path / "releases" / "20261001"
        release.mkdir(parents=True)
        deployer._staged = type("Staged", (), {"path": release})()  # type: ignore[assignment]
        assert deployer._unit_environment()["DENO_DIR"] == str(
            deployer.app_path / "current" / ".deno"
        )


class TestTheCache:
    def test_every_command_of_a_build_caches_in_the_release(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(project(tmp_path), runner)

        deployer._run(["deno", "install"], phase=BuildPhase.INSTALL)
        deployer._run(["deno", "task", "build"], phase=BuildPhase.BUILD)

        for env in runner.envs:
            assert (env or {})["DENO_DIR"] == str(tmp_path / ".deno")

    def test_what_the_operator_gave_wins(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(project(tmp_path), runner)

        deployer._run(["deno", "install"], env={"DENO_DIR": "/elsewhere"}, phase=BuildPhase.INSTALL)

        assert (runner.envs[-1] or {})["DENO_DIR"] == "/elsewhere"


class TestInstall:
    def test_the_version_is_checked_and_the_dependencies_cached(self, tmp_path: Path) -> None:
        runner = FakeRunner().script(["deno", "--version"], stdout="deno 2.1.4 (stable)")
        deployer = deployer_for(project(tmp_path), runner)

        deployer.install_dependencies()

        assert runner.calls_to("deno") == [
            ("deno", "--version"),
            ("deno", "install", "--entrypoint", "main.ts"),
        ]

    def test_deno_one_cannot_install_and_is_told_to_upgrade(self, tmp_path: Path) -> None:
        runner = FakeRunner().script(["deno", "--version"], stdout="deno 1.46.3 (release)")
        deployer = deployer_for(project(tmp_path), runner)

        with pytest.raises(DeploymentError) as caught:
            deployer.install_dependencies()

        assert "Deno 1.46 and Noust needs Deno 2" in str(caught.value)
        assert runner.calls_to("deno") == [("deno", "--version")]

    def test_a_project_with_no_program_is_refused_with_the_way_out(self, tmp_path: Path) -> None:
        write(tmp_path, "deno.json", "{}")
        deployer = deployer_for(tmp_path, FakeRunner().script(["deno"], stdout="deno 2.0.0"))
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "build.start" in (caught.value.details or "")

    def test_a_server_without_deno_is_told_where_it_comes_from(self, tmp_path: Path) -> None:
        deployer = deployer_for(project(tmp_path))
        line = deployer._missing_dependencies_issue(["deno"])
        assert line.startswith("Missing system dependencies: deno. Install Deno 2")

    def test_inspect_suggests_the_same_on_any_distribution(self) -> None:
        suggestion = DenoDeployer.install_suggestion(FakeRunner().only_knows("dnf"))
        assert suggestion is not None
        assert "Deno 2" in suggestion
