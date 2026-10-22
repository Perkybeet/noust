# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ruby on Rails and Rack applications (checklist A-39).

A repository with a ``Gemfile`` used to be only ever a container. The deployer installs the
bundle in the release, precompiles the assets as production and runs Puma on the loopback
address. The tests pin the decisions a Rails deployment turns on: how Bundler is configured
(in the release, never through the environment), what the build is given whatever ``.env``
says, what ``.env`` is given and what it is never given twice, and which repositories are not
claimed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.registry import DeployerRegistry, detect_app_type
from noust.deployers.ruby import (
    BUILD_ENVIRONMENT,
    RubyDeployer,
    installed_ruby,
    requested_ruby,
)

DOMAIN = "shop.example.com"
LOCK_WITH_PUMA = "GEM\n  specs:\n    puma (6.4.2)\n    rack (3.0.9)\n\nBUNDLED WITH\n   2.5.9\n"
LOCK_WITHOUT_PUMA = "GEM\n  specs:\n    rack (3.0.9)\n    webrick (1.8.1)\n"


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


def rails(root: Path, *, assets: bool = True, lock: str | None = LOCK_WITH_PUMA) -> Path:
    """
    Args:
        root: Where to make the project.
        assets: Whether it has an asset pipeline (an API-only application has none).
        lock: The ``Gemfile.lock``; None for no lock file.

    Returns:
        ``root``.
    """
    write(root, "Gemfile", "source 'https://rubygems.org'\ngem 'rails'\ngem 'puma'\n")
    write(root, "config/application.rb", "module Shop\nend\n")
    write(root, "config.ru", "run Rails.application\n")
    if assets:
        write(root, "app/assets/config/manifest.js", "")
    if lock is not None:
        write(root, "Gemfile.lock", lock)
    return root


def deployer_for(project: Path, runner: FakeRunner | None = None) -> RubyDeployer:
    """
    Args:
        project: The project.
        runner: What runs its commands.

    Returns:
        A configured deployer.
    """
    instance = RubyDeployer(runner=runner or FakeRunner())
    instance.configure(DOMAIN, "src", app_path=project)
    return instance


class TestDetection:
    def test_it_sits_between_python_and_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("ruby") < order.index("nodejs")

    def test_a_rails_application(self, tmp_path: Path) -> None:
        rails(tmp_path)
        assert RubyDeployer().detect(tmp_path) is True
        assert detect_app_type(tmp_path) == "ruby"

    def test_a_rack_application(self, tmp_path: Path) -> None:
        write(tmp_path, "Gemfile", "gem 'sinatra'\n")
        write(tmp_path, "config.ru", "run App\n")
        assert RubyDeployer().detect(tmp_path) is True

    @pytest.mark.parametrize("files", [{"Gemfile": ""}, {"Gemfile": "", "_config.yml": ""}])
    def test_a_gem_or_a_jekyll_site_has_nothing_to_serve(
        self, tmp_path: Path, files: dict[str, str]
    ) -> None:
        for name, text in files.items():
            write(tmp_path, name, text)
        assert RubyDeployer().detect(tmp_path) is False

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        rails(tmp_path)
        write(tmp_path, "Dockerfile", "FROM ruby:3.3\n")
        assert RubyDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"


class TestRubyVersions:
    def test_the_project_says_it_in_one_of_three_places(self, tmp_path: Path) -> None:
        assert requested_ruby(tmp_path) is None
        write(tmp_path, "Gemfile", "source 'x'\nruby '3.1.4'\n")
        assert requested_ruby(tmp_path) == (3, 1)
        write(tmp_path, ".tool-versions", "nodejs 22.1.0\nruby 3.2.2\n")
        assert requested_ruby(tmp_path) == (3, 2)
        write(tmp_path, ".ruby-version", "ruby-3.3.0\n")
        assert requested_ruby(tmp_path) == (3, 3)

    def test_what_ruby_version_prints(self) -> None:
        assert installed_ruby(
            "ruby 3.1.2p20 (2022-04-12 revision 4491bb740a) [x86_64-linux-gnu]"
        ) == (
            3,
            1,
        )
        assert installed_ruby("") is None


class TestCommands:
    def test_install_is_bundle_install(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path))
        assert deployer.get_install_command() == [
            "bundle",
            "install",
            "--jobs",
            "4",
            "--retry",
            "3",
        ]

    def test_assets_are_precompiled_for_an_application_that_has_them(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path))
        assert deployer.get_build_command() == ["bundle", "exec", "rails", "assets:precompile"]

    def test_an_api_only_application_has_nothing_to_build(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path, assets=False))
        assert deployer.get_build_command() == []

    def test_a_rack_application_has_nothing_to_build(self, tmp_path: Path) -> None:
        write(tmp_path, "Gemfile", "gem 'sinatra'\n")
        write(tmp_path, "config.ru", "run App\n")
        write(tmp_path, "app/assets/x.css", "")
        assert deployer_for(tmp_path).get_build_command() == []

    def test_puma_listens_on_the_loopback_address_and_the_applications_port(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(rails(tmp_path))
        assert deployer.get_start_command() == (
            f"bundle exec puma -b tcp://127.0.0.1:{deployer.port}"
        )

    def test_without_puma_it_is_rackup(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path, lock=LOCK_WITHOUT_PUMA))
        assert deployer.get_start_command() == (
            f"bundle exec rackup -o 127.0.0.1 -p {deployer.port} config.ru"
        )

    def test_without_a_lock_file_the_gemfile_decides(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path, lock=None))
        assert "puma" in deployer.get_start_command()

    def test_the_unit_gets_the_port_and_the_rails_logging_settings_not_the_environment(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(rails(tmp_path))
        assert deployer._unit_environment() == {
            "PORT": str(deployer.port),
            "RAILS_LOG_TO_STDOUT": "1",
            "RAILS_SERVE_STATIC_FILES": "1",
        }

    def test_the_build_block_replaces_the_start_command(self, tmp_path: Path) -> None:
        rails(tmp_path)
        write(
            tmp_path,
            "noust.yaml",
            "build:\n  start: [bundle, exec, falcon, serve, -b, 'http://127.0.0.1:3000']\n",
        )
        deployer = deployer_for(tmp_path)
        assert deployer.build_config().start is not None
        assert "falcon" in deployer.build_config().start


class TestTheBuildAndTheEnvironment:
    def test_a_value_given_at_creation_is_the_operators_to_decide(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(rails(tmp_path), runner)
        deployer.env_vars = {"RAILS_ENV": "staging"}

        deployer._run(["bundle", "exec", "rails", "assets:precompile"], phase=BuildPhase.BUILD)

        assert runner.envs[-1] is not None
        assert runner.envs[-1]["RAILS_ENV"] == "staging"
        assert runner.envs[-1]["SECRET_KEY_BASE_DUMMY"] == "1"

    def test_the_build_gets_the_defaults_when_nothing_says_otherwise(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = deployer_for(rails(tmp_path), runner)

        deployer._run(["true"], phase=BuildPhase.BUILD)

        assert dict(runner.envs[-1] or {}) == BUILD_ENVIRONMENT

    def test_an_install_and_a_hook_are_not_given_the_build_environment(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        deployer = deployer_for(rails(tmp_path), runner)

        deployer._run(["bundle", "install"], phase=BuildPhase.INSTALL)
        deployer._run(["bundle", "exec", "rails", "db:migrate"], phase=BuildPhase.RELEASE)

        for env in runner.envs:
            assert "SECRET_KEY_BASE_DUMMY" not in (env or {})

    def test_env_gets_production_and_a_secret_once(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path))

        deployer._prepare_env()
        first = deployer._env_manager.read_env_file(deployer._env_file())

        assert first["RAILS_ENV"] == "production"
        assert first["RACK_ENV"] == "production"
        assert len(first["SECRET_KEY_BASE"]) == 128

        deployer._prepare_env()
        assert deployer._env_manager.read_env_file(deployer._env_file()) == first

    def test_an_operators_values_are_never_replaced(self, tmp_path: Path) -> None:
        rails(tmp_path)
        write(tmp_path, ".env", "RAILS_ENV=staging\nSECRET_KEY_BASE=mine\n")
        deployer = deployer_for(tmp_path)

        deployer._prepare_env()

        values = deployer._env_manager.read_env_file(deployer._env_file())
        assert values["RAILS_ENV"] == "staging"
        assert values["SECRET_KEY_BASE"] == "mine"
        assert values["RACK_ENV"] == "production"

    def test_a_rack_application_gets_no_rails_secret(self, tmp_path: Path) -> None:
        write(tmp_path, "Gemfile", "gem 'sinatra'\n")
        write(tmp_path, "config.ru", "run App\n")
        deployer = deployer_for(tmp_path)

        deployer._prepare_env()

        assert "SECRET_KEY_BASE" not in deployer._env_manager.read_env_file(deployer._env_file())

    def test_the_env_file_is_private(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path))
        deployer._prepare_env()
        assert (deployer._env_file().stat().st_mode & 0o077) == 0


class TestInstall:
    def test_bundler_is_configured_in_the_release_before_the_bundle_is_installed(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner().script(["ruby", "--version"], stdout="ruby 3.3.1 (2024)")
        deployer = deployer_for(rails(tmp_path), runner)

        deployer.install_dependencies()

        assert runner.calls_to("bundle") == [
            ("bundle", "config", "set", "--local", "deployment", "true"),
            ("bundle", "config", "set", "--local", "without", "development:test"),
            ("bundle", "config", "set", "--local", "path", "vendor/bundle"),
            ("bundle", "install", "--jobs", "4", "--retry", "3"),
        ]

    def test_without_a_lock_file_it_is_not_a_deployment_install_and_says_so(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        deployer = deployer_for(rails(tmp_path, lock=None), runner)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        deployer.pre_install()

        assert ("bundle", "config", "set", "--local", "deployment", "true") not in runner.calls
        assert any("no Gemfile.lock" in line for line in lines)

    def test_a_bundler_that_cannot_be_configured_says_what_to_do(self, tmp_path: Path) -> None:
        runner = FakeRunner().script(
            ["bundle", "config"], stderr="Unknown command: set", exit_code=15
        )
        deployer = deployer_for(rails(tmp_path), runner)

        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()

        assert "Unknown command: set" in (caught.value.details or "")
        assert "Bundler 2.1" in (caught.value.details or "")

    def test_a_ruby_that_is_not_the_projects_is_a_warning_not_a_refusal(
        self, tmp_path: Path
    ) -> None:
        rails(tmp_path)
        write(tmp_path, ".ruby-version", "3.3.1\n")
        runner = FakeRunner().script(["ruby", "--version"], stdout="ruby 3.1.2p20 (2022)")
        deployer = deployer_for(tmp_path, runner)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        assert deployer.pre_install() is True

        assert any("asks for Ruby 3.3 and this server has 3.1" in line for line in lines)

    def test_migrations_are_said_to_be_a_decision(self, tmp_path: Path) -> None:
        rails(tmp_path)
        write(tmp_path, "db/migrate/20260101000000_create_orders.rb", "class CreateOrders; end\n")
        deployer = deployer_for(tmp_path)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        deployer.post_install()

        assert any("bundle exec rails db:migrate, migrates: true" in line for line in lines)

    def test_a_declared_hook_silences_it(self, tmp_path: Path) -> None:
        rails(tmp_path)
        write(tmp_path, "db/migrate/20260101000000_create_orders.rb", "class CreateOrders; end\n")
        write(
            tmp_path,
            "noust.yaml",
            "hooks:\n  pre_deploy:\n    - run: [bundle, exec, rails, db:migrate]\n      migrates: true\n",
        )
        deployer = deployer_for(tmp_path)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        deployer.post_install()

        assert not any("migrations are not run" in line for line in lines)


class TestMissingToolchain:
    def test_the_preflight_line_says_how_to_install_it(self, tmp_path: Path) -> None:
        deployer = deployer_for(rails(tmp_path), FakeRunner().only_knows("apt-get"))
        assert deployer._missing_dependencies_issue(["ruby", "bundle"]) == (
            "Missing system dependencies: ruby, bundle. Install it with: "
            "apt install ruby ruby-dev bundler build-essential"
        )

    def test_inspect_suggests_the_same(self) -> None:
        runner = FakeRunner().only_knows("dnf")
        assert RubyDeployer.install_suggestion(runner) == (
            "Install the toolchain with: dnf install ruby ruby-devel rubygem-bundler gcc make "
            "redhat-rpm-config. Then deploy it."
        )
