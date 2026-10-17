"""
Elixir and Phoenix applications deployed natively as a Mix release (checklist A-40).

A repository with a ``mix.exs`` used to be only ever a container. The tests pin what a Mix
deployment turns on: which projects are programs, that the release is built as production
whatever ``.env`` says, how Hex and the asset tools are set up, which release runs, and what
``.env`` is given once and never again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.deployers.elixir import (
    BUILD_ENVIRONMENT,
    ElixirDeployer,
    app_name,
    choose_release,
    has_application,
    installed_elixir,
    is_phoenix,
    release_names,
    requested_elixir,
)
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.registry import DeployerRegistry, detect_app_type

DOMAIN = "shop.example.com"

PHOENIX = """\
defmodule Shop.MixProject do
  use Mix.Project

  def project do
    [
      app: :shop,
      version: "0.1.0",
      elixir: "~> 1.15",
      aliases: aliases(),
      deps: deps()
    ]
  end

  def application, do: [mod: {Shop.Application, []}, extra_applications: [:logger]]

  defp deps, do: [{:phoenix, "~> 1.7"}, {:ecto_sql, "~> 3.10"}]

  defp aliases do
    [
      "assets.setup": ["tailwind.install --if-missing", "esbuild.install --if-missing"],
      "assets.deploy": ["tailwind shop --minify", "esbuild shop --minify", "phx.digest"]
    ]
  end
end
"""

LIBRARY = """\
defmodule Slug.MixProject do
  use Mix.Project
  def project, do: [app: :slug, version: "0.1.0", deps: []]
  def application, do: [extra_applications: [:logger]]
end
"""

OTP = """\
defmodule Worker.MixProject do
  use Mix.Project
  def project, do: [app: :worker, version: "1.0.0", releases: [worker: [include_executables_for: [:unix]]]]
  def application, do: [extra_applications: [:logger]]
end
"""


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


def phoenix(root: Path, mix: str = PHOENIX) -> Path:
    """
    Args:
        root: Where to make the project.
        mix: Its ``mix.exs``.

    Returns:
        ``root``.
    """
    write(root, "mix.exs", mix)
    write(root, "lib/shop.ex", "defmodule Shop do\nend\n")
    return root


def deployer_for(root: Path, runner: FakeRunner | None = None) -> ElixirDeployer:
    """
    Args:
        root: The project.
        runner: What runs its commands.

    Returns:
        A configured deployer.
    """
    instance = ElixirDeployer(runner=runner or FakeRunner())
    instance.configure(DOMAIN, "src", app_path=root)
    return instance


def built(root: Path, *names: str) -> None:
    """
    Args:
        root: The project.
        *names: Releases to leave in ``_build/prod/rel``, each with its start script.
    """
    for name in names:
        write(root, f"_build/prod/rel/{name}/bin/{name}", "#!/bin/sh\n")


class TestReadingMixExs:
    def test_phoenix_and_the_application_are_found(self) -> None:
        assert is_phoenix(PHOENIX) is True
        assert app_name(PHOENIX) == "shop"
        assert has_application(PHOENIX) is True

    def test_a_library_is_not_a_program(self) -> None:
        assert has_application(LIBRARY) is False

    def test_a_release_block_makes_it_a_program(self) -> None:
        assert has_application(OTP) is True
        assert release_names(OTP) == ["worker"]

    def test_the_applications_own_name_follows_the_releases(self) -> None:
        text = "def project, do: [app: :shop, releases: [web: [], jobs: []]]"
        assert release_names(text) == ["web", "jobs", "shop"]

    def test_only_the_keys_of_the_first_level_are_releases(self) -> None:
        text = (
            "def project, do: [releases: [web: [applications: [shop: :permanent], "
            'cookie: "a: b"], jobs: [steps: [:assemble, :tar]]]]'
        )
        assert release_names(text) == ["web", "jobs"]

    def test_a_releases_function_is_not_read_and_the_applications_name_stands(self) -> None:
        assert release_names("def project, do: [app: :shop, releases: releases()]") == ["shop"]

    def test_the_elixir_it_asks_for(self) -> None:
        assert requested_elixir(PHOENIX) == (1, 15)
        assert requested_elixir("elixir: '>= 1.14.0'") == (1, 14)
        assert requested_elixir(LIBRARY) is None

    def test_the_elixir_the_server_has(self) -> None:
        assert installed_elixir(
            "Erlang/OTP 26 [erts-14.2]\n\nElixir 1.16.1 (compiled with OTP 26)"
        ) == (
            1,
            16,
        )
        assert installed_elixir("nonsense") is None


class TestChoosingTheRelease:
    def test_the_only_release_is_the_one(self) -> None:
        assert choose_release(["shop"], hints=()) == "shop"

    def test_a_hint_decides_among_several(self) -> None:
        assert choose_release(["jobs", "web"], hints=["nothing", "web"]) == "web"

    def test_several_and_no_hint_is_refused(self) -> None:
        with pytest.raises(DeploymentError) as caught:
            choose_release(["jobs", "web"], hints=["other"])
        assert "jobs, web" in str(caught.value)
        assert "build.start" in (caught.value.details or "")

    def test_none_is_refused_and_says_where_to_look(self) -> None:
        with pytest.raises(DeploymentError) as caught:
            choose_release([], hints=())
        assert "_build/prod/rel" in (caught.value.details or "")


class TestDetection:
    def test_it_sits_between_python_and_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("elixir") < order.index("nodejs")

    def test_a_phoenix_application(self, tmp_path: Path) -> None:
        phoenix(tmp_path)
        assert ElixirDeployer().detect(tmp_path) is True
        assert detect_app_type(tmp_path) == "elixir"

    def test_an_otp_application_with_a_release(self, tmp_path: Path) -> None:
        phoenix(tmp_path, OTP)
        assert ElixirDeployer().detect(tmp_path) is True

    def test_a_library_has_nothing_to_run(self, tmp_path: Path) -> None:
        phoenix(tmp_path, LIBRARY)
        assert ElixirDeployer().detect(tmp_path) is False

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        phoenix(tmp_path)
        write(tmp_path, "Dockerfile", "FROM elixir:1.16\n")
        assert ElixirDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"

    def test_a_mix_exs_that_is_a_link_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-mix.exs"
        outside.write_text(PHOENIX)
        (tmp_path / "mix.exs").symlink_to(outside)
        assert ElixirDeployer().detect(tmp_path) is False


class TestCommands:
    def test_the_dependencies_are_the_production_ones(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer.get_install_command() == ["mix", "deps.get", "--only", "prod"]

    def test_the_build_is_the_release(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer.get_build_command() == ["mix", "release", "--overwrite"]

    def test_the_compilation_and_the_assets_come_before_it(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer._before_release() == [["mix", "compile"], ["mix", "assets.deploy"]]

    def test_an_application_without_the_alias_has_no_assets_to_build(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path, OTP))
        assert deployer._before_release() == [["mix", "compile"]]

    def test_a_build_block_replaces_the_whole_build(self, tmp_path: Path) -> None:
        phoenix(tmp_path)
        write(tmp_path, "noust.yaml", "build:\n  build: [mix, release, web]\n")
        deployer = deployer_for(tmp_path)
        assert deployer.build_command() == ["mix", "release", "web"]
        assert deployer._before_release() == []

    def test_the_unit_starts_the_release_script_through_current(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer.get_start_command() == f"{tmp_path}/_build/prod/rel/shop/bin/shop start"

    def test_the_release_that_was_built_is_the_one_started(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path, OTP))
        built(tmp_path, "worker")
        deployer.post_build()
        assert deployer.release_name == "worker"
        assert deployer.get_start_command().endswith("rel/worker/bin/worker start")

    def test_the_unit_gets_the_port_and_the_loopback_address(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer._unit_environment() == {"PORT": str(deployer.port), "HOST": "127.0.0.1"}


class TestTheBuildEnvironment:
    def test_install_and_build_are_production_whatever_env_says(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(phoenix(tmp_path), runner)
        deployer.env_vars = {}

        deployer._run(["mix", "deps.get"], phase=BuildPhase.INSTALL)
        deployer._run(["mix", "release"], phase=BuildPhase.BUILD)

        for env in runner.envs:
            assert dict(env or {}) == BUILD_ENVIRONMENT

    def test_a_value_given_at_creation_is_the_operators_to_decide(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(phoenix(tmp_path), runner)
        deployer.env_vars = {"MIX_ENV": "staging"}

        deployer._run(["mix", "release"], phase=BuildPhase.BUILD)

        assert (runner.envs[-1] or {})["MIX_ENV"] == "staging"

    def test_a_hook_is_not_given_the_build_environment(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(phoenix(tmp_path), runner)

        deployer._run(["bin/shop", "eval", "Shop.Release.migrate"], phase=BuildPhase.RELEASE)

        assert "MIX_ENV" not in (runner.envs[-1] or {})


class TestInstall:
    def test_hex_and_rebar_are_installed_for_the_application_before_the_dependencies(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner().script(["elixir", "--version"], stdout="Elixir 1.16.1")
        deployer = deployer_for(phoenix(tmp_path), runner)

        deployer.install_dependencies()

        assert runner.calls_to("mix") == [
            ("mix", "local.hex", "--force"),
            ("mix", "local.rebar", "--force"),
            ("mix", "deps.get", "--only", "prod"),
            ("mix", "assets.setup"),
        ]

    def test_a_hex_that_cannot_be_installed_says_the_network_is_needed(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner().script(["mix", "local.hex"], stderr="timeout", exit_code=1)
        deployer = deployer_for(phoenix(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "timeout" in (caught.value.details or "")
        assert "network" in (caught.value.details or "")

    def test_an_elixir_older_than_the_projects_is_a_warning_not_a_refusal(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner().script(["elixir", "--version"], stdout="Elixir 1.13.4")
        deployer = deployer_for(phoenix(tmp_path), runner)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        assert deployer.pre_install() is True

        assert any("asks for Elixir 1.15 and this server has 1.13" in line for line in lines)

    def test_assets_are_set_up_only_when_the_project_has_the_alias(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(phoenix(tmp_path, OTP), runner)
        deployer.post_install()
        assert runner.calls_to("mix") == []

    def test_a_failed_asset_setup_stops_with_mixs_own_words(self, tmp_path: Path) -> None:
        runner = FakeRunner().script(["mix", "assets.setup"], stderr="download failed", exit_code=1)
        deployer = deployer_for(phoenix(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.post_install()
        assert "download failed" in (caught.value.details or "")

    def test_migrations_are_said_to_be_a_decision(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        deployer.post_install()

        assert any("migrations are not run for you" in line for line in lines)


class TestBuild:
    def test_the_compilation_and_the_assets_run_in_order(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(phoenix(tmp_path), runner)

        deployer.pre_build()

        assert runner.calls_to("mix") == [("mix", "compile"), ("mix", "assets.deploy")]

    def test_a_compilation_that_fails_stops_with_mixs_own_words(self, tmp_path: Path) -> None:
        runner = FakeRunner().script(["mix", "compile"], stderr="** (CompileError)", exit_code=1)
        deployer = deployer_for(phoenix(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_build()
        assert "CompileError" in (caught.value.details or "")

    def test_a_dry_run_builds_nothing_and_finds_nothing(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        assert deployer.post_build() is True
        assert deployer.release_name is None

    def test_a_build_that_left_no_release_says_so(self, tmp_path: Path) -> None:
        (tmp_path / "_build/prod/rel").mkdir(parents=True)
        deployer = deployer_for(phoenix(tmp_path))
        with pytest.raises(DeploymentError, match="built nothing"):
            deployer.post_build()

    def test_several_releases_are_told_apart_by_the_applications_name(self, tmp_path: Path) -> None:
        built(tmp_path, "shop", "migrator")
        deployer = deployer_for(phoenix(tmp_path))
        deployer.post_build()
        assert deployer.release_name == "shop"


class TestTheEnvFile:
    def test_a_phoenix_application_gets_its_production_defaults_once(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))

        deployer._prepare_env()
        first = deployer._env_manager.read_env_file(deployer._env_file())

        assert first["PHX_SERVER"] == "true"
        assert first["RELEASE_DISTRIBUTION"] == "none"
        assert first["PHX_HOST"] == DOMAIN
        assert len(first["SECRET_KEY_BASE"]) == 128

        deployer._prepare_env()
        assert deployer._env_manager.read_env_file(deployer._env_file()) == first

    def test_an_operators_values_are_never_replaced(self, tmp_path: Path) -> None:
        phoenix(tmp_path)
        write(
            tmp_path,
            ".env",
            "SECRET_KEY_BASE=mine\nPHX_HOST=www.example.org\nRELEASE_DISTRIBUTION=name\n",
        )
        deployer = deployer_for(tmp_path)

        deployer._prepare_env()

        values = deployer._env_manager.read_env_file(deployer._env_file())
        assert values["SECRET_KEY_BASE"] == "mine"
        assert values["PHX_HOST"] == "www.example.org"
        assert values["RELEASE_DISTRIBUTION"] == "name"
        assert values["PHX_SERVER"] == "true"

    def test_an_otp_application_that_is_not_phoenix_gets_no_phoenix_values(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(phoenix(tmp_path, OTP))

        deployer._prepare_env()

        values = deployer._env_manager.read_env_file(deployer._env_file())
        assert "SECRET_KEY_BASE" not in values
        assert "PHX_HOST" not in values
        assert values["RELEASE_DISTRIBUTION"] == "none"

    def test_the_env_file_is_private(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        deployer._prepare_env()
        assert (deployer._env_file().stat().st_mode & 0o077) == 0


class TestReporting:
    def test_the_preflight_line_says_how_to_install_it(self, tmp_path: Path) -> None:
        deployer = deployer_for(phoenix(tmp_path))
        deployer._runner = FakeRunner().only_knows("apt-get")
        assert deployer._missing_dependencies_issue(["elixir"]) == (
            "Missing system dependencies: elixir. "
            "Install it with: apt install elixir erlang-dev build-essential"
        )

    def test_inspect_suggests_the_same(self) -> None:
        suggestion = ElixirDeployer.install_suggestion(FakeRunner().only_knows("dnf"))
        assert suggestion is not None
        assert "dnf install elixir erlang gcc make" in suggestion
